//! Interactive TUI for test suite introspection.
//!
//! This module implements `oxitest inspect`, a ratatui-based terminal UI that
//! lets users browse tests, fixtures, marks, and other collected metadata.
//!
//! Graph construction uses **progressive loading** (#1119): instant-tier data
//! (tests, marks, helpers) is collected synchronously before the TUI starts,
//! while Python-tier data (fixtures, plugins) is collected in a background
//! thread and merged into the graph when ready.

mod app;
mod detail;
pub(crate) mod graph;
mod input;
pub(crate) mod nav;
pub(crate) mod overview;
pub(crate) mod search;
pub(crate) mod signals;
pub(crate) mod source;
mod ui;

use std::sync::mpsc;
use std::thread;

use camino::Utf8PathBuf;

use crate::config::{self, cli::InspectArgs};
use app::{Phase2Data, RefreshArgs};
use graph::InspectGraph;

/// Phase 1: build the inspect graph from instant-tier data available without a
/// Python session.
///
/// Accepts pre-collected file lists so that the caller can spawn Phase 2 with
/// the conftest paths before this function begins AST extraction.
///
/// Startup filters are applied in order:
/// 1. `--affected` — narrow test files before AST extraction
/// 2. Extract AST entries from surviving files (single-pass combined extraction)
/// 3. `-E` expression — evaluate DSL against test entries, discard non-matching
/// 4. `--lf` — load TestCache, keep only previously-failed tests
/// 5. Build graph from surviving entries
pub(crate) fn build_phase1_graph(
    args: &InspectArgs,
    cfg: &config::Config,
    mut test_files: Vec<Utf8PathBuf>,
    conftest_files: &[Utf8PathBuf],
) -> Result<InspectGraph, Box<dyn std::error::Error>> {
    use crate::query::extract;
    use graph::builder::GraphBuilder;

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

    // ── 2. Extract AST entries from surviving files (single-pass) ────
    let (mut test_entries, mark_entries) =
        extract::extract_test_and_mark_entries(&test_files, &cfg.markers.registered_markers);

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
    builder.add_mark_entries(&mark_entries);
    builder.add_helper_entries(&extract::extract_helper_entries(conftest_files));
    builder.resolve_edges();

    Ok(builder.build())
}

/// Phase 2: spawn a background thread that initializes the Python session and
/// collects fixture, plugin, and test→fixture dependency entries.
///
/// Sends a [`Phase2Data`] payload through the channel when done.  If the
/// Python session fails, the sender is simply dropped, which causes the
/// receiver to see `Disconnected` — the TUI then transitions to
/// `LoadingState::Complete` with whatever data it already has.
pub(crate) fn spawn_phase2(
    conftest_files: Vec<Utf8PathBuf>,
    test_files: Vec<Utf8PathBuf>,
    plugins: Vec<String>,
    plugin_settings: std::collections::HashMap<String, toml::Value>,
    tx: mpsc::Sender<Phase2Data>,
) {
    thread::spawn(move || {
        pyo3::Python::attach(|py| {
            let result = (|| -> Result<Phase2Data, String> {
                use crate::bridge::FixtureSession;
                use crate::query::resource::QueryEntry;

                let (session, _violations) =
                    FixtureSession::new(py, &conftest_files).map_err(|e| e.to_string())?;

                // Load plugins if configured.
                if !plugins.is_empty() {
                    session
                        .load_plugins(py, &plugins, &plugin_settings)
                        .map_err(|e| e.to_string())?;
                }

                let fixture_raw = crate::query::bridge::fixture_entries(&session, py)
                    .map_err(|e| e.to_string())?;
                let plugin_raw = crate::query::bridge::plugin_entries(&session, py)
                    .map_err(|e| e.to_string())?;
                let fixture_dep_raw =
                    crate::query::bridge::test_fixture_deps(&session, py, &test_files)
                        .map_err(|e| e.to_string())?;

                let fixture_entries = fixture_raw
                    .into_iter()
                    .map(|fields| QueryEntry { fields })
                    .collect();
                let plugin_entries = plugin_raw
                    .into_iter()
                    .map(|fields| QueryEntry { fields })
                    .collect();
                let fixture_dep_entries = fixture_dep_raw
                    .into_iter()
                    .map(|fields| QueryEntry { fields })
                    .collect();

                Ok(Phase2Data {
                    fixture_entries,
                    plugin_entries,
                    fixture_dep_entries,
                })
            })();

            match result {
                Ok(data) => {
                    // If the receiver was dropped (TUI quit early), this is fine.
                    let _ = tx.send(data);
                }
                Err(e) => {
                    tracing::warn!(error = %e, "phase-2 Python session failed; fixture/plugin data unavailable");
                    // Dropping tx causes Disconnected on the receiver side.
                }
            }
        });
    });
}

/// Launch the inspect TUI with progressive loading.
///
/// Phase 1 collects instant-tier data (tests, marks, helpers) synchronously,
/// then starts the TUI immediately.  Phase 2 spawns a background thread to
/// collect fixture and plugin data from the Python session, which is merged
/// into the graph when ready.
pub(crate) fn run(
    args: &InspectArgs,
    cfg: &config::Config,
) -> Result<(), Box<dyn std::error::Error>> {
    // Collect files first so conftest paths are available before AST work begins.
    let (test_files, conftest_files) = crate::collector::collect_files(cfg)?;

    // Clone test files for phase 2 before phase 1 consumes the original.
    let test_files_for_phase2 = test_files.clone();

    // Phase 2: spawn background Python session immediately — it starts while
    // Phase 1 parses ASTs, so fixture/plugin data is ready sooner.
    let (tx, rx) = mpsc::channel();
    spawn_phase2(
        conftest_files.clone(),
        test_files_for_phase2,
        cfg.features.plugins.clone(),
        cfg.features.plugin_settings.clone(),
        tx,
    );

    // Phase 1: instant-tier graph (synchronous, runs concurrently with Phase 2).
    let mut graph = build_phase1_graph(args, cfg, test_files, &conftest_files)?;
    graph.relativize_paths(cfg.rootdir.as_str());

    let timeout = std::time::Duration::from_secs(cfg.exec.inspect_timeout_secs);
    let refresh_args = RefreshArgs {
        inspect_args: args.clone(),
        config: cfg.clone(),
    };
    let mut terminal = ui::setup_terminal()?;
    let result = app::InspectApp::with_progressive_loading(
        graph,
        rx,
        args.name.as_deref(),
        timeout,
        cfg.rootdir.as_str(),
        Some(refresh_args),
    )
    .run(&mut terminal);
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
