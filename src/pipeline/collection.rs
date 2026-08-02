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
pub(crate) fn file_could_match(
    rel: &camino::Utf8Path,
    entries: &[crate::config::ScopeEntry],
) -> bool {
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
pub(crate) struct FileProfile {
    pub(super) path: Utf8PathBuf,
    pub(super) prescan_us: u64,
    pub(super) collection_us: u64,
    /// True if this file was skipped by lazy collection (not imported).
    pub(super) lazy_skipped: bool,
}

/// Aggregate collection timing profile.
#[derive(Debug, Default)]
pub(crate) struct CollectionProfile {
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

    let file_count = profile.files.len();
    writeln!(
        out,
        "Collection profile ({file_count} files, {total_ms:.0}ms total):"
    )
    .unwrap();
    writeln!(out, "  prescan:    {prescan_ms:.0}ms ({prescan_pct:.1}%)").unwrap();
    writeln!(
        out,
        "  collection: {collection_ms:.0}ms ({collection_pct:.1}%)"
    )
    .unwrap();
    writeln!(out, "  other:      {other_ms:.0}ms ({other_pct:.1}%)").unwrap();

    let lazy_count = profile.files.iter().filter(|f| f.lazy_skipped).count();
    let eager_count = file_count - lazy_count;
    if lazy_count > 0 || eager_count < file_count {
        writeln!(
            out,
            "  lazy: {lazy_count} files skipped, eager: {eager_count} files imported"
        )
        .unwrap();
    }

    // Top 5 slowest files
    let mut sorted: Vec<&FileProfile> = profile.files.iter().collect();
    sorted.sort_by_key(|f| std::cmp::Reverse(f.prescan_us + f.collection_us));
    let has_slow = sorted
        .first()
        .is_some_and(|f| f.prescan_us + f.collection_us > 0);
    if has_slow {
        writeln!(out).unwrap();
        writeln!(out, "Slowest files:").unwrap();
        for fp in sorted.iter().take(5) {
            let file_ms = (fp.prescan_us + fp.collection_us) as f64 / 1000.0;
            let file_pct = if total_ms > 0.0 {
                file_ms / total_ms * 100.0
            } else {
                0.0
            };
            writeln!(out, "  {}    {file_ms:.0}ms ({file_pct:.1}%)", fp.path).unwrap();
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
}

/// The top of the collected test tree — the deepest directory that contains
/// every collected test file.
///
/// This is ADR-0009's "rootdir package". It is derived from the files actually
/// collected rather than from `testpaths`, because a positional path argument
/// overrides `testpaths` (`config/merge.rs`): `oxitest tests/` and a bare
/// `oxitest` run from inside `tests/` would otherwise disagree about which
/// directory is the root, making the same `lifetime="session"` declaration
/// legal or illegal depending on the caller's shell.
///
/// Returns `None` when nothing was collected, in which case there is no tree
/// and no declaration to place inside it.
fn test_tree_root(test_files: &[camino::Utf8PathBuf]) -> Option<camino::Utf8PathBuf> {
    let mut dirs = test_files.iter().filter_map(|file| file.parent());
    let first = dirs.next()?;
    Some(dirs.fold(first.to_owned(), |common, dir| {
        common
            .ancestors()
            .find(|candidate| dir.starts_with(candidate))
            .unwrap_or(camino::Utf8Path::new(""))
            .to_owned()
    }))
}

/// Reject inline declarations whose lifetime exceeds `module` (ADR-0009 Rule 4).
///
/// This is the second, independent cap axis. The rule enforced in
/// [`register_declaration_home`] is about **location** — `session` needs the
/// rootdir package — and it never sees a test file. This one is about the
/// **kind** of declaration home: inline caps at `module` wherever the file sits,
/// including at the rootdir package, where the location rule alone would permit
/// `session`.
///
/// Naming the sibling `__fixtures__.py` matters. A hint that only says "move it
/// elsewhere" is unusable, because the user cannot derive the target — the
/// lesson from #1711's review.
fn reject_inline_lifetime_over_cap(
    test_file: &camino::Utf8Path,
    declarations: &[crate::prescan::PrescanDeclaration],
) -> Vec<types::CollectError> {
    // Hoisted: the target file depends on the test file, not on the declaration,
    // so recomputing it per offender would be wasted work in the one case that
    // has more than one.
    let home: String = test_file.parent().map_or_else(
        || "__fixtures__.py".to_owned(),
        |dir| dir.join("__fixtures__.py").to_string(),
    );
    declarations
        .iter()
        .filter(|decl| {
            decl.lifetime == crate::prescan::LIFETIME_PACKAGE
                || decl.lifetime == crate::prescan::LIFETIME_SESSION
        })
        .map(|decl| {
            types::CollectError::PyError(format!(
                "{} in {test_file} declares lifetime=\"{}\", but a fixture \
                 declared inline in a test file is capped at \
                 lifetime=\"module\".\n\
                 An inline fixture is anchored to its own module, so a lifetime \
                 wider than the module would outlive the only scope that can see \
                 it.\n\
                 Hint: drop to lifetime=\"module\", or move the declaration to \
                 {home} to keep lifetime=\"{}\".",
                decl.fn_name, decl.lifetime, decl.lifetime,
            ))
        })
        .collect()
}

/// One declaration-home file and where it sits in the collected test tree.
///
/// Grouped rather than passed loose: the four travel together and mean nothing
/// apart, and naming them at the call site is what keeps two `Utf8Path`s and a
/// bare `bool` from being swappable by accident.
struct DeclarationHome<'a> {
    /// The declaration file itself — `__fixtures__.py` or `__init__.py`.
    path: &'a camino::Utf8Path,
    /// The directory that owns it; the anchor of everything declared inside.
    anchor: &'a camino::Utf8Path,
    /// Whether the filename is one oxitest owns. Gates the mistyped-alias hint.
    reserved: bool,
    /// Top of the collected test tree. Equal to `anchor` exactly when this home
    /// is a *rootdir package*, the only place `lifetime="session"` may be
    /// declared (ADR-0009 Rule 4).
    tree_root: Option<&'a camino::Utf8Path>,
}

/// Prescan one declaration-home file and register whatever it declares.
///
/// `home.reserved` says whether the filename is one oxitest owns. It gates a
/// single diagnostic: in a reserved file (`__fixtures__.py`) a decorated
/// top-level function that is *not* a recognized `@oxi.fixture` almost
/// certainly means a mistyped import alias, and saying so saves a confusing
/// fixture-not-found at test time. In `__init__.py` the same shape is ordinary
/// Python — decorators belong there for reasons that have nothing to do with
/// oxitest — so the hint would fire on well-formed packages and is suppressed.
fn register_declaration_home(
    py: pyo3::Python<'_>,
    session: &bridge::FixtureSession,
    home: &DeclarationHome<'_>,
    errors: &mut Vec<types::CollectError>,
    fixture_modules: &mut Vec<types::FixtureModule>,
) {
    let DeclarationHome {
        path,
        anchor,
        reserved,
        tree_root,
    } = *home;
    match crate::prescan::prescan_fixture_module(path) {
        crate::prescan::PrescanFixtureResult::HasFixtures(payload) => {
            let session_obj = session.as_py_object(py);
            if let Err(e) = bridge::register_fixture_module_for_path(py, session_obj, path, anchor)
            {
                errors.push(e);
            }
            // Read off the AST rather than from the registered session: the
            // scheduler decision must hold even when registration above failed,
            // and it has to be available before any Python runs.
            let package_declarations = payload
                .declarations
                .iter()
                .filter(|d| d.lifetime == crate::prescan::LIFETIME_PACKAGE)
                .map(|d| types::PackageDeclaration {
                    fn_name: d.fn_name.clone(),
                    lineno: d.lineno,
                })
                .collect();
            // ADR-0009 Rule 4: `session` is legal only in a rootdir package. It
            // is the tier that does not constrain the scheduler, so anchoring it
            // below the root attaches it to no boundary at all.
            //
            // Read off the AST for the same reason the scheduler decision above
            // is: it must hold even when registration failed, and be available
            // before any Python runs. Per declaration rather than per file, so
            // two offending declarations produce two diagnostics.
            let is_rootdir_package = tree_root == Some(anchor);
            if !is_rootdir_package {
                // Name the directory that *is* the root. "Move it to a rootdir
                // package" is unactionable on its own: the root is derived from
                // the collected tree, so the user cannot read it off their
                // config. Absent only when nothing was collected, in which case
                // this loop cannot produce a diagnostic anyway.
                let root_hint = tree_root.map_or_else(
                    || "the root of your test tree".to_owned(),
                    |root| root.to_string(),
                );
                errors.extend(
                    payload
                        .declarations
                        .iter()
                        .filter(|decl| decl.lifetime == crate::prescan::LIFETIME_SESSION)
                        .map(|decl| {
                            types::CollectError::PyError(format!(
                                "{} in {path} declares lifetime=\"session\", but \
                                 {anchor} is not a rootdir package.\n\
                                 session is the tier that does not constrain the \
                                 scheduler, so anchoring it below the root attaches \
                                 it to no boundary at all.\n\
                                 Hint: move the declaration to {root_hint}, or drop \
                                 to lifetime=\"package\" to scope it to {anchor}, or \
                                 lifetime=\"module\" for per-file.",
                                decl.fn_name,
                            ))
                        }),
                );
            }
            // Recorded even when registration failed above: the serial session
            // and a worker session are independent, so a failure here says
            // nothing about whether the worker will succeed. It reports its own
            // diagnostic.
            fixture_modules.push(types::FixtureModule {
                module: path.to_owned(),
                anchor: anchor.to_owned(),
                package_declarations,
            });
        }
        crate::prescan::PrescanFixtureResult::Unavailable => {
            // The file exists but could not be parsed (syntax error, I/O error).
            // Surface a clear collection error naming it, rather than a silent
            // fixture-not-found at test time.
            tracing::warn!(path = path.as_str(), "prescan: file could not be parsed");
            errors.push(types::CollectError::PyError(format!(
                "{path} could not be parsed (syntax error or I/O error); \
                 fixtures in this file will not be registered",
            )));
        }
        crate::prescan::PrescanFixtureResult::NoFixtures(payload) => {
            if reserved && payload.has_unrecognized_decorated_functions {
                tracing::warn!(
                    path = path.as_str(),
                    "prescan: reserved file has @-decorated functions but no \
                     recognized @oxi.fixture calls — check import alias"
                );
                errors.push(types::CollectError::PyError(format!(
                    "{path} has @-decorated functions but no recognized \
                     @oxi.fixture declarations. Only `import oxitest as oxi`, \
                     `import oxitest`, or `from oxitest import fixture` are \
                     recognized as import aliases for oxitest.",
                )));
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
    // Computed once over the whole file list rather than per directory: the
    // rootdir package is a property of the run, and the per-file loop below
    // visits directories in collection order, not depth order.
    let tree_root = test_tree_root(test_files);

    // Deduplicate fixture-module registrations: multiple test files in the
    // same directory all share the same __fixtures__.py. Register once per dir.
    let mut registered_fixture_dirs: std::collections::HashSet<camino::Utf8PathBuf> =
        std::collections::HashSet::new();
    // The same set, as (module, anchor) pairs, for the parallel path: workers
    // build their own sessions and must register exactly what the serial path
    // registered here. Deriving it independently over there would mean two
    // places deciding what counts as a registrable fixture module.
    let mut fixture_modules: Vec<types::FixtureModule> = Vec::new();

    for file in test_files {
        // Pre-scan: skip files with no test functions.
        // When collecting violations (strict mode), keep the parsed AST
        // for bare-assert detection to avoid double-parsing.
        let prescan_start = std::time::Instant::now();
        let prescan = crate::prescan::prescan_with_ast(file, collect_violations);
        let prescan_us = prescan_start.elapsed().as_micros() as u64;

        // `declares_inline_fixtures` gates the item cache below — see there.
        let (declares_inline_fixtures, cached_ast) = match prescan {
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
                errors.extend(reject_inline_lifetime_over_cap(file, &p.declarations));
                let ast = if collect_violations && !p.source.is_empty() {
                    Some((p.source, p.stmts))
                } else {
                    None
                };
                (!p.declarations.is_empty(), ast)
            }
        };

        // Fixture-module registration: if this file's directory contains a
        // __fixtures__.py whose prescan found @oxi.fixture declarations,
        // register them into the session registry before collecting tests.
        // One registration per directory (multiple test files share the same
        // __fixtures__.py — the HashSet deduplicates).
        //
        // IMPORTANT: this must run BEFORE the cache-hit check below. On warm
        // cache runs the per-file `continue` fires before any code below it,
        // so any registration placed after the cache check is silently skipped
        // for cached modules (HIGH-1 fix).
        if let Some(parent_dir) = file.parent()
            && !registered_fixture_dirs.contains(parent_dir)
        {
            // Both declaration homes for this directory, per ADR-0009's
            // file-convention table. `__fixtures__.py` is reserved and holds any
            // lifetime; `__init__.py` is an ordinary package-init file that may
            // also host declarations (package lifetime is the recommended use).
            for (name, reserved) in [("__fixtures__.py", true), ("__init__.py", false)] {
                let path = parent_dir.join(name);
                if path.exists() {
                    register_declaration_home(
                        py,
                        session,
                        &DeclarationHome {
                            path: &path,
                            anchor: parent_dir,
                            reserved,
                            tree_root: tree_root.as_deref(),
                        },
                        &mut errors,
                        &mut fixture_modules,
                    );
                }
            }
            registered_fixture_dirs.insert(parent_dir.to_owned());
        }

        let mtime = file_mtime_secs(file);
        // The item cache may serve a file only when prescan positively
        // establishes that the file declares no inline fixtures (#1850).
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
        let cached = if collect_violations || declares_inline_fixtures {
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

    errors.extend(reject_inprocess_inside_package(&items, &fixture_modules));

    CollectionOutput {
        items,
        errors,
        raw_violations,
        profile,
        fixture_modules,
    }
}

/// Reject `@oxi.mark.inprocess` inside a package-lifetime subtree.
///
/// `lifetime="package"` promises exactly one instance per run, enforced by
/// co-locating the declaring subtree into one task. `inprocess` breaks that
/// promise from underneath: `arrange::partition_inprocess_groups` splits marked
/// items into a separate phase that runs on the *coordinator's* session while
/// the rest run on a worker's, so the fixture is built once in each — a silent,
/// load-dependent duplicate that only appears under `-n`.
///
/// Rejected at collection time, before any test runs, following ADR-0009's
/// precedent for B1 violations: the combination cannot be honoured, so saying
/// so up front beats a guarantee that quietly is not one. Lifting the
/// restriction means teaching the planner to keep a declaring subtree in a
/// single phase — tracked separately, not worked around here.
fn reject_inprocess_inside_package(
    items: &[Arc<types::TestItem>],
    fixture_modules: &[types::FixtureModule],
) -> Vec<types::CollectError> {
    let declaring: Vec<&camino::Utf8Path> = fixture_modules
        .iter()
        .filter(|m| m.declares_package())
        .map(|m| m.anchor.as_path())
        .collect();
    if declaring.is_empty() {
        return Vec::new();
    }

    items
        .iter()
        .filter(|item| item.markers.has_inprocess())
        .filter_map(|item| {
            let module = camino::Utf8Path::new(item.module_path());
            let anchor = declaring.iter().find(|dir| module.starts_with(dir))?;
            Some(types::CollectError::PyError(format!(
                "{} is marked @oxi.mark.inprocess but sits inside {anchor}, which \
                 declares a lifetime=\"package\" fixture.\n\
                 inprocess tests run on the main process while the rest of the \
                 package runs on a worker, so the package fixture would be built \
                 once in each — the exactly-once guarantee cannot hold.\n\
                 Hint: drop the inprocess mark, or move the fixture to \
                 lifetime=\"module\".",
                item.node_id,
            )))
        })
        .collect()
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
    /// Classify *entry*, given whether it matched a coverage subject this run.
    ///
    /// Two questions, kept apart because a run can only answer one of them:
    ///
    /// 1. **Does the path exist?** Static and run-independent (`src/mod.py` vs
    ///    `src/mods.py`).
    /// 2. **Does the named symbol exist?** Only the scan knows, so it stays
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

    // ── test_tree_root (#1711) ───────────────────────────────────────────────

    fn paths(entries: &[&str]) -> Vec<Utf8PathBuf> {
        entries.iter().map(Utf8PathBuf::from).collect()
    }

    #[test]
    fn tree_root_of_one_directory_is_that_directory() {
        let files = paths(&["tests/test_a.py", "tests/test_b.py"]);

        let root = test_tree_root(&files);

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("tests")),
            "a flat suite's root is the directory holding it — that is where a \
             lifetime=\"session\" declaration is legal"
        );
    }

    #[test]
    fn tree_root_climbs_to_the_common_ancestor_of_siblings() {
        let files = paths(&["tests/api/test_a.py", "tests/db/test_b.py"]);

        let root = test_tree_root(&files);

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("tests")),
            "with tests in two sibling directories the root is their parent, \
             even though it holds no test file itself; declaring session in \
             either sibling would scope it below the run"
        );
    }

    #[test]
    fn tree_root_is_independent_of_how_deep_the_search_started() {
        // The same suite, whether reached via `oxitest project/` or by running
        // inside it. A positional path overrides testpaths (config/merge.rs),
        // so deriving the root from config would give two different answers and
        // make the same declaration legal or illegal depending on the caller.
        let files = paths(&["project/suite/test_a.py", "project/suite/test_b.py"]);

        let root = test_tree_root(&files);

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("project/suite")),
            "the root is derived from the files collected, not from the search \
             root, so invocation style cannot change which directory is rootdir"
        );
    }

    #[test]
    fn tree_root_of_nothing_collected_is_none() {
        let root = test_tree_root(&[]);

        assert!(
            root.is_none(),
            "with no tests there is no tree, so no directory can be the rootdir \
             package and no session declaration can sit inside one"
        );
    }

    #[test]
    fn tree_root_of_a_single_file_is_its_directory() {
        let files = paths(&["tests/api/test_only.py"]);

        let root = test_tree_root(&files);

        assert_eq!(
            root,
            Some(Utf8PathBuf::from("tests/api")),
            "a one-directory run makes that directory the rootdir package — \
             otherwise a suite that lives in a subdirectory could never declare \
             a session fixture at all"
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
    /// *entry_path*, placed in `scope` or `skip` per *as_scope*.
    ///
    /// *rootdir* must exist on disk: the staleness verdict resolves each entry
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

    /// Count stale diagnostics of either kind for *cfg*, scanning *doctest_files*.
    ///
    /// `doctest_files` are absolute paths; `collect_coverage_diagnostics` strips
    /// `rootdir` from them to build the scanned set. Pass an empty slice to
    /// model "nothing scanned".
    fn stale_count(cfg: &crate::config::Config, doctest_files: &[Utf8PathBuf]) -> usize {
        collect_coverage_diagnostics(doctest_files, cfg)
            .iter()
            .filter(|d| d.context.as_ref().starts_with("doctest.coverage.stale-"))
            .count()
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
