//! Test result reporting — terminal output, CI formatting, and JSON export.
//!
//! Defines the [`Reporter`] trait and its concrete implementations:
//! `TtyReporter` (progress bars, colors),
//! `CiReporter` (GitHub Actions annotations),
//! `JsonReporter` (CTRF format), and
//! `PyPluginReporter` (user-supplied Python plugins).

pub(crate) mod bridge;
mod ci;
pub(crate) use crate::colors;
mod composite;
mod exit;
mod format;
pub(crate) mod json;
pub(crate) mod junit;
mod options;
mod outcome_fmt;
pub(crate) mod plugin;
mod print;
mod session;
pub(crate) use session::ReporterSession;
pub(crate) mod parametrize_buffer;
mod stats;
pub(crate) mod tracing_writer;
mod traits;
mod tty;

pub(crate) use outcome_fmt::{ColorCategory, JunitCategory};

#[cfg(test)]
pub(crate) mod test_helpers;

pub use ci::CiReporter;
pub(crate) use composite::CompositeReporter;
pub use options::{ReporterOpts, ReporterOptsBuilder};
pub(crate) use print::{print_collected, print_strict_abort, print_strict_suite_section};
pub(crate) use traits::{standard_finish, StandardReporter};
pub use traits::{ExitVote, Reporter};
pub use tty::TtyReporter;

// Re-export so ci.rs and tty.rs can reach it via `super::sep_width()`
pub(crate) use format::sep_width;

// ─── Deferred-failure dedup ──────────────────────────────────────────────────

/// Remove entries from `deferred` when a flaky outcome is reported.
///
/// Both [`TtyReporter`] and [`CiReporter`] maintain a deferred-failure list that
/// must be pruned when a retry reveals a test was flaky.  The predicate
/// `matches_node` lets each caller define how an element matches the node-id
/// string (e.g. exact field match vs. `contains`).
pub(crate) fn remove_if_flaky<T>(
    deferred: &mut Vec<T>,
    outcome: &crate::types::TestOutcome,
    item: &crate::types::TestItem,
    matches_node: impl Fn(&T, &str) -> bool,
) {
    if matches!(outcome, crate::types::TestOutcome::Flaky { .. }) {
        let target = item.node_id.as_ref();
        deferred.retain(|d| !matches_node(d, target));
    }
}

/// Build the active reporter from resolved options.
///
/// Chooses [`TtyReporter`] or [`CiReporter`] based on `is_tty`, then wraps
/// all reporters (including optional JSON, JUnit, and plugin reporters) in a
/// [`CompositeReporter`] which owns the single [`RunStats`](stats::RunStats) for the run.
pub fn make_reporter(
    opts: ReporterOpts,
    is_tty: bool,
    json_path: Option<camino::Utf8PathBuf>,
    junit_xml_path: Option<camino::Utf8PathBuf>,
    plugin_reporters: Vec<Box<dyn Reporter>>,
) -> Box<dyn Reporter> {
    let strict_suite_count = opts.strict_suite_lines.len();

    let json_reporter =
        json_path.map(|path| Box::new(json::JsonReporter::new(path)) as Box<dyn Reporter>);
    let junit_reporter =
        junit_xml_path.map(|path| Box::new(junit::JunitReporter::new(path)) as Box<dyn Reporter>);

    let primary: Box<dyn Reporter> = if is_tty {
        Box::new(TtyReporter::new(opts))
    } else {
        Box::new(CiReporter::new(opts))
    };

    let mut reporters = vec![primary];
    if let Some(jr) = json_reporter {
        reporters.push(jr);
    }
    if let Some(xr) = junit_reporter {
        reporters.push(xr);
    }
    reporters.extend(plugin_reporters);

    Box::new(CompositeReporter::new(reporters, strict_suite_count))
}

#[cfg(test)]
mod json_tests {
    use super::*;
    use crate::types::{DurationMs, TestItem, TestOutcome};

    #[test]
    fn test_json_reporter_writes_ctrf_on_finish() {
        use crate::reporter::json::JsonReporter;
        use tempfile::NamedTempFile;

        let tmp = NamedTempFile::new().unwrap();
        let path = camino::Utf8Path::from_path(tmp.path()).unwrap().to_owned();

        let mut rep = JsonReporter::new(path.clone());

        let item = TestItem::builder("tests/test_mod.py", "test_passes").build();
        rep.test_started(&item);
        rep.test_completed(
            &item,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(12.5),
            None,
        );
        rep.finish(&[], false, &ReporterSession::new(0));

        let content = std::fs::read_to_string(&path).unwrap();
        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(v["results"]["tool"]["name"], "oxitest");
        assert_eq!(v["results"]["summary"]["tests"], 1);
        assert_eq!(v["results"]["summary"]["passed"], 1);
        assert_eq!(
            v["results"]["tests"][0]["name"],
            "tests/test_mod.py::test_passes"
        );
        assert_eq!(v["results"]["tests"][0]["status"], "passed");
    }

    #[test]
    fn test_json_reporter_records_failed_test() {
        use crate::reporter::json::JsonReporter;
        use tempfile::NamedTempFile;

        let tmp = NamedTempFile::new().unwrap();
        let path = camino::Utf8Path::from_path(tmp.path()).unwrap().to_owned();

        let mut rep = JsonReporter::new(path.clone());

        let item = TestItem::builder("tests/test_mod.py", "test_fails").build();
        rep.test_started(&item);
        rep.test_completed(
            &item,
            &TestOutcome::failed("assert x == 1")
                .file("tests/test_mod.py")
                .lineno(5)
                .source("assert x == 1")
                .comparison("0", "==", "1")
                .build(),
            DurationMs::new(8.0),
            None,
        );
        rep.finish(&[], false, &ReporterSession::new(0));

        let content = std::fs::read_to_string(&path).unwrap();
        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(v["results"]["summary"]["failed"], 1);
        assert_eq!(v["results"]["tests"][0]["status"], "failed");
    }

    #[test]
    fn test_json_reporter_output_sorted_by_node_id() {
        use crate::reporter::json::JsonReporter;
        use tempfile::NamedTempFile;

        let tmp = NamedTempFile::new().unwrap();
        let path = camino::Utf8Path::from_path(tmp.path()).unwrap().to_owned();
        let mut rep = JsonReporter::new(path.clone());

        // Insert in reverse alphabetical order
        let b = TestItem::builder("tests/test_mod.py", "test_b").build();
        let a = TestItem::builder("tests/test_mod.py", "test_a").build();
        rep.test_started(&b);
        rep.test_completed(
            &b,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(5.0),
            None,
        );
        rep.test_started(&a);
        rep.test_completed(
            &a,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(5.0),
            None,
        );
        rep.finish(&[], false, &ReporterSession::new(0));

        let content = std::fs::read_to_string(&path).unwrap();
        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        let names: Vec<&str> = v["results"]["tests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        // test_a must come before test_b
        assert!(
            names.iter().position(|n| n.contains("test_a"))
                < names.iter().position(|n| n.contains("test_b")),
            "JSON output must be sorted by node_id: got {:?}",
            names
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{CollectError, DurationMs, ExitCode};

    #[test]
    fn test_slowest_block_included_when_show_durations_set() {
        use crate::reporter::stats::RunStats;
        use crate::types::NodeId;
        let mut stats = RunStats::new();
        stats.record_timing(
            &NodeId::from_raw("tests/test_foo.py::test_slow"),
            DurationMs::new(500.0),
        );
        stats.record_timing(
            &NodeId::from_raw("tests/test_foo.py::test_fast"),
            DurationMs::new(10.0),
        );
        let slowest = stats.slowest(1);
        assert_eq!(slowest.len(), 1);
        assert_eq!(slowest[0].node_id.as_ref(), "tests/test_foo.py::test_slow");
        assert!((slowest[0].duration_ms.as_f64() - 500.0).abs() < 0.01);
    }

    // ── StandardReporter / standard_finish ─────────────────────────────────────

    #[test]
    fn test_standard_finish_returns_zero_on_clean_run() {
        struct Stub {
            opts: ReporterOpts,
        }
        impl StandardReporter for Stub {
            fn pre_finish(&mut self) {}
            fn run_opts(&self) -> &ReporterOpts {
                &self.opts
            }
        }
        let mut s = Stub {
            opts: ReporterOptsBuilder::new().build(),
        };
        let session = ReporterSession::new(0);
        let vote = standard_finish(&mut s, &session, &[], false);
        assert_eq!(vote.code(), ExitCode::Success);
    }

    #[test]
    fn test_print_collect_errors_is_noop_when_empty() {
        use super::print::print_collect_errors;
        // smoke test — no panic when slice is empty
        print_collect_errors(&[], false);
        print_collect_errors(&[], true);
    }

    #[test]
    fn test_print_collect_errors_prints_when_non_empty() {
        use super::print::print_collect_errors;
        // smoke test — function must not panic when given errors
        let errors = vec![CollectError::PyError("import failed".to_string())];
        print_collect_errors(&errors, false);
        print_collect_errors(&errors, true);
    }

    #[test]
    fn test_print_strict_abort_does_not_panic_with_violations() {
        let lines = vec![
            "tests/test_foo.py::test_x — bare assert at lines 5, 12".to_string(),
            "marker 'db' has no description".to_string(),
        ];
        // Should not panic. Output goes to stdout (captured in test harness).
        print_strict_abort(&lines, false);
    }

    // ── CompositeReporter ─────────────────────────────────────────────────────

    #[test]
    fn test_composite_reporter_finish_returns_max_exit_code() {
        struct StubReporter(ExitVote);
        impl Reporter for StubReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                self.0
            }
        }

        let mut composite = CompositeReporter::new(
            vec![
                Box::new(StubReporter(ExitVote::Code(ExitCode::Success))),
                Box::new(StubReporter(ExitVote::Code(ExitCode::Failure))),
                Box::new(StubReporter(ExitVote::Code(ExitCode::Success))),
            ],
            0,
        );
        assert_eq!(
            composite
                .finish(&[], false, &ReporterSession::new(0))
                .code(),
            ExitCode::Failure,
            "CompositeReporter::finish should return the max exit code across all reporters"
        );
    }

    #[test]
    fn test_composite_reporter_finish_with_no_reporters_returns_zero() {
        let mut composite = CompositeReporter::new(vec![], 0);
        assert_eq!(
            composite
                .finish(&[], false, &ReporterSession::new(0))
                .code(),
            ExitCode::Success,
            "CompositeReporter with no reporters should return exit code 0"
        );
    }

    #[test]
    fn test_composite_reporter_finish_ignores_abstentions() {
        struct StubReporter(ExitVote);
        impl Reporter for StubReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                self.0
            }
        }

        let mut composite = CompositeReporter::new(
            vec![
                Box::new(StubReporter(ExitVote::Code(ExitCode::Failure))),
                Box::new(StubReporter(ExitVote::Abstain)),
                Box::new(StubReporter(ExitVote::Abstain)),
            ],
            0,
        );
        assert_eq!(
            composite
                .finish(&[], false, &ReporterSession::new(0))
                .code(),
            ExitCode::Failure,
            "CompositeReporter::finish should ignore Abstain votes"
        );
    }

    #[test]
    fn test_composite_reporter_finish_all_abstain_returns_zero() {
        struct StubReporter(ExitVote);
        impl Reporter for StubReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                self.0
            }
        }

        let mut composite = CompositeReporter::new(
            vec![
                Box::new(StubReporter(ExitVote::Abstain)),
                Box::new(StubReporter(ExitVote::Abstain)),
            ],
            0,
        );
        assert_eq!(
            composite
                .finish(&[], false, &ReporterSession::new(0))
                .code(),
            ExitCode::Success,
            "CompositeReporter with all Abstain should return exit code 0"
        );
    }

    // ── make_reporter ─────────────────────────────────────────────────────────

    #[test]
    fn test_make_reporter_returns_single_reporter_when_tty_and_no_extras() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = make_reporter(opts, true, None, None, vec![]);
        assert_eq!(
            reporter.finish(&[], false, &ReporterSession::new(0)).code(),
            ExitCode::Success
        );
    }

    #[test]
    fn test_make_reporter_returns_single_reporter_when_ci_and_no_extras() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = make_reporter(opts, false, None, None, vec![]);
        assert_eq!(
            reporter.finish(&[], false, &ReporterSession::new(0)).code(),
            ExitCode::Success
        );
    }

    #[test]
    fn test_make_reporter_wraps_in_composite_when_json_path_given() {
        use camino::Utf8PathBuf;
        let opts = ReporterOptsBuilder::new().build();
        let path = Utf8PathBuf::from("/tmp/oxitest_report.json");
        let mut reporter = make_reporter(opts, false, Some(path), None, vec![]);
        assert_eq!(
            reporter.finish(&[], false, &ReporterSession::new(0)).code(),
            ExitCode::Success
        );
    }

    #[test]
    fn test_make_reporter_wraps_in_composite_when_plugin_reporters_given() {
        use crate::types::{TestItem, TestOutcome};
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::Arc;

        struct CountingStub(Arc<AtomicUsize>);
        impl Reporter for CountingStub {
            fn test_started(&mut self, _: &crate::types::TestItem) {
                self.0.fetch_add(1, Ordering::Relaxed);
            }
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
                self.0.fetch_add(1, Ordering::Relaxed);
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                ExitVote::Abstain
            }
        }
        let calls = Arc::new(AtomicUsize::new(0));
        let opts = ReporterOptsBuilder::new().build();
        let plugins: Vec<Box<dyn Reporter>> = vec![Box::new(CountingStub(Arc::clone(&calls)))];
        let mut reporter = make_reporter(opts, true, None, None, plugins);
        let item = TestItem::builder("tests/test_foo.py", "test_x").arc();
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(1.0), None);
        assert!(
            calls.load(Ordering::Relaxed) >= 2,
            "plugin reporter should receive test_started and test_completed events"
        );
        assert_eq!(
            reporter.finish(&[], false, &ReporterSession::new(0)).code(),
            ExitCode::Success
        );
    }

    #[test]
    fn test_composite_reporter_dispatches_test_started_to_all_reporters() {
        use crate::types::TestItem;
        use std::sync::{Arc, Mutex};

        struct CountingReporter(Arc<Mutex<usize>>);
        impl Reporter for CountingReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {
                *self.0.lock().unwrap() += 1;
            }
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                ExitVote::Abstain
            }
        }

        let count = Arc::new(Mutex::new(0usize));
        let mut composite = CompositeReporter::new(
            vec![
                Box::new(CountingReporter(Arc::clone(&count))),
                Box::new(CountingReporter(Arc::clone(&count))),
            ],
            0,
        );
        composite.test_started(&TestItem::builder("tests/test_foo.py", "test_foo").build());
        assert_eq!(
            *count.lock().unwrap(),
            2,
            "test_started should be dispatched to every inner reporter"
        );
    }

    // ── remove_if_flaky ──────────────────────────────────────────────────────

    #[test]
    fn test_remove_if_flaky_removes_matching_entry() {
        use crate::types::{OutcomeKind, TestItem, TestOutcome};

        let item = TestItem::builder("tests/test_foo.py", "test_a").arc();
        let mut deferred = vec![
            "tests/test_foo.py::test_a".to_string(),
            "tests/test_foo.py::test_b".to_string(),
        ];
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
            original: OutcomeKind::Failed,
        };
        super::remove_if_flaky(&mut deferred, &outcome, &item, |d, target| {
            d.contains(target)
        });
        assert_eq!(deferred, vec!["tests/test_foo.py::test_b"]);
    }

    #[test]
    fn test_remove_if_flaky_noop_for_non_flaky_outcome() {
        use crate::types::{TestItem, TestOutcome};

        let item = TestItem::builder("tests/test_foo.py", "test_a").arc();
        let mut deferred = vec!["tests/test_foo.py::test_a".to_string()];
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        super::remove_if_flaky(&mut deferred, &outcome, &item, |d, target| {
            d.contains(target)
        });
        assert_eq!(
            deferred,
            vec!["tests/test_foo.py::test_a"],
            "non-flaky outcomes must not remove deferred entries"
        );
    }

    #[test]
    fn test_remove_if_flaky_works_with_tuple_vec() {
        use crate::types::{OutcomeKind, TestItem, TestOutcome};
        use std::sync::Arc;

        let item_a = TestItem::builder("tests/test_foo.py", "test_a").arc();
        let item_b = TestItem::builder("tests/test_foo.py", "test_b").arc();
        let mut deferred: Vec<(Arc<TestItem>, TestOutcome, DurationMs)> = vec![
            (
                Arc::clone(&item_a),
                TestOutcome::Passed {
                    no_message_lines: vec![],
                },
                DurationMs::new(1.0),
            ),
            (
                Arc::clone(&item_b),
                TestOutcome::Passed {
                    no_message_lines: vec![],
                },
                DurationMs::new(2.0),
            ),
        ];
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
            original: OutcomeKind::Failed,
        };
        super::remove_if_flaky(&mut deferred, &outcome, &item_a, |entry, target| {
            entry.0.node_id.as_ref() == target
        });
        assert_eq!(deferred.len(), 1);
        assert_eq!(deferred[0].0.node_id.as_ref(), item_b.node_id.as_ref());
    }

    #[test]
    fn test_composite_reporter_dispatches_teardown_warning_to_all() {
        use std::sync::{Arc, Mutex};

        struct WarningCollector(Arc<Mutex<Vec<(String, String)>>>);
        impl Reporter for WarningCollector {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &ReporterSession) -> ExitVote {
                ExitVote::Abstain
            }
            fn record_teardown_warning(&mut self, context: &str, error: &str) {
                self.0
                    .lock()
                    .unwrap()
                    .push((context.to_string(), error.to_string()));
            }
        }

        let warnings = Arc::new(Mutex::new(Vec::new()));
        let mut composite = CompositeReporter::new(
            vec![
                Box::new(WarningCollector(Arc::clone(&warnings))),
                Box::new(WarningCollector(Arc::clone(&warnings))),
            ],
            0,
        );
        composite.record_teardown_warning("end_module(tests/test_foo.py)", "RuntimeError: boom");
        let collected = warnings.lock().unwrap();
        assert_eq!(
            collected.len(),
            2,
            "teardown warning should be dispatched to every inner reporter"
        );
        assert_eq!(collected[0].0, "end_module(tests/test_foo.py)");
        assert_eq!(collected[0].1, "RuntimeError: boom");
    }
}
