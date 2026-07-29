//! Test item collection: file scanning, module import, and profiling.

use std::sync::Arc;

use camino::Utf8PathBuf;
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

        let cached_ast = match prescan {
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
            crate::prescan::PrescanResult::Unavailable => None,
            crate::prescan::PrescanResult::HasTests(p) => {
                if collect_violations && !p.source.is_empty() {
                    Some((p.source, p.stmts))
                } else {
                    None
                }
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
        let cached = if collect_violations {
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

    let (mut diagnostics, (scope_hits, skip_hits)) =
        run_coverage_check(&rel_files, &root, severity.clone(), &dt.scope, &dt.skip);

    let scope_entries: &[crate::config::ScopeEntry] = match dt.scope.as_ref() {
        Some(crate::config::DoctestScope::List(e)) => e,
        _ => &[],
    };
    diagnostics.extend(stale_diagnostics(
        scope_entries,
        &scope_hits,
        "doctest.coverage.stale-scope",
        "scope",
        &severity,
    ));
    diagnostics.extend(stale_diagnostics(
        &dt.skip,
        &skip_hits,
        "doctest.coverage.stale-skip",
        "skip",
        &severity,
    ));

    diagnostics
}

/// Emit one diagnostic per configured entry whose `hits[i]` is false — i.e.
/// entries that matched zero subjects during scope/skip filtering.
fn stale_diagnostics(
    entries: &[crate::config::ScopeEntry],
    hits: &[bool],
    context: &'static str,
    kind: &'static str,
    severity: &crate::reporter::stats::DiagnosticSeverity,
) -> Vec<crate::reporter::stats::DiagnosticEntry> {
    entries
        .iter()
        .zip(hits.iter())
        .filter(|(_, hit)| !**hit)
        .map(|(entry, _)| crate::reporter::stats::DiagnosticEntry {
            severity: severity.clone(),
            context: std::sync::Arc::from(context),
            message: format!(
                "{kind} entry '{}' matched no coverage subjects (remove it or fix the path)",
                crate::config::render_entry(entry),
            ),
            file: None,
            lineno: None,
        })
        .collect()
}

/// Split a coverage diagnostic set into hard-fail errors and pending diagnostics.
///
/// `doctest.coverage`, `doctest.coverage.analysis`, `doctest.coverage.stale-scope`,
/// and `doctest.coverage.stale-skip` Error-severity entries all become
/// `CollectError::PyError` (hard fail under `strict = "abort"`).
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

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        cfg.markers.strict = Some(StrictMode::Enforce);
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::Public),
            ..Default::default()
        });

        let files = vec![root.join("mypkg/__init__.py")];
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

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        cfg.markers.strict = Some(StrictMode::Off);
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::Public),
            ..Default::default()
        });

        let files = vec![root.join("mypkg/__init__.py")];
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

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        // markers.strict is None by default
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::Public),
            ..Default::default()
        });

        let files = vec![root.join("mypkg/__init__.py")];
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

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        cfg.doctest = None;

        let files = vec![root.join("mypkg/__init__.py")];
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
        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root;
        cfg.markers.strict = strict;
        cfg.doctest = Some(DoctestConfig {
            scope: Some(scope),
            ..Default::default()
        });
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
            "promoted CollectError carries the original diagnostic message so the user sees which subject failed; got: {}",
            err_msg
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

    #[test]
    fn stale_scope_fires_when_file_scanned_but_all_subjects_filtered() {
        use crate::config::{DoctestConfig, DoctestScope, ScopeEntry, StrictMode};

        // Scope entry names a REAL file that gets scanned, but every subject
        // in that file is private (leading _) so the Public private-filter drops
        // them all. Under List scope with just this one entry we'd expect it to
        // match at least one of the _-subjects (list bypasses the private
        // filter) — but the entry is Symbol-form for a name that doesn't exist,
        // so it still matches zero subjects and must fire stale-scope. This
        // guards against a regression where "file existed in scan" was
        // mistakenly treated as "entry matched".
        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        std::fs::create_dir_all(root.join("mypkg")).unwrap();
        std::fs::write(root.join("mypkg/__init__.py"), "def real_symbol(): pass\n").unwrap();

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        cfg.markers.strict = Some(StrictMode::Enforce);
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::List(vec![ScopeEntry::Symbol {
                file: Utf8PathBuf::from("mypkg/__init__.py"),
                name: "not_real_symbol".to_string(),
            }])),
            skip: vec![],
        });

        let files = vec![root.join("mypkg/__init__.py")];
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let stale = diags
            .iter()
            .find(|d| d.context.as_ref() == "doctest.coverage.stale-scope");
        assert!(
            stale.is_some(),
            "stale-scope must fire even when the entry's file was scanned — matching is subject-level, and 'file entered the pipeline' must not silently satisfy the entry (diags: {diags:?})",
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
            "the analysis-error hard-fail must name the scanner failure; got: {:?}",
            msgs
        );
        assert!(
            msgs.iter().any(|m| m.contains("mypkg.y")),
            "the coverage-gap hard-fail must name the missing subject; got: {:?}",
            msgs
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
