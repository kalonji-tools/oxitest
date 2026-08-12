//! Test item collection: file scanning, module import, and profiling.

use std::sync::Arc;

use camino::{Utf8Path, Utf8PathBuf};
use pyo3::prelude::*;

use crate::{bare_asserts, bridge, cache, config, filter, types};

fn file_mtime_secs(path: &camino::Utf8Path) -> u64 {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .map(|t| {
            t.duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
        })
        .unwrap_or(0)
}

/// Returns true iff any scope entry could match a subject in `rel`.
///
/// Prefix entries match by `starts_with`; File/Symbol entries match only when
/// the entry's `file` equals `rel`. Used by the Phase-1 prescreen to filter
/// the file set handed to the scanner.
pub fn file_could_match(rel: &camino::Utf8Path, entries: &[crate::config::ScopeEntry]) -> bool {
    use crate::config::ScopeEntry;
    entries.iter().any(|e| match e {
        ScopeEntry::Prefix(p) => rel.starts_with(p),
        ScopeEntry::File(f)
        | ScopeEntry::Symbol { file: f, .. }
        | ScopeEntry::Member { file: f, .. } => rel == *f,
    })
}

/// Per-file collection timing.
#[derive(Debug)]
pub struct FileProfile {
    pub(super) path: Utf8PathBuf,
    pub(super) prescan_us: u64,
    pub(super) collection_us: u64,
    /// True if this file was skipped by lazy collection (not imported).
    pub(super) lazy_skipped: bool,
}

/// Aggregate collection timing profile.
#[derive(Debug, Default)]
pub struct CollectionProfile {
    pub(super) files: Vec<FileProfile>,
    pub(super) total_us: u64,
}

impl CollectionProfile {
    pub(super) fn prescan_us(&self) -> u64 {
        self.files.iter().map(|f| f.prescan_us).sum()
    }

    pub(super) fn collection_us(&self) -> u64 {
        self.files.iter().map(|f| f.collection_us).sum()
    }
}

/// Format the collection profile for stderr output.
pub(super) fn format_collection_profile(profile: &CollectionProfile) -> String {
    use std::fmt::Write;
    let mut out = String::new();

    let total_ms = profile.total_us as f64 / 1000.0;
    let prescan_ms = profile.prescan_us() as f64 / 1000.0;
    let collection_ms = profile.collection_us() as f64 / 1000.0;
    let other_ms = total_ms - prescan_ms - collection_ms;

    let prescan_pct = if total_ms > 0.0 {
        prescan_ms / total_ms * 100.0
    } else {
        0.0
    };
    let collection_pct = if total_ms > 0.0 {
        collection_ms / total_ms * 100.0
    } else {
        0.0
    };
    let other_pct = if total_ms > 0.0 {
        other_ms / total_ms * 100.0
    } else {
        0.0
    };

    // Discarded, not unwrapped: `std::fmt::Write` on a `String` has no failing
    // sink, so a `Result` here would plumb an error that cannot occur.
    let file_count = profile.files.len();
    let _ = writeln!(
        out,
        "Collection profile ({file_count} files, {total_ms:.0}ms total):"
    );
    let _ = writeln!(out, "  prescan:    {prescan_ms:.0}ms ({prescan_pct:.1}%)");
    let _ = writeln!(
        out,
        "  collection: {collection_ms:.0}ms ({collection_pct:.1}%)"
    );
    let _ = writeln!(out, "  other:      {other_ms:.0}ms ({other_pct:.1}%)");

    let lazy_count = profile.files.iter().filter(|f| f.lazy_skipped).count();
    let eager_count = file_count - lazy_count;
    if lazy_count > 0 || eager_count < file_count {
        let _ = writeln!(
            out,
            "  lazy: {lazy_count} files skipped, eager: {eager_count} files imported"
        );
    }

    // Top 5 slowest files
    let mut sorted: Vec<&FileProfile> = profile.files.iter().collect();
    sorted.sort_by_key(|f| std::cmp::Reverse(f.prescan_us + f.collection_us));
    let has_slow = sorted
        .first()
        .is_some_and(|f| f.prescan_us + f.collection_us > 0);
    if has_slow {
        let _ = writeln!(out);
        let _ = writeln!(out, "Slowest files:");
        for fp in sorted.iter().take(5) {
            let file_ms = (fp.prescan_us + fp.collection_us) as f64 / 1000.0;
            let file_pct = if total_ms > 0.0 {
                file_ms / total_ms * 100.0
            } else {
                0.0
            };
            let _ = writeln!(out, "  {}    {file_ms:.0}ms ({file_pct:.1}%)", fp.path);
        }
    }

    out
}

/// Everything `collect_items` produces.
///
/// A named struct rather than a tuple: five positional fields is past the
/// point where call sites stay readable.
pub(super) struct CollectionOutput {
    pub items: Vec<Arc<types::TestItem>>,
    pub errors: Vec<types::CollectError>,
    pub raw_violations: Vec<bridge::RawViolation>,
    pub profile: Option<CollectionProfile>,
    /// `__fixtures__.py` files registered here, forwarded to workers (#1732).
    pub fixture_modules: Vec<types::FixtureModule>,
    /// Non-fatal diagnostics raised while registering declaration homes, drained
    /// into `SharedState::pending_diagnostics` by the `collect` transition.
    pub diagnostics: Vec<crate::reporter::stats::DiagnosticEntry>,
}

/// ADR-0009's "rootdir package" — the deepest directory containing every
/// directory the project *declares* as its test surface.
///
/// Folds `declared_testpaths`, never the collected files or `testpaths` — see
/// [`crate::config::PathConfig::declared_testpaths`] for why those two cannot
/// answer this (#1798).
///
/// Takes directories. A declared entry that names a file contributes its
/// parent, and that normalisation happens at the call site, where the
/// filesystem is already being touched — this fold stays pure path arithmetic.
///
/// **Clamped to `rootdir`** (#1921). `resolve_testpaths` joins each declared
/// entry to the rootdir, but `join` returns an already-absolute entry
/// unchanged, so a `testpaths` entry pointing outside the project drags the
/// fold above it — far enough, on disjoint filesystem trees, to answer `/`.
/// The rootdir package is the root of *this project's* declared test tree, so
/// a value outside the project is not one, and the hint that names it is
/// unactionable.
///
/// The clamp is conditional on some declared entry being **inside** `rootdir`.
/// A project whose whole test surface sits outside itself
/// (`testpaths = ["/elsewhere/suite"]`) has its rootdir package out there too,
/// and clamping it to the project root would reject the declaration beside its
/// own tests while pointing the user at a directory holding none — the very
/// shape [`declared_dirs_holding_tests`] exists to avoid. Only a *mixture*
/// widens to the project root, because only then is there an inside surface for
/// the outside entry to drag the fold off.
///
/// Not reached: two absolute entries on disjoint trees that are *both* outside
/// the project still fold to `/`. Narrower than the case above and left as a
/// documented limitation rather than a third rule.
///
/// Returns `None` only when nothing is declared, in which case there is no tree
/// and no declaration to place inside it.
fn rootdir_package(
    declared_dirs: &[camino::Utf8PathBuf],
    rootdir: &camino::Utf8Path,
) -> Option<camino::Utf8PathBuf> {
    let mut dirs = declared_dirs.iter();
    let first = dirs.next()?;
    let folded = dirs.fold(first.to_owned(), |common, dir| {
        common
            .ancestors()
            .find(|candidate| dir.starts_with(candidate))
            .unwrap_or(camino::Utf8Path::new(""))
            .to_owned()
    });
    let any_inside = declared_dirs.iter().any(|dir| dir.starts_with(rootdir));
    Some(if any_inside && !folded.starts_with(rootdir) {
        rootdir.to_owned()
    } else {
        folded
    })
}

/// The test surface a project implies by its layout, for use when it declares
/// none.
///
/// Why an undeclared project is not simply given its rootdir:
/// [`crate::config::PathConfig::declared_testpaths`].
///
/// Walks from `cfg.rootdir` rather than from `cfg.paths.testpaths`, so a
/// positional path argument cannot move the answer — the invocation
/// independence this issue buys has to hold for the undeclared case too.
///
/// **Not** gated on `has_explicit_paths`: that flag is also set by
/// `merge_affected`, which narrows the *item set* without touching the walk
/// (#1796). It answers a different question than "was the walk narrowed".
///
/// A walk that finds nothing yields no directories, which folds to `None` — the
/// same answer as an empty declared list, and the honest one.
fn implied_declared_dirs(cfg: &config::Config) -> Vec<camino::Utf8PathBuf> {
    match crate::collector::collect_files_in(std::slice::from_ref(&cfg.rootdir), cfg) {
        Ok((files, _)) => files
            .iter()
            .filter_map(|file| file.parent())
            .map(camino::Utf8Path::to_owned)
            .collect(),
        // A glob-set failure here is the same failure collection is about to
        // report for real; falling back to the rootdir keeps Rule 4 answering
        // rather than adding a second diagnostic for one cause.
        Err(_) => vec![cfg.rootdir.clone()],
    }
}

/// The declared test paths that actually hold test files, as directories.
///
/// A declared entry reaches [`rootdir_package`] only if the walk finds a test
/// file under it. Without the filter a declared directory holding no tests —
/// this project's own `python/oxitest`, declared so that doctest coverage
/// audits it — drags the fold above the directory the tests live in, and Rule 4
/// then rejects a `process` declaration sitting beside them (#1798).
///
/// **The filter refines between declared entries; it never demotes the
/// declaration.** When no entry holds tests the unfiltered list is returned, so
/// a project whose tests were all deleted keeps the rootdir package its config
/// describes rather than losing it to `None` — which would make every `process`
/// declaration illegal and leave the hint with no directory to name.
///
/// Membership is decided by `collect_files` itself rather than by a bespoke
/// probe. `python_files`, `norecursedirs` and `use_gitignore` all bear on what
/// counts as a test file, and a second mechanism obliged to re-honour them
/// would be this issue's own defect one layer down.
fn declared_dirs_holding_tests(cfg: &config::Config) -> Vec<camino::Utf8PathBuf> {
    let declared: Vec<camino::Utf8PathBuf> = cfg
        .paths
        .declared_testpaths
        .iter()
        .map(|path| declared_dir(path))
        .collect();

    let holding: Vec<camino::Utf8PathBuf> = declared
        .iter()
        .filter(|dir| {
            match crate::collector::collect_files_in(std::slice::from_ref(*dir), cfg) {
                Ok((files, _)) => !files.is_empty(),
                // A glob-set failure keeps the entry. Collection is about to
                // report that same failure for real, and dropping a declaration
                // on it would answer Rule 4 from a set the user never wrote.
                Err(_) => true,
            }
        })
        .cloned()
        .collect();

    if holding.is_empty() {
        declared
    } else {
        holding
    }
}

/// Normalise a declared testpath to the directory it names.
///
/// `testpaths` entries are directories by convention, but nothing forbids
/// naming a file, and [`rootdir_package`] folds directories. Split out rather
/// than inlined so the fold stays free of I/O and its unit tests stay pure.
fn declared_dir(path: &camino::Utf8Path) -> camino::Utf8PathBuf {
    if path.is_file() {
        path.parent().unwrap_or(path).to_owned()
    } else {
        path.to_owned()
    }
}

/// The directories whose declaration homes one collected test file can reach.
///
/// ADR-0009 Rule 3 makes a declaration visible to tests in its anchor package
/// "or a descendant of it", so the homes reachable from a test file are the
/// ones along its parent chain — not its own directory alone (#1765).
///
/// Bounded above by the rootdir package, **inclusive**: that is the top of the
/// declared test tree, so a home there is reachable from every test below it,
/// and it is the only site where `lifetime="process"` is legal.
///
/// A directory outside that bound gets itself alone. There is no chain to walk
/// — the bound is not its ancestor, so `take_while` would never fire and the
/// walk would climb to the filesystem root. A positional path argument reaches
/// this case while `declared_testpaths` still names another tree.
///
/// Shallowest-first. Order cannot affect resolution — `_deepest_visible` ranks
/// by anchor depth and uses registration index only for equal-depth ties, and
/// no two directories on one chain share a depth — but a fixed order keeps
/// diagnostics reproducible.
fn registration_chain(
    parent: &camino::Utf8Path,
    tree_root: Option<&camino::Utf8Path>,
) -> Vec<camino::Utf8PathBuf> {
    let Some(root) = tree_root.filter(|root| parent.starts_with(root)) else {
        return vec![parent.to_owned()];
    };
    let mut chain: Vec<camino::Utf8PathBuf> = parent
        .ancestors()
        .take_while(|dir| dir.starts_with(root))
        .map(camino::Utf8Path::to_owned)
        .collect();
    chain.reverse();
    chain
}

/// Which of the two declaration-home files this is.
///
/// They differ in exactly one place — what an unparsable file costs.
/// `__fixtures__.py` is a reserved name whose only purpose is declarations, so
/// failing to read it certainly loses fixtures and is a collection error.
/// `__init__.py` is an ordinary package-init file that usually has nothing to
/// do with fixtures, and the ancestor walk now reaches directories a run never
/// used to read, so failing on unrelated breakage there is collateral (#1765).
#[derive(Clone, Copy)]
enum HomeFile {
    Fixtures,
    Init,
}

impl HomeFile {
    /// The file name this kind is looked up under.
    const fn as_str(self) -> &'static str {
        match self {
            Self::Fixtures => "__fixtures__.py",
            Self::Init => "__init__.py",
        }
    }
}

/// One declaration-home file and where it sits in the collected test tree.
///
/// Grouped rather than passed loose: the four travel together and mean nothing
/// apart, and naming them at the call site is what keeps two `Utf8Path`s from
/// being swappable by accident.
struct DeclarationHome<'a> {
    /// The declaration file itself — `__fixtures__.py` or `__init__.py`.
    path: &'a camino::Utf8Path,
    /// The directory that owns it; the anchor of everything declared inside.
    anchor: &'a camino::Utf8Path,
    /// Which of the two the `path` names. Read only when the file cannot be
    /// parsed, which is the sole place the two kinds diverge.
    file: HomeFile,
    /// Which regime this home belongs to. The two differ in three places and
    /// share everything else, prescan included.
    kind: HomeKind<'a>,
}

/// Where the rootdir package came from.
///
/// Carried into the Rule 4 diagnostic because the two derivations disagree by
/// design: the declared fold is always an ancestor-or-equal of the layout one,
/// so *adding* `testpaths` to a project can move the rootdir package up and
/// reject a `process` declaration that was legal the day before, without
/// changing which tests run. A derived value that flips a verdict names its
/// source (#1798).
#[derive(Clone, Copy)]
enum RootProvenance {
    /// Folded from `declared_testpaths`, filtered to the entries holding tests.
    Declared,
    /// Folded from the project's layout, because it declared no `testpaths`.
    Layout,
}

impl RootProvenance {
    /// The parenthetical that follows the directory named in the Rule 4 hint.
    const fn hint_clause(self) -> &'static str {
        match self {
            Self::Declared => "the deepest directory covering your declared testpaths",
            Self::Layout => "derived from your test layout — no testpaths declared",
        }
    }
}

/// Whether a declaration home sits in the user's test tree or in a plugin.
///
/// A plugin home is *not* a user home with a synthesised tree root. Rule 4
/// exists because a `process` fixture anchored below the root attaches to no
/// boundary; a plugin's attaches to the process regardless, so the rule has
/// nothing to say about it rather than being satisfied by coincidence (#1717).
///
/// `Copy` so `DeclarationHome` keeps destructuring through a shared reference,
/// as it did when it held a bare `Option<&Utf8Path>`.
#[derive(Clone, Copy)]
enum HomeKind<'a> {
    /// A directory in the collected test tree. Anchored, B1-enforced.
    User {
        /// Top of the collected test tree. Equal to `anchor` exactly when this
        /// home is a *rootdir package*, the only place `lifetime="process"`
        /// may be declared (ADR-0009 Rule 4).
        tree_root: Option<&'a camino::Utf8Path>,
        /// Where `tree_root` came from, carried so the Rule 4 diagnostic can
        /// say rather than re-derive it.
        root_provenance: RootProvenance,
    },
    /// An activated plugin package. Ambient, B1-exempt, and outside every
    /// `testpath`, so Rule 4 does not apply and there is no subtree for the
    /// scheduler to co-locate — `package` lifetime is refused in the registrar.
    Plugin {
        plugin_module: &'a str,
        namespace: &'a str,
        autouse: &'a [String],
    },
}

/// Prescan's view of a home's declarations, in the shape the registry returns.
///
/// Used only when registration failed, so the registry has no answer to give.
/// It is a strictly worse answer — it sees only the three recognized decorator
/// spellings — which is exactly why it is the fallback and not the source.
fn ast_declarations(payload: &crate::prescan::PrescanFixturePayload) -> Vec<(String, String, u32)> {
    payload
        .declarations
        .iter()
        .map(|d| (d.fn_name.clone(), d.lifetime.clone(), *d.lineno as u32))
        .collect()
}

/// Import a declaration home, then record what it actually declared.
///
/// The declaration list comes from the registry when registration succeeded and
/// from `ast_fallback` when it did not (#1859). That ordering is the whole point:
/// registration is by marker attribute, so the registry sees every import
/// spelling — including the dynamic ones no static scan can reach — while the
/// AST sees only three. Falling back only on failure preserves the invariant the
/// previous AST-only code was written to protect: the scheduler decision must
/// hold even when registration failed.
fn register_and_record(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    home: &DeclarationHome<'_>,
    ast_fallback: &[(String, String, u32)],
    errors: &mut Vec<types::CollectError>,
    diagnostics: &mut Vec<crate::reporter::stats::DiagnosticEntry>,
    fixture_modules: &mut Vec<types::FixtureModule>,
) {
    let DeclarationHome {
        path,
        anchor,
        file,
        kind,
    } = *home;

    let session_obj = session.as_py_object(py);

    // A plugin home diverges here and at the two steps below; everything else,
    // prescan included, is shared with the user path (#1717).
    //
    // One `match` rather than an early-returning `if let` plus a second
    // destructure of the same value, which could only restate "the plugin arm
    // returned" as an `unreachable!()`.
    let (tree_root, root_provenance) = match kind {
        HomeKind::Plugin {
            plugin_module,
            namespace,
            autouse,
        } => {
            if let Err(e) = bridge::register_plugin_fixture_module(
                py,
                session_obj,
                path,
                plugin_module,
                namespace,
                autouse,
            ) {
                errors.push(e);
            }
            // No Rule 4 check: there is no tree root to compare against.
            // No `fixture_modules` entry: `package` lifetime is refused for a
            // plugin, so `package_declarations` would always be empty and the
            // scheduler has no subtree to co-locate.
            return;
        }
        HomeKind::User {
            tree_root,
            root_provenance,
        } => (tree_root, root_provenance),
    };

    // Keyed on `path`, not `anchor`: a directory may hold both a
    // `__fixtures__.py` and an `__init__.py`, which register under the same
    // anchor. Asking by anchor gives each of them the other's declarations —
    // which reported a Rule 4 violation against a file that did not contain the
    // declaration, and double-counted every package declaration in such a
    // directory.
    let declarations: Vec<(String, String, u32)> =
        match bridge::register_fixture_module_for_path(py, session_obj, path, anchor) {
            Err(e) => {
                // Importing is how a declaration is found when prescan cannot
                // rule one out (#1859), so this arm carries *user* code failing
                // — not an oxitest fault. The ancestor walk reaches files a run
                // never used to read, so the same asymmetry the parse failure
                // uses applies here: fatal for a reserved declaration file,
                // collateral for an ordinary package initialiser (#1765).
                //
                // Named either way. The bare exception text leaves the user
                // with no way to tell which file failed.
                match file {
                    HomeFile::Fixtures => errors.push(e),
                    HomeFile::Init => {
                        diagnostics.push(crate::reporter::stats::DiagnosticEntry {
                            severity: crate::reporter::stats::DiagnosticSeverity::Warning,
                            context: std::sync::Arc::from("fixture registration"),
                            message: format!(
                                "{path} could not be imported, so any fixtures it \
                                 declares are not registered: {e}"
                            ),
                            file: Some(path.to_owned()),
                            lineno: None,
                        });
                    }
                }
                ast_fallback.to_vec()
            }
            Ok(()) => session
                .module_source_declarations(py, path)
                .unwrap_or_else(|e| {
                    errors.push(e);
                    ast_fallback.to_vec()
                }),
        };

    let package_declarations = declarations
        .iter()
        .filter(|(_, lifetime, _)| lifetime == crate::prescan::LIFETIME_PACKAGE)
        .map(|(fn_name, _, lineno)| types::PackageDeclaration {
            fn_name: fn_name.clone(),
            lineno: crate::types::LineNo::from_u32(*lineno),
        })
        .collect();

    // ADR-0009 Rule 4: `process` is legal only in a rootdir package. It is the
    // tier that does not constrain the scheduler, so anchoring it below the root
    // attaches it to no boundary at all. Per declaration rather than per file,
    // so two offending declarations produce two diagnostics.
    let is_rootdir_package = tree_root == Some(anchor);
    if !is_rootdir_package {
        // Name the directory that *is* the root, and where it came from. "Move
        // it to a rootdir package" is unactionable on its own, and the root is
        // derived — from `testpaths` or from layout — so naming the directory
        // alone still leaves the user unable to tell which edit would move it.
        // Absent only when nothing is declared and the layout walk found
        // nothing, in which case this loop cannot produce a diagnostic anyway.
        let root_hint = tree_root.map_or_else(
            || "the root of your test tree".to_owned(),
            |root| format!("{root} ({})", root_provenance.hint_clause()),
        );
        errors.extend(
            declarations
                .iter()
                .filter(|(_, lifetime, _)| lifetime == crate::prescan::LIFETIME_PROCESS)
                .map(|(fn_name, _, _)| {
                    types::CollectError::PyError(format!(
                        "{fn_name} in {path} declares lifetime=\"process\", but \
                         {anchor} is not a rootdir package.\n\
                         process is the tier that does not constrain the \
                         scheduler, so anchoring it below the root attaches \
                         it to no boundary at all.\n\
                         Hint: move the declaration to {root_hint}, or drop \
                         to lifetime=\"package\" to scope it to {anchor}, or \
                         lifetime=\"module\" for per-file.",
                    ))
                }),
        );
    }

    // Recorded even when registration failed above: the serial session and a
    // worker session are independent, so a failure here says nothing about
    // whether the worker will succeed. It reports its own diagnostic.
    fixture_modules.push(types::FixtureModule {
        module: path.to_owned(),
        anchor: anchor.to_owned(),
        package_declarations,
    });
}

/// Prescan and register one activated plugin's `__fixtures__.py` (#1717).
///
/// The plugin entry into [`register_declaration_home`]. `plugin_fixture_homes`
/// has already established that the file exists, so a missing one here is a
/// race, not the ordinary "this plugin ships no fixtures" case — and prescan
/// reports it as a collection error either way.
///
/// Only `__fixtures__.py` is scanned, never `__init__.py`: that file is a
/// declaration home for users because it is the natural place for
/// package-lifetime declarations, and `package` is refused for a plugin. It is
/// also where a plugin's own `oxitest_plugin()` entry point lives.
pub(super) fn register_plugin_home(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    home: &bridge::PluginFixtureHome,
    errors: &mut Vec<types::CollectError>,
) {
    let path = home.anchor_dir.join(HomeFile::Fixtures.as_str());
    let mut fixture_modules = Vec::new();
    register_declaration_home(
        py,
        session,
        &DeclarationHome {
            path: &path,
            anchor: &home.anchor_dir,
            file: HomeFile::Fixtures,
            kind: HomeKind::Plugin {
                plugin_module: &home.plugin_module,
                namespace: &home.namespace,
                autouse: &home.autouse,
            },
        },
        errors,
        // A plugin home is always `__fixtures__.py`, so both failure arms route
        // to `errors`; this sink is unreachable by construction.
        &mut Vec::new(),
        &mut fixture_modules,
    );
}

/// Prescan one declaration-home file and register whatever it declares.
///
/// Prescan is an optimization here, not an authority. It answers "does this file
/// certainly declare fixtures?" — and when it cannot say, the file is imported
/// anyway and the runtime decides (#1859). Registration is by marker attribute,
/// so `import oxitest as ox` declares a real fixture that no static scan can
/// see; erring wide costs one import, erring narrow silently drops the fixture,
/// which is #1850.
/// Register every declaration home reachable from one test file's directory.
///
/// The chain is the file's own directory and every ancestor up to the rootdir
/// package (#1765); each directory may hold both declaration homes.
///
/// Extracted from the collection loop so the `query` and `inspect` surfaces can
/// build the same registry the pipeline does (#1720). Those surfaces used to
/// see builtins only, because `FixtureSession::new` creates an empty session
/// and the prescan walk lived here — so `oxitest query fixtures` listed
/// nothing a `@oxi.fixture` declared.
///
/// `registered_dirs` is the caller's, not this function's: collection
/// accumulates it across every file it visits, and calls this **before** its
/// cache-hit `continue` so a warm cache still registers.
#[expect(
    clippy::too_many_arguments,
    reason = "three accumulators, the dedup set, and the four inputs a DeclarationHome needs; \
              bundling them would hide which of the two Utf8Paths is the anchor"
)]
fn register_homes_in_chain(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    parent_dir: &camino::Utf8Path,
    tree_root: Option<&camino::Utf8Path>,
    root_provenance: RootProvenance,
    registered_dirs: &mut std::collections::HashSet<camino::Utf8PathBuf>,
    errors: &mut Vec<types::CollectError>,
    diagnostics: &mut Vec<crate::reporter::stats::DiagnosticEntry>,
    fixture_modules: &mut Vec<types::FixtureModule>,
) {
    for dir in registration_chain(parent_dir, tree_root) {
        // `continue`, not `break`. The set accumulates every directory a
        // previously walked file registered, so `break` would stop the second
        // file in a package at the already-registered root and never reach its
        // own directory; and the seed means a vendored plugin mid-chain must
        // not stop the walk reaching the user homes below it. The second is
        // pinned by test_a_plugin_anchor_mid_chain_does_not_stop_the_walk
        // (#1934).
        if registered_dirs.contains(&dir) {
            continue;
        }
        // Both declaration homes for this directory, per ADR-0009's
        // file-convention table. `__fixtures__.py` is reserved and holds any
        // lifetime; `__init__.py` is an ordinary package-init file that may
        // also host declarations (package lifetime is the recommended use).
        for file_kind in [HomeFile::Fixtures, HomeFile::Init] {
            let path = dir.join(file_kind.as_str());
            if path.exists() {
                register_declaration_home(
                    py,
                    session,
                    &DeclarationHome {
                        path: &path,
                        anchor: &dir,
                        file: file_kind,
                        kind: HomeKind::User {
                            tree_root,
                            root_provenance,
                        },
                    },
                    errors,
                    diagnostics,
                    fixture_modules,
                );
            }
        }
        registered_dirs.insert(dir);
    }
}

/// Register every declaration home the given test files can reach.
///
/// The query and inspect surfaces build an empty session (`FixtureSession::new`),
/// so without this they see builtins and nothing a `@oxi.fixture` declares —
/// which is exactly what `inspect` did until #1722. Collection reaches the same
/// homes through its per-file loop; this is the same walk over a file set that
/// is already known, for the surfaces that do not run a collection (#1720).
///
/// Returns the first error, if any. A query answers from the registry, so an
/// unimportable declaration file makes the answer wrong rather than merely
/// incomplete — and `no results` is indistinguishable from a correct empty
/// answer. Diagnostics are warnings about files that *were* read, so they do
/// not make the answer wrong and are dropped here.
pub fn register_declaration_homes_for_files(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    cfg: &config::Config,
    test_files: &[camino::Utf8PathBuf],
) -> Option<types::CollectError> {
    // Same derivation as the collection loop, and for the same reason: the
    // rootdir package is a property of the project, not of the run.
    let (declared_dirs, root_provenance) = if cfg.paths.declared_testpaths.is_empty() {
        (implied_declared_dirs(cfg), RootProvenance::Layout)
    } else {
        (declared_dirs_holding_tests(cfg), RootProvenance::Declared)
    };
    let tree_root = rootdir_package(&declared_dirs, &cfg.rootdir);

    // Seeded with the activated plugins' anchors, so a vendored plugin is not
    // registered twice — once ambient, once anchored (#1717).
    let mut registered_dirs: std::collections::HashSet<camino::Utf8PathBuf> = session
        .plugin_anchor_dirs(py)
        .into_iter()
        .map(camino::Utf8PathBuf::from)
        .collect();

    let mut errors = Vec::new();
    let mut diagnostics = Vec::new();
    let mut fixture_modules = Vec::new();

    for file in test_files {
        if let Some(parent_dir) = file.parent() {
            register_homes_in_chain(
                py,
                session,
                parent_dir,
                tree_root.as_deref(),
                root_provenance,
                &mut registered_dirs,
                &mut errors,
                &mut diagnostics,
                &mut fixture_modules,
            );
        }
    }

    errors.into_iter().next()
}

fn register_declaration_home(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    home: &DeclarationHome<'_>,
    errors: &mut Vec<types::CollectError>,
    diagnostics: &mut Vec<crate::reporter::stats::DiagnosticEntry>,
    fixture_modules: &mut Vec<types::FixtureModule>,
) {
    let path = home.path;
    match crate::prescan::prescan_fixture_module(path) {
        crate::prescan::PrescanFixtureResult::HasFixtures(payload) => {
            register_and_record(
                py,
                session,
                home,
                &ast_declarations(&payload),
                errors,
                diagnostics,
                fixture_modules,
            );
        }
        crate::prescan::PrescanFixtureResult::Unavailable(reason) => {
            // The file exists but could not be read or parsed. Surface it
            // naming the file, rather than a silent fixture-not-found at test
            // time.
            //
            // The two arms get different words on purpose: a parse failure is
            // a typo the user can fix from the message, a read failure is not.
            // One sentence covering both told the user neither (#1727). The
            // inline declaration home already reports at this quality, because
            // an unparsable `test_*.py` falls through to Python import — this
            // is the other two homes agreeing with it.
            //
            // Fatal for a reserved declaration file, collateral for an ordinary
            // package-init file the ancestor walk merely passed through — see
            // `HomeFile`. A warning cannot fail the run, because
            // `compute_exit_code` never reads diagnostics, which is exactly the
            // asymmetry wanted here (#1765).
            let (message, lineno) = match reason {
                crate::python_ast::ParseFailure::Parse { line, cause } => {
                    tracing::warn!(
                        path = path.as_str(),
                        line,
                        "prescan: file could not be parsed"
                    );
                    (
                        format!(
                            "{path}:{line}: {cause}; \
                             fixtures in this file will not be registered",
                        ),
                        Some(crate::types::LineNo::from_u32(line)),
                    )
                }
                crate::python_ast::ParseFailure::Io { cause } => {
                    tracing::warn!(path = path.as_str(), "prescan: file could not be read");
                    (
                        format!(
                            "{path} could not be read: {cause}; \
                             fixtures in this file will not be registered",
                        ),
                        None,
                    )
                }
            };
            match home.file {
                HomeFile::Fixtures => errors.push(types::CollectError::PyError(message)),
                HomeFile::Init => {
                    diagnostics.push(crate::reporter::stats::DiagnosticEntry {
                        severity: crate::reporter::stats::DiagnosticSeverity::Warning,
                        context: std::sync::Arc::from("fixture registration"),
                        message,
                        file: Some(path.to_owned()),
                        lineno,
                    });
                }
            }
        }
        crate::prescan::PrescanFixtureResult::NoFixtures(payload) => {
            // Prescan found no declaration it can name, but a decorated function
            // means it may not be able to name one that exists. Import and let
            // the runtime answer rather than guessing from decorator shape
            // (#1859). The previous guard rejected the file instead, which both
            // refused a spelling the runtime accepts and fired on files holding
            // no fixtures at all — it never inspected the decorator.
            if payload.has_decorated_functions {
                // The AST fallback is empty by construction on this arm —
                // prescan named no declaration, which is why we are importing at
                // all. If registration fails here there is genuinely nothing to
                // fall back to, and that is the honest answer rather than a
                // guess.
                register_and_record(py, session, home, &[], errors, diagnostics, fixture_modules);
            }
        }
    }
}

pub(super) fn collect_items(
    py: Python<'_>,
    test_files: &[Utf8PathBuf],
    cfg: &config::Config,
    session: &bridge::FixtureSession,
    cache: &mut cache::TestCache,
) -> CollectionOutput {
    let mut items: Vec<Arc<types::TestItem>> = Vec::new();
    let mut errors = Vec::new();
    let mut raw_violations: Vec<bridge::RawViolation> = Vec::new();
    let collect_violations = cfg.markers.strict.is_some();

    let profile_enabled = cfg.output.collection_profile;
    let wall_start = std::time::Instant::now();
    let mut file_profiles: Vec<FileProfile> = Vec::new();
    // Computed once rather than per directory: the rootdir package is a
    // property of the *project*, not of the run, and the per-file loop below
    // visits directories in collection order, not depth order.
    let (declared_dirs, root_provenance) = if cfg.paths.declared_testpaths.is_empty() {
        (implied_declared_dirs(cfg), RootProvenance::Layout)
    } else {
        (declared_dirs_holding_tests(cfg), RootProvenance::Declared)
    };
    let tree_root = rootdir_package(&declared_dirs, &cfg.rootdir);

    // Deduplicate fixture-module registrations: multiple test files in the
    // same directory all share the same __fixtures__.py. Register once per dir.
    //
    // Seeded with the activated plugins' anchors, so a plugin vendored under
    // `testpaths` — or installed with `pip install -e .` from inside the repo —
    // is not registered twice: once ambient as a plugin, and again anchored as
    // a user package, under the same derived namespace and with two scope
    // buckets for one fixture. The plugin reading wins, which is the declared
    // intent: the user named that package in `plugins` (#1717).
    let mut registered_fixture_dirs: std::collections::HashSet<camino::Utf8PathBuf> = session
        .plugin_anchor_dirs(py)
        .into_iter()
        .map(camino::Utf8PathBuf::from)
        .collect();
    // The same set, as (module, anchor) pairs, for the parallel path: workers
    // build their own sessions and must register exactly what the serial path
    // registered here. Deriving it independently over there would mean two
    // places deciding what counts as a registrable fixture module.
    let mut fixture_modules: Vec<types::FixtureModule> = Vec::new();
    // Non-fatal registration diagnostics. Separate from `errors` because the
    // two decide different things: `compute_exit_code` reads errors and never
    // reads diagnostics.
    let mut diagnostics: Vec<crate::reporter::stats::DiagnosticEntry> = Vec::new();

    for file in test_files {
        // Pre-scan: skip files with no test functions.
        // When collecting violations (strict mode), keep the parsed AST
        // for bare-assert detection to avoid double-parsing.
        let prescan_start = std::time::Instant::now();
        let prescan = crate::prescan::prescan_with_ast(file, collect_violations);
        let prescan_us = prescan_start.elapsed().as_micros() as u64;

        // `may_declare_inline_fixtures` gates the item cache below — see there.
        let (may_declare_inline_fixtures, cached_ast) = match prescan {
            crate::prescan::PrescanResult::NoTests => {
                tracing::debug!(path = file.as_str(), "pre-scan: no tests, skipping");
                if profile_enabled {
                    file_profiles.push(FileProfile {
                        path: file.clone(),
                        prescan_us,
                        collection_us: 0,
                        lazy_skipped: false,
                    });
                }
                continue;
            }
            // Could not be parsed: prescan cannot rule inline declarations
            // out, and "we could not tell" must not silently drop registration.
            crate::prescan::PrescanResult::Unavailable => (true, None),
            crate::prescan::PrescanResult::HasTests(p) => {
                // ADR-0009 Rule 2's home-kind cap used to be enforced here from
                // `p.declarations`. It moved to `register_module_source_fixtures`
                // in #1859: registration is by marker attribute, so this scan saw
                // only three decorator spellings and the cap silently did not
                // apply to any other.
                let ast = if collect_violations && !p.source.is_empty() {
                    Some((p.source, p.stmts))
                } else {
                    None
                };
                (p.has_fixture_shaped_decorator, ast)
            }
        };

        // Fixture-module registration: register the declaration homes this file
        // can reach — its own directory and every ancestor up to the rootdir
        // package (#1765). One registration per directory; the HashSet
        // deduplicates across the many files that share a chain.
        //
        // IMPORTANT: this must run BEFORE the cache-hit check below. On warm
        // cache runs the per-file `continue` fires before any code below it,
        // so any registration placed after the cache check is silently skipped
        // for cached modules (HIGH-1 fix).
        if let Some(parent_dir) = file.parent() {
            register_homes_in_chain(
                py,
                session,
                parent_dir,
                tree_root.as_deref(),
                root_provenance,
                &mut registered_fixture_dirs,
                &mut errors,
                &mut diagnostics,
                &mut fixture_modules,
            );
        }

        let mtime = file_mtime_secs(file);
        // The item cache may serve a file only when prescan positively
        // establishes that the file declares no inline fixtures (#1850).
        //
        // "Positively" is why the flag is `has_fixture_shaped_decorator` and
        // not `!declarations.is_empty()`: registration happens by marker
        // attribute at import, so `import oxitest as alias` declares a real
        // fixture that the declaration list — which only recognizes the
        // documented spellings — cannot see. Erring wide costs a cache miss;
        // erring narrow silently reinstates this bug for that file.
        //
        // An inline `@oxi.fixture` is registered as a side effect of importing
        // the test module — `collect_module` calls `_register_inline_fixtures`
        // — and the cache hit below `continue`s past that import. Unlike the
        // declaration homes registered above, there is nothing to hoist: with
        // no module object there are no fixture functions to register, and
        // re-importing the file to find them would run every module-level
        // statement twice on cold runs and hand warm runs a *different* module
        // object than the one the tests execute from.
        //
        // So the file pays for its own import on every run. That import is not
        // extra work serially — `collect_module` stores the module on the
        // session's `ModuleCache`, so `run_test` reuses it instead of loading
        // it later — and it is bounded by the files that declare inline
        // fixtures; every other file keeps the cache.
        let cached = if collect_violations || may_declare_inline_fixtures {
            None
        } else {
            cache.cached_module_items(file, mtime)
        };
        if let Some(cached_items) = cached {
            items.extend(cached_items);
            if profile_enabled {
                file_profiles.push(FileProfile {
                    path: file.clone(),
                    prescan_us,
                    collection_us: 0,
                    lazy_skipped: false,
                });
            }
            continue;
        }

        let collection_start = std::time::Instant::now();
        match bridge::collect_module_with_session_obj(
            py,
            file,
            session.as_py_object(py),
            collect_violations,
        ) {
            Ok((file_items, file_violations)) => {
                let arc_items: Vec<Arc<types::TestItem>> =
                    file_items.into_iter().map(Arc::new).collect();
                if mtime != 0 && !collect_violations {
                    cache.update_module_cache(file, mtime, &arc_items);
                }
                raw_violations.extend(file_violations);
                // Bare-assert detection: reuse pre-parsed AST when available.
                if collect_violations {
                    if let Some((ref source, ref stmts)) = cached_ast {
                        raw_violations.extend(bare_asserts::collect_bare_asserts_from_ast(
                            file, source, stmts,
                        ));
                    } else {
                        raw_violations.extend(bare_asserts::collect_bare_asserts(file));
                    }
                }
                items.extend(arc_items);
            }
            Err(e) => errors.push(e),
        }
        let collection_us = collection_start.elapsed().as_micros() as u64;

        if profile_enabled {
            file_profiles.push(FileProfile {
                path: file.clone(),
                prescan_us,
                collection_us,
                lazy_skipped: false,
            });
        }
    }

    if errors.is_empty() {
        let registered: std::collections::HashSet<&str> = cfg
            .markers
            .registered_markers
            .iter()
            .map(String::as_str)
            .collect();
        let marker_errors = filter::validate_markers(&items, &registered);
        errors.extend(marker_errors);
    }

    let profile = if profile_enabled {
        Some(CollectionProfile {
            files: file_profiles,
            total_us: wall_start.elapsed().as_micros() as u64,
        })
    } else {
        None
    };

    CollectionOutput {
        items,
        errors,
        raw_violations,
        profile,
        fixture_modules,
        diagnostics,
    }
}

/// Scan files for docstrings with `>>>` examples and create doctest `TestItem`s.
pub(super) fn collect_doctest_items(doctest_files: &[Utf8PathBuf]) -> Vec<Arc<types::TestItem>> {
    let mut items = Vec::new();

    for file in doctest_files {
        let locations = crate::doctest::scan_doctests(file);
        for loc in locations {
            let fn_name = format!("<doctest>{}", loc.name);
            let node_id = types::NodeId::new(file.as_str(), &fn_name, None);
            items.push(Arc::new(types::TestItem {
                node_id,
                fn_name: Arc::from(fn_name.as_str()),
                lineno: types::LineNo::new(loc.lineno),
                markers: types::MarkerSet::from(vec!["doctest".to_string()]),
                param_id: None,
                param_values: vec![],
                is_async: false,
                fixture_deps: vec![],
                fixref_deps: vec![],
                arranged: vec![],
            }));
        }
    }

    items
}

/// Run the doctest coverage rule and return the resulting diagnostics.
///
/// Called from `Pipeline::collect` right after `collect_doctest_items`. Returns
/// an empty vec when doctest collection is disabled, when scope is `off`, or
/// when the global `strict` mode is `None` or `Off`. Severity inherits from
/// `[tool.oxitest].strict`:
///
/// - `None` / `Off` → rule is **silent** (no diagnostics returned).
/// - `Enforce` → gaps + analysis errors surface at `Warning` severity.
/// - `Abort` → gaps + analysis errors surface at `Error` severity;
///   promoted to hard-fail by `split_coverage_diagnostics`.
pub(super) fn collect_coverage_diagnostics(
    doctest_files: &[Utf8PathBuf],
    config: &crate::config::Config,
) -> Vec<crate::reporter::stats::DiagnosticEntry> {
    use crate::config::StrictMode;
    use crate::doctest::alias::ModuleRoot;
    use crate::doctest::coverage::run_coverage_check;
    use crate::reporter::stats::DiagnosticSeverity;

    let Some(dt) = config.doctest.as_ref() else {
        return vec![];
    };

    // Severity is driven by the global strict mode — same axis, one vocabulary.
    let severity = match config.markers.strict.as_ref() {
        None | Some(StrictMode::Off) => return vec![],
        Some(StrictMode::Enforce) => DiagnosticSeverity::Warning,
        Some(StrictMode::Abort) => DiagnosticSeverity::Error,
    };

    let root = ModuleRoot {
        root: config.rootdir.clone(),
        use_gitignore: config.paths.use_gitignore,
    };
    // `norecursedirs` is a semantic exclusion: directories with those names are
    // never scanned for coverage regardless of how the files were discovered.
    // This matters when the user passes an explicit path that is itself a
    // norecursedirs directory (e.g. `python -m oxitest python/tests/docs/`
    // where `norecursedirs = ["docs"]`): the collector's WalkBuilder does not
    // prune the walk root, so the files appear in `doctest_files`. We enforce
    // the exclusion here so coverage is consistent with full-testpaths runs.
    let norecursedirs = &config.paths.norecursedirs;
    // Exclude files matching `python_files` glob (default: `test_*.py`). Test
    // modules aren't part of the public API surface — their `test_*` functions
    // should not be treated as coverage subjects.
    let python_files_glob = crate::collector::build_glob_set(&config.paths.python_files).ok();
    // Skip is applied at the subject level by `filter_subjects_by_scope`
    // inside `run_coverage_check` (Task 8). A pre-scan skip filter would be
    // both redundant and too coarse — Symbol entries would drop the whole file.
    let rel_files: Vec<Utf8PathBuf> = doctest_files
        .iter()
        .filter_map(|abs| abs.strip_prefix(&config.rootdir).ok().map(|p| p.to_owned()))
        .filter(|rel| {
            !rel.components()
                .any(|c| norecursedirs.iter().any(|d| d.as_str() == c.as_str()))
        })
        .filter(|rel| {
            python_files_glob.as_ref().is_none_or(|g| {
                // Match against filename only (same as the collector)
                !rel.file_name().is_some_and(|name| g.is_match(name))
            })
        })
        // `conftest.py` is test infrastructure by pytest/oxitest convention —
        // its top-level definitions are fixture registrations and helper setup,
        // never public API. Excluded alongside `python_files` matches. See #1616.
        .filter(|rel| rel.file_name() != Some("conftest.py"))
        .collect();

    // Phase-1 scope prescreen: under scope = List, drop files that no scope
    // entry could plausibly match. Under Public, no prescreen (every file
    // that survived the built-in filters is considered).
    let rel_files: Vec<Utf8PathBuf> = rel_files
        .into_iter()
        .filter(|rel| match dt.scope.as_ref() {
            None | Some(crate::config::DoctestScope::Public) => true,
            Some(crate::config::DoctestScope::List(entries)) => file_could_match(rel, entries),
        })
        .collect();

    let (mut diagnostics, (scope_hits, skip_hits), scanned_files) =
        run_coverage_check(&rel_files, &root, severity.clone(), &dt.scope, &dt.skip);

    let scope_entries: &[crate::config::ScopeEntry] = match dt.scope.as_ref() {
        Some(crate::config::DoctestScope::List(e)) => e,
        _ => &[],
    };
    let inputs = StalenessInputs {
        rootdir: &config.rootdir,
        coverage_roots: crate::collector::coverage_roots(config),
        scanned: &scanned_files.parsed,
        parse_failed: &scanned_files.parse_failed,
    };
    diagnostics.extend(stale_diagnostics(
        scope_entries,
        &scope_hits,
        "doctest.coverage.stale-scope",
        "scope",
        &severity,
        &inputs,
    ));
    diagnostics.extend(stale_diagnostics(
        &dt.skip,
        &skip_hits,
        "doctest.coverage.stale-skip",
        "skip",
        &severity,
        &inputs,
    ));

    diagnostics
}

/// What a staleness verdict is allowed to consult.
///
/// Nothing here comes from `FilterConfig`, so `--affected`, `--lf`, `--ff`,
/// `-E` and explicit CLI paths need no special case: they cannot change either
/// input. That invariant is the whole design -- see ADR-0010. Three shipped
/// predicates violated it and each one reopened #1796 in a new shape.
struct StalenessInputs<'a> {
    rootdir: &'a Utf8Path,
    /// The project's declared auditable surface, from
    /// [`crate::collector::coverage_roots`] — the same call the coverage walk
    /// uses, so the walk and the verdict cannot disagree about what is in
    /// scope.
    ///
    /// This is the guard's third input, and the reason it is legal: after
    /// #1798, `coverage_roots` cannot observe positional CLI paths, so ADR-0010's
    /// invariant still holds. It rests on one fact —
    /// `src/config/merge.rs:120` is the sole writer of `declared_testpaths`.
    /// Anything that starts writing it from argv reopens #1796, and no test
    /// can express that.
    coverage_roots: &'a [Utf8PathBuf],
    /// Files the coverage scanner actually opened and parsed, relative to
    /// `rootdir` -- what `run_coverage_check` reports back, not what it was
    /// offered.
    ///
    /// The distinction is the whole point. The scanner drops any file whose
    /// dotted module path has an underscore-prefixed component unless a scope
    /// entry names it explicitly, and drops any file `parse_file` cannot read.
    /// A dropped file was never opened, so the run holds no evidence about the
    /// symbols inside it -- an entry naming one must abstain, not be reported
    /// stale. Gating on the offered set instead told users to "check the symbol
    /// name" for symbols that were fine (#1796).
    scanned: &'a [Utf8PathBuf],
    /// Files the scan attempted to read but could not parse, relative to
    /// `rootdir` (#1800). Not evidence about the symbols inside — the file was
    /// never read — but evidence that the run *tried*: an entry naming a
    /// symbol in one of these reports the parse failure instead of abstaining.
    /// Files the scan never attempted appear in neither slice and still force
    /// abstention.
    parse_failed: &'a [Utf8PathBuf],
}

/// Why an entry is stale, or that it is not.
#[derive(Debug)]
enum Staleness {
    /// The entry names a path that is not on disk.
    MissingPath,
    /// The entry exists but is disjoint from the declared test tree, so no
    /// invocation can bring it into scope (#1798). Closes ADR-0010's first
    /// blind spot, whose expiry condition was this issue.
    Unreachable,
    /// The file was scanned, but the named symbol produced no coverage subject.
    NoSubjects,
    /// The scan attempted the entry's file and could not parse it (#1800).
    /// Not a staleness verdict — the entry may be perfectly correct — but the
    /// run cannot judge it until the file is fixed, and saying so beats
    /// silent abstention.
    ParseFailure,
    /// Not stale.
    Fresh,
}

impl StalenessInputs<'_> {
    /// Whether `rel` is disjoint from every declared root.
    ///
    /// Symmetric: an entry is reachable if it sits under a declared root **or**
    /// contains one. `scope = ["src/"]` against `testpaths = ["src/pkg"]` is the
    /// second case, and it matches every subject under `src/pkg` today —
    /// containment alone would report a working config stale, which is the
    /// shape that reopened #1796 on attempt 3.
    ///
    /// Both sides go through `rootdir.join` because `declared_testpaths` is
    /// absolute under `Config::load` and relative under `Config::from_str`,
    /// while entries are always rootdir-relative; `join` returns an absolute
    /// path unchanged, so one expression is correct in both. Comparison is
    /// component-wise, so a `Prefix` entry's trailing `/` is immaterial here —
    /// the `ENOTDIR` behaviour it drives lives in the `exists()` check above.
    fn is_unreachable(&self, rel: &Utf8Path) -> bool {
        let entry = self.rootdir.join(rel);
        !self.coverage_roots.iter().any(|declared| {
            let declared = self.rootdir.join(declared);
            entry.starts_with(&declared) || declared.starts_with(&entry)
        })
    }

    /// Classify *entry*, given whether it matched a coverage subject this run.
    ///
    /// Three questions, kept apart because a run can only answer one of them:
    ///
    /// 1. **Does the path exist?** Static and run-independent (`src/mod.py` vs
    ///    `src/mods.py`).
    /// 2. **Can any run reach it?** Also static — see [`Self::is_unreachable`].
    /// 3. **Does the named symbol exist?** Only the scan knows, so it stays
    ///    hit-based, gated on exact membership in the scanned set.
    fn classify(&self, entry: &crate::config::ScopeEntry, hit: bool) -> Staleness {
        use crate::config::ScopeEntry;

        let rel = match entry {
            ScopeEntry::Prefix(path) | ScopeEntry::File(path) => path,
            ScopeEntry::Symbol { file, .. } | ScopeEntry::Member { file, .. } => file,
        };
        // `Prefix` entries keep their trailing `/` from `parse_scope_entry_str`,
        // so POSIX path resolution rejects a regular file here (ENOTDIR) and no
        // explicit `is_dir()` check is needed. Pinned by
        // `stale_prefix_entry_naming_a_regular_file_is_stale`.
        if !self.rootdir.join(rel).exists() {
            return Staleness::MissingPath;
        }
        // After existence, before the shape dispatch: all four shapes get it
        // (`Prefix`/`File` are unconditionally Fresh below once they exist),
        // and an entry that is both mistyped and disjoint reports as the typo
        // it is rather than sending the user to `testpaths`.
        if self.is_unreachable(rel) {
            return Staleness::Unreachable;
        }
        match entry {
            // A Prefix/File entry whose path exists is not a typo. Zero hits
            // means only that this run produced no subjects under it, which
            // happens routinely (test_*.py exclusion, conftest.py, private-only
            // modules) and is never evidence. Asking anything run-dependent
            // here is what reopened #1796 three times.
            ScopeEntry::Prefix(_) | ScopeEntry::File(_) => Staleness::Fresh,
            ScopeEntry::Symbol { .. } | ScopeEntry::Member { .. } => {
                // Parse failure first (#1800): the scan tried to read this
                // exact file and could not, so it can say *why* the entry has
                // no verdict instead of silently abstaining. Same exact
                // membership rule as `scanned` below, for the same reason.
                if self
                    .parse_failed
                    .iter()
                    .any(|failed_file| failed_file == rel)
                {
                    return Staleness::ParseFailure;
                }
                // Exact membership, not containment: the question is whether
                // *this file* was scanned, never whether its directory was.
                // Directory-level containment is what the retired `covers()`
                // asked -- it judges symbols in files the run never opened,
                // guessing where it must abstain, and reopens #1796.
                // `==` and `starts_with` agree on today's inputs, because
                // `Symbol`/`Member` always name a concrete file. That makes
                // `starts_with` look free; it stops being free the moment
                // anything directory-shaped reaches this arm.
                if !hit && self.scanned.iter().any(|scanned_file| scanned_file == rel) {
                    Staleness::NoSubjects
                } else {
                    Staleness::Fresh
                }
            }
        }
    }
}

/// Diagnose scope/skip entries that can never match a coverage subject —
/// plus entries whose file the scan attempted but could not parse (#1800).
///
/// A missing path and a missing symbol are different findings with different
/// remedies, so they get different messages. The staleness `context` strings
/// are unchanged; a parse failure is not a staleness verdict, so it reports
/// under `doctest.coverage.parse-error` instead — leaving `stale-scope` /
/// `stale-skip` reserved for entries that can never match. See ADR-0010.
fn stale_diagnostics(
    entries: &[crate::config::ScopeEntry],
    hits: &[bool],
    context: &'static str,
    kind: &'static str,
    severity: &crate::reporter::stats::DiagnosticSeverity,
    inputs: &StalenessInputs<'_>,
) -> Vec<crate::reporter::stats::DiagnosticEntry> {
    entries
        .iter()
        .zip(hits.iter())
        .filter_map(|(entry, hit)| {
            let (entry_context, detail) = match inputs.classify(entry, *hit) {
                Staleness::Fresh => return None,
                // `render_entry` prints the whole `file::Class::method`, so this
                // must say which half is wrong -- otherwise a Symbol entry with
                // a missing file reads as "the method does not exist" and sends
                // the user hunting in the wrong place.
                Staleness::MissingPath => (
                    context,
                    "names a path that does not exist (remove the entry or fix the path)",
                ),
                // Same `context` as a missing path: ADR-0010 reserves
                // stale-scope/stale-skip for "entries that can never match",
                // which is exactly what this is. A new context string would
                // also have to be added to `split_coverage_diagnostics`'
                // hard-fail list, and a miss there degrades silently to a
                // pending warning under `abort`.
                Staleness::Unreachable => (
                    context,
                    "is outside the declared test tree, so it can never match \
                     (add it to testpaths, or remove the entry)",
                ),
                Staleness::NoSubjects => (
                    context,
                    "matched no coverage subjects (remove it, or check the symbol name)",
                ),
                Staleness::ParseFailure => (
                    "doctest.coverage.parse-error",
                    "names a file that could not be parsed (fix the file so \
                     coverage can judge this entry)",
                ),
            };
            Some(crate::reporter::stats::DiagnosticEntry {
                severity: severity.clone(),
                context: std::sync::Arc::from(entry_context),
                message: format!(
                    "{kind} entry '{}' {detail}",
                    crate::config::render_entry(entry),
                ),
                file: None,
                lineno: None,
            })
        })
        .collect()
}

/// Split a coverage diagnostic set into hard-fail errors and pending diagnostics.
///
/// `doctest.coverage`, `doctest.coverage.analysis`, `doctest.coverage.stale-scope`,
/// `doctest.coverage.stale-skip`, and `doctest.coverage.parse-error`
/// Error-severity entries all become `CollectError::PyError` (hard fail under
/// `strict = "abort"`). A parse failure hard-fails on the same terms: the run
/// cannot vouch for coverage of a module it could not read (#1800).
///
/// Under `abort`, an analysis error means "the scanner cannot verify coverage
/// for this subject" — that is semantically a coverage failure. Stale scope/skip
/// entries are also hard-failed under `abort` so a typo (`src/mod.py` vs
/// `src/mods.py`) cannot silently bypass coverage. Users who need to allow
/// unresolvable aliases must fix the alias chain or downgrade `strict` globally.
///
/// Warning/Notice always pass through to pending.
pub(super) fn split_coverage_diagnostics(
    diagnostics: Vec<crate::reporter::stats::DiagnosticEntry>,
) -> (
    Vec<crate::types::CollectError>,
    Vec<crate::reporter::stats::DiagnosticEntry>,
) {
    use crate::reporter::stats::DiagnosticSeverity;
    use crate::types::CollectError;
    let mut errors = Vec::new();
    let mut pending = Vec::new();
    for d in diagnostics {
        let is_hard_fail_context = matches!(
            d.context.as_ref(),
            "doctest.coverage"
                | "doctest.coverage.analysis"
                | "doctest.coverage.stale-scope"
                | "doctest.coverage.stale-skip"
                | "doctest.coverage.parse-error"
        );
        if d.severity == DiagnosticSeverity::Error && is_hard_fail_context {
            let mut msg = d.message.clone();
            if let Some(file) = &d.file
                && let Some(lineno) = d.lineno
            {
                msg = format!("{} ({}:{})", msg, file, *lineno);
            }
            errors.push(CollectError::PyError(msg));
        } else {
            pending.push(d);
        }
    }
    (errors, pending)
}

#[cfg(test)]
mod tests {
    use super::*;
    use assert_fs::prelude::*;

    // ── ast_declarations: the registration-failure fallback (#1859) ──────────

    #[test]
    fn ast_declarations_preserves_the_scheduler_answer_when_registration_fails() {
        // Arrange — what prescan saw, in the shape the registry would return.
        let payload = crate::prescan::PrescanFixturePayload {
            declarations: vec![crate::prescan::PrescanDeclaration {
                fn_name: "engine".to_owned(),
                lineno: crate::types::LineNo::new(4),
                lifetime: crate::prescan::LIFETIME_PACKAGE.to_owned(),
                is_async: false,
            }],
        };

        // Act
        let sourced = ast_declarations(&payload);

        // Assert
        assert_eq!(
            sourced,
            vec![("engine".to_owned(), "package".to_owned(), 4_u32)],
            "when registration fails the registry has no answer, so the AST is \
             the only source left. Dropping to an empty list instead would \
             silently disable co-location for the whole subtree — the exactly-\
             once guarantee failing quietly, which is the defect #1859 exists \
             to remove rather than relocate"
        );
    }

    // ── rootdir_package (#1711, re-based on the declared tree by #1798) ──────

    /// Declared entries are spelled absolute here, because `Config::load`
    /// resolves each one against the rootdir (`config::merge::resolve_testpaths`).
    /// The relative form these fold tests used before #1921 is reachable only
    /// through the test-only `Config::from_str`, so they exercised a shape no
    /// real run takes. The `registration_chain` tests below stay relative: that
    /// walk is pure path arithmetic whose inputs' absoluteness is immaterial.
    fn paths(entries: &[&str]) -> Vec<Utf8PathBuf> {
        entries.iter().map(Utf8PathBuf::from).collect()
    }

    #[test]
    fn rootdir_package_of_one_declared_directory_is_that_directory() {
        let declared = paths(&["/proj/tests"]);

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/proj/tests")),
            "a project declaring one test directory makes it the rootdir \
             package — that is where a lifetime=\"process\" declaration is legal"
        );
    }

    #[test]
    fn rootdir_package_climbs_to_the_common_ancestor_of_declared_siblings() {
        let declared = paths(&["/proj/tests/api", "/proj/tests/db"]);

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/proj/tests")),
            "two declared siblings put the root at their parent, even though it \
             is not itself declared; declaring process in either sibling would \
             scope it below the project's own test surface"
        );
    }

    #[test]
    fn rootdir_package_ignores_which_subtree_the_run_narrowed_to() {
        // The whole point of #1798. `testpaths` and the collected file set are
        // both narrowed by a positional path argument; `declared_testpaths` is
        // not. Deriving from either of the first two made `oxitest project/` and
        // `oxitest project/suite/` disagree about which directory is the root,
        // and so disagree about whether the same declaration was legal.
        //
        // The unit can only assert that the fold reads the declared set; that
        // the declared set survives merge is asserted end-to-end by
        // test_rule_4_verdict_does_not_depend_on_how_the_run_was_invoked.
        let declared = paths(&["/proj/project/suite"]);

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/proj/project/suite")),
            "the root is the declared test tree, so no argv can move it; a \
             narrowed run must not be able to legalise a declaration the \
             project's own layout rejects"
        );
    }

    #[test]
    fn rootdir_package_of_nothing_declared_is_none() {
        let root = rootdir_package(&[], Utf8Path::new("/proj"));

        assert!(
            root.is_none(),
            "with nothing declared there is no tree, so no directory can be the \
             rootdir package and no process declaration can sit inside one"
        );
    }

    #[test]
    fn rootdir_package_never_escapes_the_project() {
        // An absolute `testpaths` entry outside the project: `rootdir.join(s)`
        // returns `s` unchanged when `s` is already absolute, so the fold has
        // no in-project ancestor to land on and climbs above `rootdir` — the
        // Rule 4 hint then tells the user to move their declaration to `/`.
        let declared = vec![
            Utf8PathBuf::from("/proj/tests"),
            Utf8PathBuf::from("/elsewhere/suite"),
        ];

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/proj")),
            "the rootdir package is the root of THIS project's declared test \
             tree, so a declared entry outside the project must widen it to the \
             project root and no further — naming a directory the project does \
             not own gives the user a hint they cannot act on"
        );
    }

    #[test]
    fn a_wholly_external_declaration_keeps_its_own_root() {
        // The clamp must not fire when nothing is declared inside the project.
        // Clamping here would reject a `process` declaration sitting beside the
        // only tests there are, and point the user at the project root — which
        // holds none. Measured before this guard existed: the same project went
        // from `1 passed` to a collection error naming a test-less directory.
        let declared = vec![Utf8PathBuf::from("/elsewhere/suite")];

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/elsewhere/suite")),
            "a project declaring its whole test surface outside itself has its \
             rootdir package out there too; the clamp exists to stop an outside \
             entry dragging an *inside* surface upward, and there is no inside \
             surface here to drag"
        );
    }

    #[test]
    fn rootdir_package_and_a_parent_relative_declaration() {
        // `Utf8Path::starts_with` is component-wise and does not normalise, so
        // `/proj/..` may compare as starting with `/proj`. This test records
        // which way that falls rather than assuming it: the clamp is written
        // against lexical containment, and a `..` entry is the one input where
        // lexical and real containment can disagree.
        let declared = vec![
            Utf8PathBuf::from("/proj/../a"),
            Utf8PathBuf::from("/proj/../b"),
        ];

        let root = rootdir_package(&declared, Utf8Path::new("/proj"));

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("/proj/..")),
            "a testpaths entry escaping via `..` is lexically inside rootdir, so \
             the clamp does not fire on it; this is a recorded limitation, not a \
             guarantee — change this assertion only alongside a decision on #1921"
        );
    }

    // ── registration_chain (#1765) ──────────────────────────────────────────

    #[test]
    fn chain_from_below_the_root_includes_every_directory_up_to_it() {
        let chain = registration_chain(Utf8Path::new("tests/api/v1"), Some(Utf8Path::new("tests")));

        assert_eq!(
            chain,
            paths(&["tests", "tests/api", "tests/api/v1"]),
            "ADR-0009 Rule 3 makes a declaration visible to descendants, so \
             every directory between the test and the rootdir package can hold \
             a home the test must see; shallowest-first keeps diagnostics \
             reproducible"
        );
    }

    #[test]
    fn chain_of_the_rootdir_package_itself_is_just_that_directory() {
        let chain = registration_chain(Utf8Path::new("tests"), Some(Utf8Path::new("tests")));

        assert_eq!(
            chain,
            paths(&["tests"]),
            "the bound is inclusive — a home at the rootdir package is \
             reachable from every test under it, and is the only legal site \
             for lifetime=\"process\""
        );
    }

    #[test]
    fn chain_of_a_directory_outside_the_root_is_only_itself() {
        let chain = registration_chain(Utf8Path::new("other"), Some(Utf8Path::new("tests")));

        assert_eq!(
            chain,
            paths(&["other"]),
            "a positional path argument can collect a file outside the \
             declared tree; the bound is not its ancestor, so walking would run \
             to the filesystem root instead of stopping"
        );
    }

    #[test]
    fn chain_without_a_rootdir_package_is_only_the_directory() {
        let chain = registration_chain(Utf8Path::new("tests/api"), None);

        assert_eq!(
            chain,
            paths(&["tests/api"]),
            "with no declared tree there is no bound to walk to, so \
             registration stays where it is today rather than guessing one"
        );
    }

    /// A project declaring both a test tree and a test-less directory, which is
    /// this repository's own shape: `testpaths = ["python/tests",
    /// "python/oxitest"]`, where the second holds the doctest-audit subject and
    /// no test files.
    fn declared_project(entries: &[(&str, &str)]) -> (assert_fs::TempDir, crate::config::Config) {
        let dir = assert_fs::TempDir::new().unwrap();
        for (path, _) in entries {
            dir.child(path).touch().unwrap();
        }
        let root = Utf8PathBuf::from_path_buf(dir.path().to_owned()).unwrap();
        let declared_testpaths = entries
            .iter()
            .map(|(_, declared)| root.join(declared))
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect();
        let cfg = crate::config::Config {
            rootdir: root,
            paths: crate::config::PathConfig {
                declared_testpaths,
                ..Default::default()
            },
            ..Default::default()
        };
        (dir, cfg)
    }

    #[test]
    fn a_declared_directory_holding_no_tests_does_not_move_the_root() {
        // `srconly` is declared — a doctest-coverage subject — but holds no test
        // file. Folding it in drags the root to the project root, above the
        // directory the tests and their __fixtures__.py actually live in.
        let (dir, cfg) = declared_project(&[
            ("suite/test_a.py", "suite"),
            ("srconly/module.py", "srconly"),
        ]);
        let root = Utf8PathBuf::from_path_buf(dir.path().to_owned()).unwrap();

        let held = declared_dirs_holding_tests(&cfg);

        assert_eq!(
            rootdir_package(&held, &root),
            Some(root.join("suite")),
            "a declared directory with no test files must not move the rootdir \
             package: declaring a source tree so doctest coverage audits it \
             would otherwise outlaw every process fixture beside the tests"
        );
    }

    #[test]
    fn declared_directories_that_all_lack_tests_keep_the_unfiltered_fold() {
        // The filter refines between declared entries. With no entry to prefer
        // there is nothing to refine, and demoting the declaration to `None`
        // would reject every process declaration with a hint naming no
        // directory.
        let (dir, cfg) = declared_project(&[("a/module.py", "a"), ("b/module.py", "b")]);
        let root = Utf8PathBuf::from_path_buf(dir.path().to_owned()).unwrap();

        let held = declared_dirs_holding_tests(&cfg);

        assert_eq!(
            rootdir_package(&held, &root),
            Some(root.clone()),
            "when no declared entry holds tests the unfiltered declaration is \
             still the project's own statement of its test surface, and folding \
             it keeps a directory to name in the Rule 4 hint"
        );
    }

    #[test]
    fn declared_dir_of_a_directory_is_itself() {
        let dir = assert_fs::TempDir::new().unwrap();
        let sub = dir.child("suite");
        sub.create_dir_all().unwrap();
        let path = Utf8PathBuf::from_path_buf(sub.path().to_owned()).unwrap();

        let normalised = declared_dir(&path);

        assert_eq!(
            normalised, path,
            "a declared directory is already the unit the fold works in; \
             rewriting it to its parent would silently move the rootdir package \
             one level up for every project"
        );
    }

    #[test]
    fn declared_dir_of_a_file_is_its_parent() {
        let dir = assert_fs::TempDir::new().unwrap();
        let file = dir.child("suite/test_only.py");
        file.touch().unwrap();
        let path = Utf8PathBuf::from_path_buf(file.path().to_owned()).unwrap();

        let normalised = declared_dir(&path);

        assert_eq!(
            normalised,
            path.parent().unwrap(),
            "nothing forbids testpaths naming a file, and folding the file path \
             itself would make the rootdir package a path that is not a \
             directory — so Rule 4 could never match any declaration's anchor"
        );
    }

    #[test]
    fn profile_shows_header_and_breakdown() {
        let profile = CollectionProfile {
            files: vec![
                FileProfile {
                    path: Utf8PathBuf::from("tests/test_fast.py"),
                    prescan_us: 2_000,
                    collection_us: 8_000,
                    lazy_skipped: false,
                },
                FileProfile {
                    path: Utf8PathBuf::from("tests/test_slow.py"),
                    prescan_us: 5_000,
                    collection_us: 45_000,
                    lazy_skipped: false,
                },
            ],
            total_us: 65_000,
        };

        let out = format_collection_profile(&profile);
        assert!(out.contains("Collection profile (2 files, 65ms total):"));
        assert!(out.contains("prescan:"));
        assert!(out.contains("collection:"));
        assert!(out.contains("other:"));
    }

    #[test]
    fn profile_slowest_files_sorted_descending() {
        let profile = CollectionProfile {
            files: vec![
                FileProfile {
                    path: Utf8PathBuf::from("fast.py"),
                    prescan_us: 1_000,
                    collection_us: 2_000,
                    lazy_skipped: false,
                },
                FileProfile {
                    path: Utf8PathBuf::from("slow.py"),
                    prescan_us: 3_000,
                    collection_us: 20_000,
                    lazy_skipped: false,
                },
            ],
            total_us: 30_000,
        };

        let out = format_collection_profile(&profile);
        let slow_pos = out.find("slow.py").expect("slow.py in output");
        let fast_pos = out.find("fast.py").expect("fast.py in output");
        assert!(
            slow_pos < fast_pos,
            "slow.py should appear before fast.py in slowest files"
        );
    }

    #[test]
    fn profile_omits_slowest_when_all_zero() {
        let profile = CollectionProfile {
            files: vec![FileProfile {
                path: Utf8PathBuf::from("empty.py"),
                prescan_us: 0,
                collection_us: 0,
                lazy_skipped: false,
            }],
            total_us: 100,
        };

        let out = format_collection_profile(&profile);
        assert!(
            !out.contains("Slowest files:"),
            "should not show slowest files when all timings are zero"
        );
    }

    #[test]
    fn profile_shows_lazy_eager_split() {
        let profile = CollectionProfile {
            files: vec![
                FileProfile {
                    path: Utf8PathBuf::from("tests/test_fast.py"),
                    prescan_us: 2_000,
                    collection_us: 8_000,
                    lazy_skipped: true,
                },
                FileProfile {
                    path: Utf8PathBuf::from("tests/test_slow.py"),
                    prescan_us: 5_000,
                    collection_us: 45_000,
                    lazy_skipped: false,
                },
            ],
            total_us: 65_000,
        };
        let out = format_collection_profile(&profile);
        assert!(out.contains("lazy: 1"));
        assert!(out.contains("eager: 1"));
    }

    #[test]
    fn file_mtime_secs_returns_nonzero_for_existing_file() {
        let mtime = file_mtime_secs(camino::Utf8Path::new(file!()));
        assert!(mtime > 0, "mtime must be non-zero for an existing file");
    }

    #[test]
    fn file_mtime_secs_returns_zero_for_missing_file() {
        let mtime = file_mtime_secs(camino::Utf8Path::new("/nonexistent/path/xyz.py"));
        assert_eq!(mtime, 0);
    }

    // ── collect_coverage_diagnostics ────────────────────────────────────────

    fn write_pkg_with_missing_examples(root: &camino::Utf8Path) {
        std::fs::create_dir_all(root.join("mypkg"))
            .expect("test setup: mypkg dir must be creatable");
        std::fs::write(
            root.join("mypkg/__init__.py"),
            "__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .expect("test setup: mypkg/__init__.py must be writable");
    }

    #[test]
    fn collect_coverage_diagnostics_emits_when_scope_public_and_strict_enforce() {
        use crate::config::{DoctestConfig, DoctestScope, StrictMode};
        use crate::reporter::stats::DiagnosticSeverity;

        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        write_pkg_with_missing_examples(&root);

        let files = vec![root.join("mypkg/__init__.py")];
        let cfg = crate::config::Config {
            rootdir: root,
            markers: crate::config::MarkerConfig {
                strict: Some(StrictMode::Enforce),
                ..Default::default()
            },
            doctest: Some(DoctestConfig {
                scope: Some(DoctestScope::Public),
                ..Default::default()
            }),
            ..Default::default()
        };

        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert_eq!(
            diags.len(),
            1,
            "one missing-header diagnostic expected for the single public subject"
        );
        assert_eq!(
            diags[0].severity,
            DiagnosticSeverity::Warning,
            "strict=enforce must map to Warning severity so runs don't fail on gaps"
        );
        assert_eq!(
            diags[0].context.as_ref(),
            "doctest.coverage",
            "reporter dedup groups on this exact context string"
        );
    }

    #[test]
    fn collect_coverage_diagnostics_returns_empty_when_strict_off() {
        use crate::config::{DoctestConfig, DoctestScope, StrictMode};

        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        write_pkg_with_missing_examples(&root);

        let files = vec![root.join("mypkg/__init__.py")];
        let cfg = crate::config::Config {
            rootdir: root,
            markers: crate::config::MarkerConfig {
                strict: Some(StrictMode::Off),
                ..Default::default()
            },
            doctest: Some(DoctestConfig {
                scope: Some(DoctestScope::Public),
                ..Default::default()
            }),
            ..Default::default()
        };

        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            diags.is_empty(),
            "strict=off silences the rule regardless of scope"
        );
    }

    #[test]
    fn collect_coverage_diagnostics_returns_empty_when_strict_absent() {
        use crate::config::{DoctestConfig, DoctestScope};

        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        write_pkg_with_missing_examples(&root);

        let files = vec![root.join("mypkg/__init__.py")];
        // markers.strict is None by default
        let cfg = crate::config::Config {
            rootdir: root,
            doctest: Some(DoctestConfig {
                scope: Some(DoctestScope::Public),
                ..Default::default()
            }),
            ..Default::default()
        };

        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            diags.is_empty(),
            "strict absent (None) silences the rule — no diagnostics emitted"
        );
    }

    #[test]
    fn collect_coverage_diagnostics_returns_empty_when_no_doctest_config() {
        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        write_pkg_with_missing_examples(&root);

        let files = vec![root.join("mypkg/__init__.py")];
        let cfg = crate::config::Config {
            rootdir: root,
            doctest: None,
            ..Default::default()
        };

        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            diags.is_empty(),
            "no [tool.oxitest.doctest] table ⇒ rule must not run — silent by default"
        );
    }

    fn doctest_only_cfg(
        scope: crate::config::DoctestScope,
        strict: Option<crate::config::StrictMode>,
    ) -> (crate::config::Config, tempfile::TempDir) {
        use crate::config::DoctestConfig;

        let tmp = tempfile::tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let cfg = crate::config::Config {
            rootdir: root,
            markers: crate::config::MarkerConfig {
                strict,
                ..Default::default()
            },
            doctest: Some(DoctestConfig {
                scope: Some(scope),
                ..Default::default()
            }),
            ..Default::default()
        };
        (cfg, tmp)
    }

    fn write_synth_missing_subject(cfg: &crate::config::Config) -> Vec<Utf8PathBuf> {
        use std::fs;
        fs::create_dir_all(cfg.rootdir.join("mypkg")).unwrap();
        fs::write(
            cfg.rootdir.join("mypkg/__init__.py"),
            "\"\"\"pkg.\"\"\"\n\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No Examples section.\"\"\"\n    pass\n",
        )
        .unwrap();
        vec![cfg.rootdir.join("mypkg/__init__.py")]
    }

    #[test]
    fn enforce_maps_to_warning_severity() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
        );
        let files = write_synth_missing_subject(&cfg);
        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            diags
                .iter()
                .filter(|d| d.context.as_ref() == "doctest.coverage")
                .all(|d| d.severity == crate::reporter::stats::DiagnosticSeverity::Warning),
            "strict=enforce maps to Warning severity — run continues, gaps are visible"
        );
    }

    /// Post-purge invariant (#1613): strict=enforce is purely warn-only for
    /// missing doctest coverage — no diagnostic may escalate to Error.
    #[test]
    fn enforce_is_purely_warn_only_after_purge() {
        use crate::reporter::stats::DiagnosticSeverity;
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
        );
        let files = write_synth_missing_subject(&cfg);
        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            !diags.is_empty(),
            "enforce mode with a missing public subject must still surface a diagnostic — silent-on-gap would defeat the rule"
        );
        assert!(
            diags
                .iter()
                .all(|d| d.severity != DiagnosticSeverity::Error),
            "post-purge invariant: no diagnostic may be Error under strict=enforce; got severities: {:?}",
            diags.iter().map(|d| &d.severity).collect::<Vec<_>>()
        );
    }

    #[test]
    fn missing_subject_under_abort_hard_fails() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
        );
        let files = write_synth_missing_subject(&cfg);
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let cov: Vec<_> = diags
            .iter()
            .filter(|d| d.context.as_ref() == "doctest.coverage")
            .collect();
        assert!(
            !cov.is_empty(),
            "missing subject under abort must produce a coverage diagnostic"
        );
        assert!(
            cov.iter()
                .all(|d| d.severity == crate::reporter::stats::DiagnosticSeverity::Error),
            "missing subject under abort must fire at Error severity"
        );
    }

    #[test]
    fn collect_coverage_diagnostics_excludes_test_files_from_public_subject_scan() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
        );
        use std::fs;
        // Non-test file with a missing subject
        fs::create_dir_all(cfg.rootdir.join("pkg")).unwrap();
        fs::write(cfg.rootdir.join("pkg/__init__.py"), "").unwrap();
        fs::write(
            cfg.rootdir.join("pkg/lib.py"),
            "\"\"\"lib.\"\"\"\n\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .unwrap();
        // Test file with what would look like a subject if we didn't exclude it
        fs::write(
            cfg.rootdir.join("test_stuff.py"),
            "\"\"\"tests.\"\"\"\n\ndef test_it():\n    \"\"\"No examples but this is a test.\"\"\"\n    pass\n",
        )
        .unwrap();
        let files = vec![
            cfg.rootdir.join("pkg/__init__.py"),
            cfg.rootdir.join("pkg/lib.py"),
            cfg.rootdir.join("test_stuff.py"),
        ];
        let diags = collect_coverage_diagnostics(&files, &cfg);
        // Should see a diagnostic for pkg.lib.foo but NOT for test_stuff.test_it.
        let has_lib_foo = diags
            .iter()
            .any(|d| d.message.contains("pkg.lib.foo") || d.message.contains("lib.foo"));
        let has_test_it = diags.iter().any(|d| d.message.contains("test_it"));
        assert!(
            has_lib_foo,
            "the non-test file's public subject must still be checked; got diags: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
        assert!(
            !has_test_it,
            "test_*.py files must be excluded from public-subject scanning — test functions aren't public API; got diags: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
    }

    #[test]
    fn conftest_py_excluded_from_public_subject_scanning() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
        );
        use std::fs;
        // Regular public module with a missing subject
        fs::create_dir_all(cfg.rootdir.join("mypkg")).unwrap();
        fs::write(cfg.rootdir.join("mypkg/__init__.py"), "").unwrap();
        fs::write(
            cfg.rootdir.join("mypkg/lib.py"),
            "\"\"\"lib.\"\"\"\n\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .unwrap();
        // Root conftest.py — must be excluded from scanning
        fs::write(
            cfg.rootdir.join("conftest.py"),
            "\"\"\"conftest.\"\"\"\n\ndef helper():\n    \"\"\"No examples but conftest is test infra.\"\"\"\n    pass\n",
        )
        .unwrap();
        // Nested conftest.py — also excluded regardless of depth
        fs::create_dir_all(cfg.rootdir.join("tests/integration")).unwrap();
        fs::write(
            cfg.rootdir.join("tests/integration/conftest.py"),
            "def inner_helper():\n    pass\n",
        )
        .unwrap();
        let files = vec![
            cfg.rootdir.join("mypkg/__init__.py"),
            cfg.rootdir.join("mypkg/lib.py"),
            cfg.rootdir.join("conftest.py"),
            cfg.rootdir.join("tests/integration/conftest.py"),
        ];
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let has_lib_foo = diags
            .iter()
            .any(|d| d.message.contains("pkg.lib.foo") || d.message.contains("lib.foo"));
        let has_helper = diags.iter().any(|d| d.message.contains("helper"));
        assert!(
            has_lib_foo,
            "regular public module subjects must still be checked; got: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
        assert!(
            !has_helper,
            "conftest.py definitions (at any nesting level) must be excluded from public-subject scanning — they are test infrastructure per pytest/oxitest convention; got: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
    }

    #[test]
    fn collect_coverage_diagnostics_respects_doctest_skip_prefix() {
        let (mut cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
        );
        if let Some(dt) = cfg.doctest.as_mut() {
            dt.skip = vec![crate::config::ScopeEntry::Prefix(Utf8PathBuf::from(
                "fixtures/",
            ))];
        }
        use std::fs;
        // Regular package
        fs::create_dir_all(cfg.rootdir.join("mypkg")).unwrap();
        fs::write(cfg.rootdir.join("mypkg/__init__.py"), "").unwrap();
        fs::write(
            cfg.rootdir.join("mypkg/lib.py"),
            "\"\"\"lib.\"\"\"\n\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .unwrap();
        // Skipped path
        fs::create_dir_all(cfg.rootdir.join("fixtures/sample")).unwrap();
        fs::write(cfg.rootdir.join("fixtures/sample/__init__.py"), "").unwrap();
        fs::write(
            cfg.rootdir.join("fixtures/sample/mod.py"),
            "\"\"\"skipped.\"\"\"\n\n__all__ = [\"bar\"]\n\ndef bar():\n    \"\"\"No examples.\"\"\"\n    pass\n",
        )
        .unwrap();

        let files = vec![
            cfg.rootdir.join("mypkg/__init__.py"),
            cfg.rootdir.join("mypkg/lib.py"),
            cfg.rootdir.join("fixtures/sample/__init__.py"),
            cfg.rootdir.join("fixtures/sample/mod.py"),
        ];
        let diags = collect_coverage_diagnostics(&files, &cfg);

        // The mypkg subject should still fire a diagnostic; the skipped fixture subject should not.
        let has_lib_foo = diags
            .iter()
            .any(|d| d.message.contains("mypkg.lib.foo") || d.message.contains("lib.foo"));
        let has_bar = diags.iter().any(|d| d.message.contains("bar"));
        assert!(
            has_lib_foo,
            "non-skipped subject must still produce a diagnostic; got: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
        assert!(
            !has_bar,
            "subject under a skipped prefix must be silently excluded; got: {:?}",
            diags.iter().map(|d| &d.message).collect::<Vec<_>>()
        );
    }

    #[test]
    fn walk_error_diagnostic_context_is_analysis_not_coverage() {
        // Focused test: ensure ALL AliasError variants emit at the analysis context.
        use crate::doctest::alias::AliasError;
        use crate::doctest::coverage::diagnostic_for_walk_error;
        use crate::doctest::subjects::Subject;
        use crate::reporter::stats::DiagnosticSeverity;
        let subj = Subject {
            public_id: "mypkg.foo".into(),
            name: "foo".into(),
            source: crate::doctest::subjects::SubjectSource::LocalDefinition,
            file: camino::Utf8PathBuf::from("mypkg/foo.py"),
            class_context: None,
        };
        let cases = [
            AliasError::Cycle {
                path: vec!["a".into(), "b".into()],
            },
            AliasError::UnknownTerminus { at: "b".into() },
            AliasError::ParseFailure {
                file: "x.py".into(),
            },
            AliasError::NameNotFound {
                module: "m".into(),
                name: "n".into(),
            },
            AliasError::ModuleFileNotFound { module: "m".into() },
        ];
        for err in &cases {
            let d = diagnostic_for_walk_error(&subj, err, None, DiagnosticSeverity::Error);
            assert_eq!(
                d.context.as_ref(),
                "doctest.coverage.analysis",
                "every AliasError variant must produce an analysis-context diagnostic so it is separated from coverage gaps; got context={} for error={:?}",
                d.context.as_ref(),
                err
            );
        }
    }

    #[test]
    fn split_coverage_diagnostics_separates_errors_from_pending() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        use std::sync::Arc;
        let entries = vec![
            DiagnosticEntry {
                severity: DiagnosticSeverity::Error,
                context: Arc::from("doctest.coverage"),
                message: "`mypkg.a` missing `Examples:` header".into(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Warning,
                context: Arc::from("doctest.coverage"),
                message: "`mypkg.b` missing `Examples:` header".into(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Notice,
                context: Arc::from("doctest.coverage"),
                message: "`mypkg.c` missing `Examples:` header".into(),
                file: None,
                lineno: None,
            },
        ];
        let (errors, pending) = split_coverage_diagnostics(entries);
        assert_eq!(
            errors.len(),
            1,
            "one Error entry ⇒ one CollectError promoted for hard-fail; got {} errors",
            errors.len()
        );
        assert_eq!(
            pending.len(),
            2,
            "Warning + Notice remain as pending diagnostics for the reporter to render; got {} pending",
            pending.len()
        );
        let err_msg = errors[0].to_string();
        assert!(
            err_msg.contains("mypkg.a"),
            "promoted CollectError carries the original diagnostic message so the user sees which subject failed; got: {err_msg}"
        );
    }

    #[test]
    fn stale_scope_entry_emits_warning_under_enforce() {
        use crate::config::{DoctestConfig, DoctestScope, ScopeEntry, StrictMode};

        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(StrictMode::Enforce);
        cfg.rootdir = Utf8PathBuf::from(".");
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::List(vec![ScopeEntry::File(
                Utf8PathBuf::from("nonexistent/mod.py"),
            )])),
            skip: vec![],
        });
        let diags = collect_coverage_diagnostics(&[], &cfg);
        let stale = diags
            .iter()
            .find(|d| d.context.as_ref() == "doctest.coverage.stale-scope");
        assert!(
            stale.is_some(),
            "stale scope entry must emit doctest.coverage.stale-scope diagnostic",
        );
        assert_eq!(
            stale.unwrap().severity,
            crate::reporter::stats::DiagnosticSeverity::Warning,
            "Warning under strict = enforce",
        );
    }

    #[test]
    fn stale_skip_entry_emits_diagnostic() {
        use crate::config::{DoctestConfig, DoctestScope, ScopeEntry, StrictMode};

        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(StrictMode::Enforce);
        cfg.rootdir = Utf8PathBuf::from(".");
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::Public),
            skip: vec![ScopeEntry::File(Utf8PathBuf::from("nonexistent/mod.py"))],
        });
        let diags = collect_coverage_diagnostics(&[], &cfg);
        let stale = diags
            .iter()
            .find(|d| d.context.as_ref() == "doctest.coverage.stale-skip");
        assert!(
            stale.is_some(),
            "stale skip entry must emit doctest.coverage.stale-skip diagnostic",
        );
    }

    /// Config for the stale-entry tests. `strict = abort`, one entry naming
    /// `entry_path`, placed in `scope` or `skip` per `as_scope`.
    ///
    /// `rootdir` must exist on disk: the staleness verdict resolves each entry
    /// against it, so a synthetic root would make every entry trivially
    /// "missing" and every assertion here would stop meaning anything.
    ///
    /// No `testpaths` and no `has_explicit_paths` -- `StalenessInputs` reads
    /// neither, which is the invariant ADR-0010 exists to hold.
    fn cfg_for_stale(
        rootdir: &Utf8Path,
        entry_path: &str,
        as_scope: bool,
    ) -> crate::config::Config {
        use crate::config::{DoctestConfig, DoctestScope, ScopeEntry, StrictMode};

        let entry = if entry_path.ends_with('/') {
            ScopeEntry::Prefix(Utf8PathBuf::from(entry_path))
        } else {
            ScopeEntry::File(Utf8PathBuf::from(entry_path))
        };
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(DoctestConfig {
            scope: if as_scope {
                Some(DoctestScope::List(vec![entry.clone()]))
            } else {
                Some(DoctestScope::Public)
            },
            skip: if as_scope { vec![] } else { vec![entry] },
        });
        cfg
    }

    /// [`cfg_for_stale`], plus an explicit declared test tree.
    ///
    /// `cfg_for_stale` leaves `declared_testpaths` empty, which
    /// `collector::coverage_roots` reads as "declared nothing" and answers with
    /// `[rootdir]` — under which every entry that exists is reachable.
    /// Reachability tests therefore need this variant; the plain one stays
    /// correct for every other staleness axis, which is why its six call sites
    /// are untouched.
    fn cfg_for_stale_declared(
        rootdir: &Utf8Path,
        entry_path: &str,
        as_scope: bool,
        declared: &[&str],
    ) -> crate::config::Config {
        let mut cfg = cfg_for_stale(rootdir, entry_path, as_scope);
        cfg.paths.declared_testpaths = declared.iter().map(Utf8PathBuf::from).collect();
        cfg
    }

    /// The stale diagnostics' messages, in order.
    ///
    /// The primitive both stale helpers are built on: [`stale_count`] is this
    /// list's length. A count cannot tell `MissingPath` from `Unreachable` —
    /// both are one diagnostic under the same context — so any test about
    /// *which* verdict fired, or about its wording, asserts on the text.
    fn stale_messages(cfg: &crate::config::Config, doctest_files: &[Utf8PathBuf]) -> Vec<String> {
        collect_coverage_diagnostics(doctest_files, cfg)
            .iter()
            .filter(|d| d.context.as_ref().starts_with("doctest.coverage.stale-"))
            .map(|d| d.message.clone())
            .collect()
    }

    /// Count stale diagnostics of either kind for `cfg`, scanning `doctest_files`.
    ///
    /// `doctest_files` are absolute paths; `collect_coverage_diagnostics` strips
    /// `rootdir` from them to build the scanned set. Pass an empty slice to
    /// model "nothing scanned".
    fn stale_count(cfg: &crate::config::Config, doctest_files: &[Utf8PathBuf]) -> usize {
        stale_messages(cfg, doctest_files).len()
    }

    #[test]
    fn stale_scope_entry_disjoint_from_the_declared_tree_is_stale() {
        // The silent false green this arm exists for: the entry exists, so the
        // path check passes it, and it is a File entry, so the old code called
        // it Fresh -- while the coverage walk never reaches its directory, so
        // the subject inside is never audited and the run exits 0.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        std::fs::create_dir_all(rootdir.join("src")).expect("create src");
        std::fs::write(rootdir.join("src/mod.py"), "def f():\n    pass\n").expect("write");
        std::fs::create_dir_all(rootdir.join("tests")).expect("create tests");
        let cfg = cfg_for_stale_declared(rootdir, "src/mod.py", true, &["tests"]);
        assert_eq!(
            stale_count(&cfg, &[]),
            1,
            "a scope entry outside the declared tree can never match under any \
             invocation, so leaving it silent tells the user their API is \
             audited when nothing ever reads it",
        );
    }

    #[test]
    fn stale_skip_entry_disjoint_from_the_declared_tree_is_stale() {
        // Parity with the scope side is a decision, not a side-effect, so it
        // needs its own test. Skip's failure is the quieter of the two -- the
        // diagnostic the user tried to silence still fires, so they find out --
        // which makes exempting it defensible enough that someone may try.
        // Nothing in the shared classifier would fail if they did.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        std::fs::create_dir_all(rootdir.join("src")).expect("create src");
        std::fs::write(rootdir.join("src/mod.py"), "def f():\n    pass\n").expect("write");
        std::fs::create_dir_all(rootdir.join("tests")).expect("create tests");
        let cfg = cfg_for_stale_declared(rootdir, "src/mod.py", false, &["tests"]);
        let messages = stale_messages(&cfg, &[]);
        assert_eq!(
            messages.len(),
            1,
            "an unreachable skip entry is as unmatchable as an unreachable \
             scope entry -- the private-module abstention is not a precedent \
             for it, because that one exists where the scan never read the file",
        );
        assert!(
            messages[0].starts_with("skip entry"),
            "the diagnostic must name which list the entry came from, or the \
             user cannot find it in pyproject.toml; got: {}",
            messages[0],
        );
        assert!(
            messages[0].contains("outside the declared test tree"),
            "skip must reach the same verdict as scope with the same wording -- \
             a second phrasing for one predicate is how the two drift apart; \
             got: {}",
            messages[0],
        );
    }

    #[test]
    fn stale_prefix_entry_containing_the_declared_tree_is_never_stale() {
        // The direction containment-only gets wrong. `src/` sits ABOVE the
        // declared root, so every file under `src/pkg` does start_with `src/`
        // and the entry matches -- reporting it stale is the "correct entry
        // reported stale" shape that reopened #1796 on attempt 3.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        std::fs::create_dir_all(rootdir.join("src/pkg")).expect("create src/pkg");
        let cfg = cfg_for_stale_declared(rootdir, "src/", true, &["src/pkg"]);
        assert_eq!(
            stale_count(&cfg, &[]),
            0,
            "a Prefix entry that CONTAINS the declared tree overlaps it and \
             matches every subject inside it -- only a symmetric test sees \
             this, and containment alone would fail a working config",
        );
    }

    #[test]
    fn stale_entry_both_missing_and_disjoint_reports_the_missing_path() {
        // Ordering. Both arms fire; the user must be sent to the filename,
        // which is the more fundamental and more certain fact, not to
        // testpaths -- otherwise they go hunting in the wrong file.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        std::fs::create_dir_all(rootdir.join("tests")).expect("create tests");
        // `src/nope.py` is neither on disk nor under the declared tree.
        let cfg = cfg_for_stale_declared(rootdir, "src/nope.py", true, &["tests"]);
        let messages = stale_messages(&cfg, &[]);
        assert_eq!(
            messages.len(),
            1,
            "one entry must yield one verdict, not one per failing axis",
        );
        assert!(
            messages[0].contains("names a path that does not exist"),
            "a mistyped path that is also outside the tree is a typo first -- \
             telling the user to add it to testpaths sends them to fix a \
             filename that will still be wrong afterwards; got: {}",
            messages[0],
        );
    }

    #[test]
    fn stale_scope_prefix_entry_naming_a_missing_directory_is_stale() {
        // The scope side, where a missed typo costs the most: every earlier
        // predicate had some shape of run under which it exempted this entry,
        // and a `scope` list that matches nothing disables coverage for every
        // subject rather than merely widening it.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        // `pkgg/helpers/` is the typo under test -- deliberately never created.
        let cfg = cfg_for_stale(rootdir, "pkgg/helpers/", true);
        assert_eq!(
            stale_count(&cfg, &[]),
            1,
            "a scope Prefix entry naming a directory that is not on disk is \
             precisely the typo the guard exists to catch -- exempting it turns \
             a hard-fail into a green run with nothing coverage-checked at all",
        );
    }

    #[test]
    fn stale_skip_entry_naming_a_missing_file_is_stale() {
        // The skip side, which fails less loudly than the scope side (the run
        // still errors, but on an unexplained missing-Examples violation).
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        // `nope/mod.py` is the missing skip entry under test -- never created.
        let cfg = cfg_for_stale(rootdir, "nope/mod.py", false);
        assert_eq!(
            stale_count(&cfg, &[]),
            1,
            "a missing path must name the mistyped skip entry rather than let \
             it silently widen coverage",
        );
    }

    #[test]
    fn stale_entry_naming_a_missing_path_is_stale_under_any_invocation() {
        // Integration tests run `oxitest <tmpdir>`: an explicit CLI path, which
        // three earlier predicates treated as a reason to abstain.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        // `src/nope.py` is the missing entry under test -- never created.
        let cfg = cfg_for_stale(rootdir, "src/nope.py", false);
        assert_eq!(
            stale_count(&cfg, &[]),
            1,
            "a path that is not on disk is a typo under every invocation shape \
             -- the verdict must not depend on how the run was narrowed, which \
             is exactly what let a mistyped entry pass CI green (#1796)",
        );
    }

    #[test]
    fn stale_scope_file_entry_that_exists_is_never_stale() {
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("mod.py")
            .write_str("def _private():\n    pass\n")
            .expect("write the scope entry's file");
        let cfg = cfg_for_stale(rootdir, "mod.py", true);
        assert_eq!(
            stale_count(&cfg, &[rootdir.join("mod.py")]),
            0,
            "a File entry whose path exists is never stale -- zero subjects is \
             routine (private-only modules, test_*.py, conftest.py) and is not \
             evidence of a typo, which is the mistake that reopened #1796 \
             three times",
        );
    }

    #[test]
    fn stale_prefix_entry_whose_directory_exists_is_never_stale() {
        // The literal shape from the #1796 report: `skip = ["python/tests/helpers/"]`
        // on a run that scanned nothing under it. Added because Mutation A
        // (existence check always fires) survived the rest of the suite --
        // the File case was pinned, the Prefix case was not.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("helpers/runners.py")
            .write_str("def build():\n    pass\n")
            .expect("populate the directory the skip entry names");
        let cfg = cfg_for_stale(rootdir, "helpers/", false);
        assert_eq!(
            stale_count(&cfg, &[]),
            0,
            "a directory that is on disk is not a typo, whatever this run \
             happened to scan -- firing here is the exact hard-fail every \
             narrowed run hit in #1796",
        );
    }

    #[test]
    fn stale_symbol_entry_that_matched_a_subject_is_never_stale() {
        // The hit path through `classify`: file scanned, symbol found. Added
        // alongside the Prefix case so a mutation that makes the existence
        // check unconditional cannot survive on the Symbol arm either.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("mod.py")
            .write_str("def thing():\n    pass\n")
            .expect("write the symbol entry's file");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![crate::config::ScopeEntry::Symbol {
                file: Utf8PathBuf::from("mod.py"),
                name: "thing".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[rootdir.join("mod.py")]),
            0,
            "an entry that did its job -- it skipped a real subject -- must \
             never be reported stale, or the only way to silence a coverage \
             violation would itself fail the run",
        );
    }

    #[test]
    fn stale_prefix_entry_naming_a_regular_file_is_stale() {
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("helpers")
            .write_str("not a directory")
            .expect("write a regular file where a directory is named");
        let cfg = cfg_for_stale(rootdir, "helpers/", false);
        assert_eq!(
            stale_count(&cfg, &[]),
            1,
            "Prefix entries keep their trailing slash, so POSIX rejects a \
             regular file with ENOTDIR and `exists()` covers the type check -- \
             this pins behaviour that is otherwise accidental and would \
             silently regress if the slash were normalised away",
        );
    }

    #[test]
    fn stale_symbol_entry_abstains_when_its_file_was_not_scanned() {
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("mod.py")
            .write_str("def thing():\n    pass\n")
            .expect("write the symbol entry's file");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![crate::config::ScopeEntry::Symbol {
                file: Utf8PathBuf::from("mod.py"),
                name: "thing".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[]),
            0,
            "a run that never scanned the file has no evidence about the \
             symbol, so it must abstain -- guessing here is what made every \
             narrowed run hard-fail in #1796",
        );
    }

    #[test]
    fn stale_symbol_entry_abstains_when_the_privacy_gate_skipped_its_file() {
        // The #1796 shape this plan fixes: the file is offered to the scanner,
        // but its dotted module path has an underscore-prefixed component, so
        // the scanner's privacy gate drops it before `parse_file` ever runs.
        // Offered is not parsed, and only parsed is evidence.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        // `__init__.py` is load-bearing, not decoration: without it `_internal`
        // is not a package, so the dotted path for `mod.py` walks no further up
        // than `mod` -- public -- and the privacy gate never engages.
        root.child("_internal/__init__.py")
            .write_str("")
            .expect("make the private directory an importable package");
        root.child("_internal/mod.py")
            .write_str("def helper():\n    pass\n")
            .expect("write a real function inside a private module");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            // Public scope on purpose: under `List`, an explicitly-named file
            // bypasses the privacy gate, so the bug cannot be expressed there.
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![crate::config::ScopeEntry::Symbol {
                file: Utf8PathBuf::from("_internal/mod.py"),
                name: "helper".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[rootdir.join("_internal/mod.py")]),
            0,
            "the scanner's privacy gate skips `_`-prefixed module paths before \
             parsing, so this run never opened the file and knows nothing about \
             `helper` -- reporting it stale tells the user to check a symbol \
             name that is correct, and under strict = abort fails CI on a wrong \
             diagnosis (#1796)",
        );
    }

    #[test]
    fn stale_symbol_entry_naming_a_missing_function_is_stale() {
        // The `NoSubjects` verdict itself: file on disk, file scanned, named
        // symbol absent. Every other `stale_count` assertion either uses an
        // empty scanned set (static rule only) or expects zero, so before this
        // test a `classify` that returned `Fresh` here passed the whole suite.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        // Documented on purpose: a bare `def` would raise a missing-`Examples:`
        // coverage violation, and the fixture must isolate the stale verdict.
        let module_source = concat!(
            "def real_thing():\n",
            "    \"\"\"The subject that does exist.\n",
            "\n",
            "    Examples:\n",
            "        >>> real_thing()\n",
            "    \"\"\"\n",
        );
        root.child("mod.py")
            .write_str(module_source)
            .expect("write the symbol entry's file");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            // `typo_thing` is the typo under test -- `mod.py` defines
            // `real_thing`, so nothing can ever match this entry.
            skip: vec![crate::config::ScopeEntry::Symbol {
                file: Utf8PathBuf::from("mod.py"),
                name: "typo_thing".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[rootdir.join("mod.py")]),
            1,
            "sub-file typo detection is the entire reason ADR-0010 kept a \
             hit-based half instead of the simpler pure-static design it \
             measured and rejected -- if this entry passes silently, the \
             hit-based half is dead weight and the ADR's rationale is void",
        );
    }

    #[test]
    fn stale_member_entry_naming_a_missing_method_is_stale() {
        // First unit coverage of `Member` in any form -- until now it was
        // exercised only by the slower Python integration suite. `Member`
        // shares `classify`'s arm with `Symbol` today, so this guards against
        // a future `cls`-aware split silently losing the verdict.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        let module_source = concat!(
            "class Widget:\n",
            "    \"\"\"The class that does exist.\n",
            "\n",
            "    Examples:\n",
            "        >>> Widget()\n",
            "    \"\"\"\n",
            "\n",
            "    def real_method(self):\n",
            "        \"\"\"The method that does exist.\n",
            "\n",
            "        Examples:\n",
            "            >>> Widget().real_method()\n",
            "        \"\"\"\n",
        );
        root.child("mod.py")
            .write_str(module_source)
            .expect("write the member entry's file");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            // `typo_method` is the typo under test -- `Widget` defines
            // `real_method`, so nothing can ever match this entry.
            skip: vec![crate::config::ScopeEntry::Member {
                file: Utf8PathBuf::from("mod.py"),
                cls: "Widget".to_string(),
                name: "typo_method".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[rootdir.join("mod.py")]),
            1,
            "a mistyped method name is exactly as unfixable as a mistyped \
             function name, so `Member` must reach the same verdict `Symbol` \
             does -- letting it pass would leave per-method opt-in as the one \
             corner of the grammar where typos stay invisible",
        );
    }

    #[test]
    fn stale_member_entry_abstains_when_its_file_was_not_scanned() {
        // The abstention half of the `Member` pair. Without it, a mutation
        // that made the `Member` verdict unconditional would be caught only on
        // the `Symbol` arm, which is precisely the divergence the pair exists
        // to detect once `Member` stops sharing `classify`'s arm with `Symbol`.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        let module_source = concat!(
            "class Widget:\n",
            "    \"\"\"The class that does exist.\n",
            "\n",
            "    Examples:\n",
            "        >>> Widget()\n",
            "    \"\"\"\n",
            "\n",
            "    def real_method(self):\n",
            "        \"\"\"The method that does exist.\n",
            "\n",
            "        Examples:\n",
            "            >>> Widget().real_method()\n",
            "        \"\"\"\n",
        );
        root.child("mod.py")
            .write_str(module_source)
            .expect("write the member entry's file");
        // Built inline rather than via `cfg_for_stale`: the fixture's entry is
        // overwritten wholesale below, so passing it a path would be dead.
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![crate::config::ScopeEntry::Member {
                file: Utf8PathBuf::from("mod.py"),
                cls: "Widget".to_string(),
                name: "typo_method".to_string(),
            }],
        });
        assert_eq!(
            stale_count(&cfg, &[]),
            0,
            "a run that never scanned the file has no evidence about the \
             method either -- `Member` must abstain on the same terms as \
             `Symbol`, or narrowed runs hard-fail all over again (#1796)",
        );
    }

    // ── parse-error diagnostics (#1800) ─────────────────────────────────────
    //
    // Real-files style, same as the `cfg_for_stale` family: every fixture
    // writes the broken file to disk and drives the full
    // `collect_coverage_diagnostics` path, so a hardwired predicate cannot
    // pass (the ADR-0010 "must kill mutants" rule).

    /// Count diagnostics carrying the parse-error context for *cfg*.
    fn parse_error_count(cfg: &crate::config::Config, doctest_files: &[Utf8PathBuf]) -> usize {
        collect_coverage_diagnostics(doctest_files, cfg)
            .iter()
            .filter(|diag| diag.context.as_ref() == "doctest.coverage.parse-error")
            .count()
    }

    #[test]
    fn unparsable_file_in_scan_set_emits_parse_error_diagnostic() {
        // Before #1800 this was a bare `continue`: the file vanished from
        // coverage auditing with no diagnostic naming it.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("broken.py")
            .write_str("def broken(:\n    pass\n")
            .expect("write a file with a syntax error");
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Enforce);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![],
        });

        let diags = collect_coverage_diagnostics(&[rootdir.join("broken.py")], &cfg);

        let parse_error = diags
            .iter()
            .find(|diag| diag.context.as_ref() == "doctest.coverage.parse-error");
        assert!(
            parse_error.is_some(),
            "a file the scanner tried to read but could not parse must produce \
             its own diagnostic -- silently dropping it removes the module from \
             coverage auditing with zero evidence anything is wrong (#1800); \
             got: {diags:?}",
        );
        let diag = parse_error.expect("checked above");
        assert!(
            diag.message.contains("broken.py"),
            "the diagnostic must name the file so the user knows what to fix; \
             got: {}",
            diag.message,
        );
        assert_eq!(
            diag.severity,
            crate::reporter::stats::DiagnosticSeverity::Warning,
            "severity rides the global strict dial exactly like the other \
             coverage diagnostics -- enforce means Warning",
        );
    }

    #[test]
    fn skip_symbol_entry_reports_parse_failure_when_file_unparsable() {
        // Half 2 of #1800: the entry names a symbol the run holds no evidence
        // about because the parse failed. Abstaining (the pre-#1800 behaviour)
        // leaves the entry as invisible dead config; a NoSubjects verdict
        // would be a wrong diagnosis (#1796's shape). It must report the
        // parse failure itself.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("broken.py")
            .write_str("def broken(:\n    pass\n")
            .expect("write a file with a syntax error");
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![crate::config::ScopeEntry::Symbol {
                file: Utf8PathBuf::from("broken.py"),
                name: "helper".to_string(),
            }],
        });

        let diags = collect_coverage_diagnostics(&[rootdir.join("broken.py")], &cfg);

        let entry_report = diags.iter().find(|diag| {
            diag.context.as_ref() == "doctest.coverage.parse-error"
                && diag.message.contains("skip entry")
        });
        assert!(
            entry_report.is_some(),
            "a skip entry naming a symbol in an offered-but-unparsable file \
             must report the parse failure instead of abstaining -- the scan \
             was asked to read the file and could not (#1800); got: {diags:?}",
        );
        assert!(
            entry_report
                .expect("checked above")
                .message
                .contains("broken.py::helper"),
            "the entry-level report must render the full entry so the user \
             can find it in their config",
        );
        assert!(
            !diags
                .iter()
                .any(|diag| diag.context.as_ref() == "doctest.coverage.stale-skip"),
            "a parse failure is not a staleness verdict -- emitting stale-skip \
             here would tell the user to check a symbol name the run never \
             read, the wrong-diagnosis shape of #1796",
        );
    }

    #[test]
    fn scope_symbol_entry_reports_parse_failure_when_file_unparsable() {
        // The scope side of half 2: a list-form entry explicitly opted the
        // file in, so the user believes the symbol is being coverage-checked.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("broken.py")
            .write_str("def broken(:\n    pass\n")
            .expect("write a file with a syntax error");
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::List(vec![
                crate::config::ScopeEntry::Symbol {
                    file: Utf8PathBuf::from("broken.py"),
                    name: "helper".to_string(),
                },
            ])),
            skip: vec![],
        });

        let diags = collect_coverage_diagnostics(&[rootdir.join("broken.py")], &cfg);

        assert!(
            diags.iter().any(|diag| {
                diag.context.as_ref() == "doctest.coverage.parse-error"
                    && diag.message.contains("scope entry")
                    && diag.message.contains("broken.py::helper")
            }),
            "a scope entry into an unparsable file must be told the file \
             could not be read -- abstaining leaves coverage silently \
             unenforced for a symbol the user explicitly asked about (#1800); \
             got: {diags:?}",
        );
    }

    #[test]
    fn unparsable_file_under_norecursedirs_emits_no_parse_error() {
        // Exclusion pin: `norecursedirs` prunes the file in
        // `collect_coverage_diagnostics` before `rel_files` ever reaches the
        // parse loop, so the scanner never asked for it. This is what keeps
        // deliberately-invalid fixture files legal in a strict repo -- the
        // noise objection that made option 1 controversial in #1800.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("fixtures/broken.py")
            .write_str("def broken(:\n    pass\n")
            .expect("write a broken file inside an excluded directory");
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.paths.norecursedirs = vec!["fixtures".to_string()];
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![],
        });

        assert_eq!(
            parse_error_count(&cfg, &[rootdir.join("fixtures/broken.py")]),
            0,
            "a file pruned by norecursedirs was never offered to the parser, \
             so it must not be diagnosed -- only files the scanner genuinely \
             tried to read may produce the parse-error diagnostic (#1800)",
        );
    }

    #[test]
    fn privacy_gated_unparsable_file_emits_no_parse_error() {
        // The scanner's own privacy gate also runs before `parse_file`: an
        // underscore-prefixed module path under Public scope is withheld, not
        // offered, so a syntax error inside it stays invisible -- same terms
        // as every other pre-parse pruning.
        let root = assert_fs::TempDir::new().expect("tempdir");
        let rootdir = Utf8Path::from_path(root.path()).expect("utf8 tempdir");
        root.child("_internal/__init__.py")
            .write_str("")
            .expect("make the private directory an importable package");
        root.child("_internal/broken.py")
            .write_str("def broken(:\n    pass\n")
            .expect("write a broken file inside a private module path");
        let mut cfg = crate::config::Config::default();
        cfg.markers.strict = Some(crate::config::StrictMode::Abort);
        cfg.rootdir = rootdir.to_owned();
        cfg.doctest = Some(crate::config::DoctestConfig {
            scope: Some(crate::config::DoctestScope::Public),
            skip: vec![],
        });

        assert_eq!(
            parse_error_count(&cfg, &[rootdir.join("_internal/broken.py")]),
            0,
            "the privacy gate withholds `_`-prefixed module paths before the \
             parse ever runs -- the scan never attempted the read, so there is \
             no parse failure to report (#1800)",
        );
    }

    #[test]
    fn split_coverage_diagnostics_parse_error_hard_fails_under_abort() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        use std::sync::Arc;

        let diag = DiagnosticEntry {
            severity: DiagnosticSeverity::Error,
            context: Arc::from("doctest.coverage.parse-error"),
            message: "`mypkg/broken.py` could not be parsed".to_string(),
            file: Some(Utf8PathBuf::from("mypkg/broken.py")),
            lineno: Some(crate::types::LineNo::new(1)),
        };

        let (errors, pending) = split_coverage_diagnostics(vec![diag]);

        assert_eq!(
            errors.len(),
            1,
            "a parse-error Error must promote to CollectError under abort -- \
             otherwise strict = abort users get a warning-shaped line for a \
             module that silently dropped out of coverage auditing (#1800)",
        );
        assert!(
            pending.is_empty(),
            "no pending diagnostic left behind when a parse-error Error is \
             promoted -- double-reporting the same finding would be noise",
        );
    }

    #[test]
    fn stale_entries_promote_to_collect_error_under_abort() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        use std::sync::Arc;

        let d = DiagnosticEntry {
            severity: DiagnosticSeverity::Error,
            context: Arc::from("doctest.coverage.stale-scope"),
            message: "scope entry 'x' matched no coverage subjects".to_string(),
            file: None,
            lineno: None,
        };
        let (errors, pending) = split_coverage_diagnostics(vec![d]);
        assert_eq!(
            errors.len(),
            1,
            "stale-scope Error must promote to CollectError under abort — otherwise typos silently bypass coverage under strict",
        );
        assert!(
            pending.is_empty(),
            "no pending diagnostic left behind when a stale-scope Error is promoted",
        );
    }

    #[test]
    fn split_coverage_diagnostics_analysis_error_hard_fails_under_abort() {
        // Under strict=abort, an analysis-context Error means "the scanner cannot
        // verify coverage for this subject" — semantically a coverage failure.
        // Both the coverage gap and the analysis error must hard-fail.
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        use std::sync::Arc;
        let entries = vec![
            DiagnosticEntry {
                severity: DiagnosticSeverity::Error,
                context: Arc::from("doctest.coverage.analysis"),
                message: "`mypkg.x` alias target module `mypkg._impl` file not found".into(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Error,
                context: Arc::from("doctest.coverage"),
                message: "`mypkg.y` missing `Examples:` header".into(),
                file: None,
                lineno: None,
            },
        ];
        let (errors, pending) = split_coverage_diagnostics(entries);
        assert_eq!(
            errors.len(),
            2,
            "both coverage-gap and analysis-error Errors must hard-fail under abort; got {} errors",
            errors.len()
        );
        assert_eq!(
            pending.len(),
            0,
            "no pending diagnostics — all Errors promote to hard-fail; got {} pending",
            pending.len()
        );
        let msgs: Vec<String> = errors.iter().map(|e| e.to_string()).collect();
        assert!(
            msgs.iter().any(|m| m.contains("mypkg.x")),
            "the analysis-error hard-fail must name the scanner failure; got: {msgs:?}"
        );
        assert!(
            msgs.iter().any(|m| m.contains("mypkg.y")),
            "the coverage-gap hard-fail must name the missing subject; got: {msgs:?}"
        );
    }

    // ── HomeKind (#1717) ─────────────────────────────────────────────────────

    #[test]
    fn a_plugin_home_carries_no_tree_root() {
        let anchor = Utf8PathBuf::from("/site-packages/oxi_pg");
        let path = anchor.join("__fixtures__.py");
        let autouse: Vec<String> = Vec::new();

        let home = DeclarationHome {
            path: &path,
            anchor: &anchor,
            file: HomeFile::Fixtures,
            kind: HomeKind::Plugin {
                plugin_module: "oxi_pg",
                namespace: "postgres",
                autouse: &autouse,
            },
        };

        assert!(
            matches!(home.kind, HomeKind::Plugin { .. }),
            "a plugin home must not be a User home: User carries tree_root, \
             which drives ADR-0009 Rule 4's rootdir check, and a plugin \
             package has no place in the user's test tree to compare against \
             — every lifetime=\"process\" declaration in it would be refused"
        );
    }

    #[test]
    fn a_user_home_keeps_its_tree_root() {
        let anchor = Utf8PathBuf::from("/proj/tests");
        let path = anchor.join("__fixtures__.py");
        let root = Utf8PathBuf::from("/proj/tests");

        let home = DeclarationHome {
            path: &path,
            anchor: &anchor,
            file: HomeFile::Fixtures,
            kind: HomeKind::User {
                tree_root: Some(&root),
                root_provenance: RootProvenance::Declared,
            },
        };

        let HomeKind::User { tree_root, .. } = home.kind else {
            panic!("a user home must stay a User home")
        };
        assert_eq!(
            tree_root,
            Some(root.as_path()),
            "Rule 4 compares tree_root against the anchor to decide whether a \
             directory is the rootdir package; losing it here would refuse \
             every process-lifetime declaration in the user's own tree"
        );
    }
}

#[cfg(test)]
mod file_prescreen_tests {
    use super::file_could_match;
    use crate::config::ScopeEntry;
    use camino::Utf8PathBuf;

    #[test]
    fn prefix_entry_matches_nested_files() {
        let entries = vec![ScopeEntry::Prefix(Utf8PathBuf::from("src/pkg/"))];
        let rel = Utf8PathBuf::from("src/pkg/sub/mod.py");
        assert!(
            file_could_match(&rel, &entries),
            "Prefix entry with trailing / matches any file under the directory",
        );
    }

    #[test]
    fn prefix_entry_rejects_sibling_files() {
        let entries = vec![ScopeEntry::Prefix(Utf8PathBuf::from("src/pkg/"))];
        let rel = Utf8PathBuf::from("src/other/mod.py");
        assert!(
            !file_could_match(&rel, &entries),
            "Prefix does not match siblings — starts_with semantics",
        );
    }

    #[test]
    fn file_entry_matches_only_exact_path() {
        let entries = vec![ScopeEntry::File(Utf8PathBuf::from("src/mod.py"))];
        assert!(
            file_could_match(&Utf8PathBuf::from("src/mod.py"), &entries),
            "File entry matches exact path",
        );
        assert!(
            !file_could_match(&Utf8PathBuf::from("src/mod2.py"), &entries),
            "File entry rejects different filename",
        );
        assert!(
            !file_could_match(&Utf8PathBuf::from("src/sub/mod.py"), &entries),
            "File entry rejects same filename in different directory",
        );
    }

    #[test]
    fn symbol_entry_matches_files_by_path() {
        let entries = vec![ScopeEntry::Symbol {
            file: Utf8PathBuf::from("src/mod.py"),
            name: "foo".to_string(),
        }];
        assert!(
            file_could_match(&Utf8PathBuf::from("src/mod.py"), &entries),
            "Symbol entry matches its file at prescreen — subject-level filter narrows to the symbol",
        );
    }

    #[test]
    fn multiple_entries_union() {
        let entries = vec![
            ScopeEntry::File(Utf8PathBuf::from("src/a.py")),
            ScopeEntry::Prefix(Utf8PathBuf::from("lib/")),
        ];
        assert!(
            file_could_match(&Utf8PathBuf::from("src/a.py"), &entries),
            "union: file entry matches its exact file",
        );
        assert!(
            file_could_match(&Utf8PathBuf::from("lib/x.py"), &entries),
            "union: prefix entry matches nested file",
        );
        assert!(
            !file_could_match(&Utf8PathBuf::from("src/b.py"), &entries),
            "union: file not matched by any entry is excluded",
        );
    }
}
