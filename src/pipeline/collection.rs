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
                fixture_names: vec![],
                fixref_names: vec![],
            }));
        }
    }

    items
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
}
