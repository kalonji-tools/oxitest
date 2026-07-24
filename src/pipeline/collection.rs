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

pub(super) fn collect_items(
    py: Python<'_>,
    test_files: &[Utf8PathBuf],
    cfg: &config::Config,
    session: &bridge::FixtureSession,
    cache: &mut cache::TestCache,
) -> (
    Vec<Arc<types::TestItem>>,
    Vec<types::CollectError>,
    Vec<bridge::RawViolation>,
    Option<CollectionProfile>,
) {
    let mut items: Vec<Arc<types::TestItem>> = Vec::new();
    let mut errors = Vec::new();
    let mut raw_violations: Vec<bridge::RawViolation> = Vec::new();
    let collect_violations = cfg.markers.strict.is_some();

    let profile_enabled = cfg.output.collection_profile;
    let wall_start = std::time::Instant::now();
    let mut file_profiles: Vec<FileProfile> = Vec::new();

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

    (items, errors, raw_violations, profile)
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
///
/// The waivers ratchet downgrades waived entries to `Notice` and emits `Error`
/// for stale entries regardless of strict mode (shrink-only invariant).
pub(super) fn collect_coverage_diagnostics(
    doctest_files: &[Utf8PathBuf],
    config: &crate::config::Config,
) -> Vec<crate::reporter::stats::DiagnosticEntry> {
    use std::sync::Arc;

    use crate::config::{DoctestScope, StrictMode};
    use crate::doctest::alias::ModuleRoot;
    use crate::doctest::coverage::run_coverage_check;
    use crate::doctest::waivers::load_waivers;
    use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
    use crate::types::LineNo;

    let Some(dt) = config.doctest.as_ref() else {
        return vec![];
    };
    if matches!(dt.scope, Some(DoctestScope::Off)) {
        return vec![];
    }

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
    // Filter out files matching any [tool.oxitest.doctest].skip prefix. Path
    // prefixes are matched via `starts_with` — simple, no globs. Skipped files
    // are excluded from both subject enumeration and alias-walking downstream.
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
        .filter(|rel| !dt.skip.iter().any(|prefix| rel.starts_with(prefix)))
        .collect();

    let raw_diags = run_coverage_check(&rel_files, &root, severity.clone());

    let waivers_rel = dt.waivers.as_deref().unwrap_or(".oxi-doctest-waivers");
    let waivers_path = config.rootdir.join(waivers_rel);
    let waivers = load_waivers(&waivers_path);

    let mut out: Vec<DiagnosticEntry> = Vec::new();
    let mut missing_subjects: std::collections::HashSet<String> = std::collections::HashSet::new();
    for d in raw_diags {
        if d.context.as_ref() == "doctest.coverage"
            && let Some(subj) = d
                .message
                .strip_prefix('`')
                .and_then(|s| s.find('`').map(|i| s[..i].to_owned()))
        {
            missing_subjects.insert(subj.clone());
            if waivers.entries.contains(&subj) {
                out.push(DiagnosticEntry {
                    severity: DiagnosticSeverity::Notice,
                    context: d.context,
                    message: format!("{} (waived)", d.message),
                    file: d.file,
                    lineno: d.lineno,
                });
                continue;
            }
        }
        out.push(d);
    }

    // Stale-entry checking requires the full testpaths scan to be meaningful.
    // Subset runs (explicit file paths) see only a fraction of the subjects, so
    // waivers entries for unscanned files would be flagged as stale — a false
    // positive. Skip the shrink-only check for subset runs.
    if !config.filter.has_explicit_paths {
        let mut stale: Vec<&String> = waivers
            .entries
            .iter()
            .filter(|e| !missing_subjects.contains(*e))
            .collect();
        stale.sort();
        for entry in stale {
            let lineno = waivers.line_by_entry.get(entry).copied().unwrap_or(0);
            out.push(DiagnosticEntry {
                severity: DiagnosticSeverity::Error,
                context: Arc::from("doctest.coverage.ratchet"),
                message: format!(
                    "`{}` is in `{}` but no longer needs coverage — shrink-only: remove this entry",
                    entry, waivers_rel
                ),
                file: Some(waivers.path.clone()),
                lineno: Some(LineNo::new(lineno.max(1) as usize)),
            });
        }
    }

    out
}

/// Split a coverage diagnostic set into hard-fail errors and pending diagnostics.
///
/// `doctest.coverage`, `doctest.coverage.ratchet`, and `doctest.coverage.analysis`
/// Error-severity entries all become `CollectError::PyError` (hard fail under
/// `strict = "abort"`). Under `abort`, an analysis error means "the scanner
/// cannot verify coverage for this subject" — that is semantically a coverage
/// failure. Users who need to allow unresolvable aliases must fix the alias
/// chain or downgrade `strict` globally.
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
            "doctest.coverage" | "doctest.coverage.ratchet" | "doctest.coverage.analysis"
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
            waivers: None,
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
    fn collect_coverage_diagnostics_returns_empty_when_scope_off() {
        use crate::config::{DoctestConfig, DoctestScope, StrictMode};

        let tmp = tempfile::tempdir().expect("test setup: tempdir must succeed");
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned())
            .expect("tempdir path must be valid UTF-8");
        write_pkg_with_missing_examples(&root);

        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root.clone();
        cfg.markers.strict = Some(StrictMode::Enforce);
        cfg.doctest = Some(DoctestConfig {
            scope: Some(DoctestScope::Off),
            waivers: None,
            ..Default::default()
        });

        let files = vec![root.join("mypkg/__init__.py")];
        let diags = collect_coverage_diagnostics(&files, &cfg);
        assert!(
            diags.is_empty(),
            "scope=off must short-circuit before running the rule — no diagnostics"
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
            waivers: None,
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
            waivers: None,
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

    // ── M2 helpers ──────────────────────────────────────────────────────────────

    fn doctest_only_cfg(
        scope: crate::config::DoctestScope,
        strict: Option<crate::config::StrictMode>,
        waivers: Option<String>,
    ) -> (crate::config::Config, tempfile::TempDir) {
        use crate::config::DoctestConfig;

        let tmp = tempfile::tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_owned()).unwrap();
        let mut cfg = crate::config::Config::default();
        cfg.rootdir = root;
        cfg.markers.strict = strict;
        cfg.doctest = Some(DoctestConfig {
            scope: Some(scope),
            waivers,
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

    fn write_synth_fully_covered_subject(cfg: &crate::config::Config) -> Vec<Utf8PathBuf> {
        use std::fs;
        fs::create_dir_all(cfg.rootdir.join("mypkg")).unwrap();
        fs::write(
            cfg.rootdir.join("mypkg/__init__.py"),
            "\"\"\"pkg.\"\"\"\n\n__all__ = [\"foo\"]\n\ndef foo():\n    \"\"\"Foo.\n\n    Examples:\n        >>> 1 + 1\n        2\n    \"\"\"\n    pass\n",
        )
        .unwrap();
        vec![cfg.rootdir.join("mypkg/__init__.py")]
    }

    // ── M2 tests ─────────────────────────────────────────────────────────────

    #[test]
    fn m2_enforce_maps_to_warning_severity() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
            None,
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

    #[test]
    fn m2_waived_missing_subject_downgraded_to_notice() {
        let (mut cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
            None,
        );
        let files = write_synth_missing_subject(&cfg);
        std::fs::write(cfg.rootdir.join(".oxi-doctest-waivers"), "mypkg.foo\n").unwrap();
        if let Some(dt) = cfg.doctest.as_mut() {
            dt.waivers = Some(".oxi-doctest-waivers".to_owned());
        }
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let coverage_diags: Vec<_> = diags
            .iter()
            .filter(|d| d.context.as_ref() == "doctest.coverage")
            .collect();
        assert!(
            !coverage_diags.is_empty(),
            "waived subject still surfaces a diagnostic — visible tech debt, not silent"
        );
        assert!(
            coverage_diags
                .iter()
                .all(|d| d.severity == crate::reporter::stats::DiagnosticSeverity::Notice),
            "waivers ∩ missing must be Notice regardless of strict mode — that is the point of the waivers file"
        );
        assert!(
            coverage_diags
                .iter()
                .any(|d| d.message.contains("(waived)")),
            "the message must mark the entry as waived so users can find it in logs"
        );
    }

    #[test]
    fn m2_missing_subject_not_waived_under_abort_hard_fails() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
            None,
        );
        let files = write_synth_missing_subject(&cfg);
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let cov: Vec<_> = diags
            .iter()
            .filter(|d| d.context.as_ref() == "doctest.coverage")
            .collect();
        assert!(
            !cov.is_empty(),
            "non-waived missing subject under abort must produce a coverage diagnostic"
        );
        assert!(
            cov.iter()
                .all(|d| d.severity == crate::reporter::stats::DiagnosticSeverity::Error),
            "non-waived missing subject under abort must fire at Error severity"
        );
    }

    #[test]
    fn m2_stale_waivers_entry_hard_fails_regardless_of_strict() {
        let (mut cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
            None,
        );
        let files = write_synth_fully_covered_subject(&cfg);
        std::fs::write(cfg.rootdir.join(".oxi-doctest-waivers"), "mypkg.foo\n").unwrap();
        if let Some(dt) = cfg.doctest.as_mut() {
            dt.waivers = Some(".oxi-doctest-waivers".to_owned());
        }
        let diags = collect_coverage_diagnostics(&files, &cfg);
        let stale: Vec<_> = diags
            .iter()
            .filter(|d| d.context.as_ref() == "doctest.coverage.ratchet")
            .collect();
        assert_eq!(
            stale.len(),
            1,
            "one stale-entry diagnostic per name in the waivers file that is not currently missing; got {} diagnostics",
            stale.len()
        );
        assert_eq!(
            stale[0].severity,
            crate::reporter::stats::DiagnosticSeverity::Error,
            "shrink-only enforcement — stale entries must always Error, not respect strict mode"
        );
        assert!(
            stale[0].message.contains("mypkg.foo"),
            "stale-entry message names the stale subject; got: {}",
            stale[0].message
        );
        assert!(
            stale[0].message.contains("shrink-only"),
            "stale-entry message explains the invariant; got: {}",
            stale[0].message
        );
    }

    #[test]
    fn collect_coverage_diagnostics_excludes_test_files_from_public_subject_scan() {
        let (cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Enforce),
            None,
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
            None,
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
    fn m2_walk_error_not_waivable_even_if_subject_matches_waivers() {
        use crate::reporter::stats::DiagnosticSeverity;
        let (mut cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
            None,
        );
        use std::fs;
        // Synthesize a project where a public subject aliases into a non-existent module.
        // The scanner should emit a ModuleFileNotFound walk error, not a missing-header
        // gap. Under the fix, this diagnostic must NOT be waivable via the waivers file.
        fs::create_dir_all(cfg.rootdir.join("mypkg")).unwrap();
        fs::write(
            cfg.rootdir.join("mypkg/__init__.py"),
            "\"\"\"pkg.\"\"\"\n\n__all__ = [\"broken\"]\n\nfrom nonexistent_module import broken\n",
        )
        .unwrap();
        // Waive the subject that's about to raise a walk error.
        fs::write(cfg.rootdir.join(".oxi-doctest-waivers"), "mypkg.broken\n").unwrap();
        if let Some(dt) = cfg.doctest.as_mut() {
            dt.waivers = Some(".oxi-doctest-waivers".to_owned());
        }
        let files = vec![cfg.rootdir.join("mypkg/__init__.py")];
        let diags = collect_coverage_diagnostics(&files, &cfg);
        // Find the walk error diagnostic.
        let walk_error_diags: Vec<_> = diags
            .iter()
            .filter(|d| d.context.as_ref() == "doctest.coverage.analysis")
            .collect();
        assert!(
            !walk_error_diags.is_empty(),
            "the scanner must emit an analysis-error diagnostic when an alias target module is missing; got contexts: {:?}",
            diags.iter().map(|d| d.context.as_ref()).collect::<Vec<_>>()
        );
        assert!(
            walk_error_diags
                .iter()
                .all(|d| d.severity == DiagnosticSeverity::Error),
            "analysis-error diagnostics must not be waivable — the waivers file cannot mask a scanner failure that means we can't verify coverage at all"
        );
        // Also confirm no doctest.coverage-context Notice was produced for this subject
        // (the waived downgrade path must NOT have fired).
        let waived_downgrades: Vec<_> = diags
            .iter()
            .filter(|d| {
                d.context.as_ref() == "doctest.coverage"
                    && d.severity == DiagnosticSeverity::Notice
                    && d.message.contains("mypkg.broken")
            })
            .collect();
        assert!(
            waived_downgrades.is_empty(),
            "an analysis error must not be reclassified as a waived-coverage Notice; got waived downgrades: {:?}",
            waived_downgrades
                .iter()
                .map(|d| &d.message)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn collect_coverage_diagnostics_respects_doctest_skip_prefix() {
        let (mut cfg, _tmp) = doctest_only_cfg(
            crate::config::DoctestScope::Public,
            Some(crate::config::StrictMode::Abort),
            None,
        );
        if let Some(dt) = cfg.doctest.as_mut() {
            dt.skip = vec!["fixtures".to_owned()];
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
    fn m2_walk_error_diagnostic_context_is_analysis_not_coverage() {
        // Focused test: ensure ALL AliasError variants emit at the analysis context.
        use crate::doctest::alias::AliasError;
        use crate::doctest::coverage::diagnostic_for_walk_error;
        use crate::doctest::subjects::Subject;
        use crate::reporter::stats::DiagnosticSeverity;
        let subj = Subject {
            public_id: "mypkg.foo".into(),
            source: crate::doctest::subjects::SubjectSource::LocalDefinition,
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
                "every AliasError variant must produce an analysis-context diagnostic so the ratchet skips it; got context={} for error={:?}",
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
                message: "`mypkg.c` missing `Examples:` header (waived)".into(),
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
