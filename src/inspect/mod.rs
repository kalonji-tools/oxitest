//! Interactive TUI for test suite introspection.
//!
//! This module implements `oxitest inspect`, a ratatui-based terminal UI that
//! lets users browse tests, fixtures, marks, and other collected metadata.

mod app;
mod detail;
pub(crate) mod graph;
mod input;
pub(crate) mod nav;
pub(crate) mod search;
mod ui;

use crate::config::{self, cli::InspectArgs};
use graph::InspectGraph;

/// Build the inspect graph from instant-tier data available without a
/// Python session.
///
/// This collects test, mark, and helper entries via Rust AST extraction
/// and wires up edges.  Fixture and plugin nodes require a Python session
/// and are added later by progressive loading (#1119).
///
/// Startup filters are applied in order:
/// 1. `--affected` — narrow test files before AST extraction
/// 2. Extract AST entries from surviving files
/// 3. `-E` expression — evaluate DSL against test entries, discard non-matching
/// 4. `--lf` — load TestCache, keep only previously-failed tests
/// 5. Build graph from surviving entries
fn build_graph(
    args: &InspectArgs,
    cfg: &config::Config,
) -> Result<InspectGraph, Box<dyn std::error::Error>> {
    use crate::collector;
    use crate::query::extract;
    use graph::builder::GraphBuilder;

    let (mut test_files, conftest_files) = collector::collect_files(cfg)?;

    // ── 1. --affected: narrow test files before extraction ────────────
    if let Some(ref raw_ref) = args.filter.affected {
        let base_ref = if raw_ref.is_empty() {
            &cfg.filter.affected_base
        } else {
            raw_ref
        };
        match crate::affected::filter_affected_test_files(&test_files, &cfg.rootdir, base_ref) {
            Ok(Some(files)) => {
                tracing::info!(
                    affected = files.len(),
                    total = test_files.len(),
                    base = base_ref,
                    "inspect: running affected test files only"
                );
                test_files = files;
            }
            Ok(None) => {
                tracing::info!("inspect: config changed — using all test files");
            }
            Err(e) => {
                tracing::warn!("inspect: --affected filtering failed ({e}), using all test files");
            }
        }
    }

    // ── 2. Extract AST entries from surviving files ───────────────────
    let mut test_entries = extract::extract_test_entries(&test_files);

    // ── 3. -E expression: filter test entries via DSL ────────────────
    if let Some(ref expr_str) = args.filter.expression {
        let tokens = crate::query::compile::lex(expr_str)?;
        let expr = crate::query::compile::parse(tokens)?;
        test_entries.retain(|entry| crate::query::eval::eval(&expr, entry));
    }

    // ── 4. --lf: keep only previously-failed tests ───────────────────
    if let Some(config::FailedMode::Only) = args.failed_filter.resolve() {
        let cache = crate::cache::TestCache::load(&cfg.rootdir);
        let failed_ids = cache.last_failed_ids();
        test_entries.retain(|entry| {
            entry
                .get("name")
                .is_some_and(|name| failed_ids.contains(name))
        });
    }

    // ── 5. Build graph from surviving entries ─────────────────────────
    let mut builder = GraphBuilder::new();
    builder.add_test_entries(&test_entries);
    builder.add_mark_entries(&extract::extract_mark_entries(
        &test_files,
        &cfg.markers.registered_markers,
    ));
    builder.add_helper_entries(&extract::extract_helper_entries(&conftest_files));
    builder.resolve_edges();

    Ok(builder.build())
}

/// Launch the inspect TUI.
///
/// Builds the inspect graph from instant-tier data, then sets up the terminal
/// (raw mode, alternate screen, mouse capture), runs the event loop, and
/// restores the terminal on exit.
pub(crate) fn run(
    args: &InspectArgs,
    cfg: &config::Config,
) -> Result<(), Box<dyn std::error::Error>> {
    let graph = build_graph(args, cfg)?;
    let mut terminal = ui::setup_terminal()?;
    let result = app::InspectApp::new(Some(graph), args.name.as_deref()).run(&mut terminal);
    ui::restore_terminal(&mut terminal)?;
    result
}

#[cfg(test)]
mod tests {
    use crate::config::FailedMode;
    use crate::config::cli::{FailedFilterArgs, FilteringArgs, InspectArgs};
    use crate::query::resource::QueryEntry;
    use std::collections::HashMap;

    /// Create a [`QueryEntry`] from key-value pairs.
    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    /// Build a minimal set of test entries for filter tests.
    fn sample_entries() -> Vec<QueryEntry> {
        vec![
            entry(&[
                ("name", "tests/test_a.py::test_fast"),
                ("source", "tests/test_a.py"),
                ("async", "false"),
                ("mark", ""),
            ]),
            entry(&[
                ("name", "tests/test_a.py::test_slow_one"),
                ("source", "tests/test_a.py"),
                ("async", "false"),
                ("mark", "slow"),
            ]),
            entry(&[
                ("name", "tests/test_b.py::test_async_thing"),
                ("source", "tests/test_b.py"),
                ("async", "true"),
                ("mark", ""),
            ]),
        ]
    }

    // ── Expression filter tests ──────────────────────────────────────────

    #[test]
    fn expression_filter_retains_matching_entries() {
        let mut entries = sample_entries();
        let tokens = crate::query::compile::lex("mark(slow)").unwrap();
        let expr = crate::query::compile::parse(tokens).unwrap();
        entries.retain(|e| crate::query::eval::eval(&expr, e));
        assert_eq!(
            entries.len(),
            1,
            "only the entry with mark=slow should survive the expression filter"
        );
        assert_eq!(
            entries[0].get("name"),
            Some("tests/test_a.py::test_slow_one"),
            "surviving entry should be the slow test"
        );
    }

    #[test]
    fn expression_filter_name_contains() {
        let mut entries = sample_entries();
        let tokens = crate::query::compile::lex("name(async)").unwrap();
        let expr = crate::query::compile::parse(tokens).unwrap();
        entries.retain(|e| crate::query::eval::eval(&expr, e));
        assert_eq!(
            entries.len(),
            1,
            "only the entry with 'async' in its name should survive"
        );
        assert_eq!(
            entries[0].get("name"),
            Some("tests/test_b.py::test_async_thing"),
            "surviving entry should be the async test"
        );
    }

    #[test]
    fn expression_filter_async_predicate() {
        let mut entries = sample_entries();
        let tokens = crate::query::compile::lex("async()").unwrap();
        let expr = crate::query::compile::parse(tokens).unwrap();
        entries.retain(|e| crate::query::eval::eval(&expr, e));
        assert_eq!(
            entries.len(),
            1,
            "only async tests should survive the async() predicate"
        );
    }

    #[test]
    fn expression_filter_not_operator() {
        let mut entries = sample_entries();
        let tokens = crate::query::compile::lex("!mark(slow)").unwrap();
        let expr = crate::query::compile::parse(tokens).unwrap();
        entries.retain(|e| crate::query::eval::eval(&expr, e));
        assert_eq!(
            entries.len(),
            2,
            "negation should keep entries without the slow mark"
        );
    }

    #[test]
    fn no_expression_filter_passes_all() {
        let entries = sample_entries();
        // Simulates the no-filter path: entries are unchanged.
        assert_eq!(
            entries.len(),
            3,
            "without an expression filter, all entries should pass through"
        );
    }

    // ── Last-failed filter tests ─────────────────────────────────────────

    #[test]
    fn lf_filter_retains_only_failed_ids() {
        let mut entries = sample_entries();
        // Simulate a failed_ids set containing only one test.
        let failed_ids: std::collections::HashSet<String> =
            ["tests/test_a.py::test_fast".to_string()]
                .into_iter()
                .collect();
        entries.retain(|e| e.get("name").is_some_and(|name| failed_ids.contains(name)));
        assert_eq!(
            entries.len(),
            1,
            "only the previously-failed test should survive the --lf filter"
        );
        assert_eq!(
            entries[0].get("name"),
            Some("tests/test_a.py::test_fast"),
            "surviving entry should be the failed test"
        );
    }

    #[test]
    fn lf_filter_empty_cache_removes_all() {
        let mut entries = sample_entries();
        let failed_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
        entries.retain(|e| e.get("name").is_some_and(|name| failed_ids.contains(name)));
        assert_eq!(
            entries.len(),
            0,
            "empty failed_ids set should remove all entries"
        );
    }

    // ── FailedFilterArgs::resolve tests ──────────────────────────────────

    #[test]
    fn failed_filter_lf_resolves_to_only() {
        let args = FailedFilterArgs {
            failed: None,
            lf: true,
            ff: false,
        };
        assert_eq!(
            args.resolve(),
            Some(FailedMode::Only),
            "--lf should resolve to FailedMode::Only"
        );
    }

    #[test]
    fn failed_filter_ff_resolves_to_first() {
        let args = FailedFilterArgs {
            failed: None,
            lf: false,
            ff: true,
        };
        assert_eq!(
            args.resolve(),
            Some(FailedMode::First),
            "--ff should resolve to FailedMode::First"
        );
    }

    #[test]
    fn failed_filter_none_resolves_to_none() {
        let args = FailedFilterArgs {
            failed: None,
            lf: false,
            ff: false,
        };
        assert_eq!(
            args.resolve(),
            None,
            "no failed flags should resolve to None"
        );
    }

    // ── Combined filter tests ────────────────────────────────────────────

    #[test]
    fn expression_then_lf_narrows_progressively() {
        let mut entries = sample_entries();

        // Step 1: expression filter keeps entries with 'test_a' in name.
        let tokens = crate::query::compile::lex("source(test_a)").unwrap();
        let expr = crate::query::compile::parse(tokens).unwrap();
        entries.retain(|e| crate::query::eval::eval(&expr, e));
        assert_eq!(
            entries.len(),
            2,
            "expression filter should keep 2 entries from test_a.py"
        );

        // Step 2: lf filter keeps only the previously-failed test.
        let failed_ids: std::collections::HashSet<String> =
            ["tests/test_a.py::test_slow_one".to_string()]
                .into_iter()
                .collect();
        entries.retain(|e| e.get("name").is_some_and(|name| failed_ids.contains(name)));
        assert_eq!(
            entries.len(),
            1,
            "combined expression+lf should narrow to a single entry"
        );
        assert_eq!(
            entries[0].get("name"),
            Some("tests/test_a.py::test_slow_one"),
            "surviving entry should be the slow failed test"
        );
    }

    // ── InspectArgs default has no active filters ────────────────────────

    #[test]
    fn default_inspect_args_has_no_filters() {
        let args = InspectArgs {
            name: None,
            filter: FilteringArgs {
                expression: None,
                affected: None,
            },
            failed_filter: FailedFilterArgs {
                failed: None,
                lf: false,
                ff: false,
            },
        };
        assert!(
            args.filter.expression.is_none(),
            "default InspectArgs should have no expression filter"
        );
        assert!(
            args.filter.affected.is_none(),
            "default InspectArgs should have no affected filter"
        );
        assert_eq!(
            args.failed_filter.resolve(),
            None,
            "default InspectArgs should have no failed filter"
        );
    }
}
