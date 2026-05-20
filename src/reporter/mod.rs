//! Test result reporting — terminal output, CI formatting, and JSON export.
//!
//! Defines the [`Reporter`] trait and its concrete implementations:
//! [`TtyReporter`](tty::TtyReporter) (progress bars, colors),
//! [`CiReporter`](ci::CiReporter) (GitHub Actions annotations),
//! [`JsonReporter`](json::JsonReporter) (CTRF format), and
//! [`PyPluginReporter`](plugin::PyPluginReporter) (user-supplied Python plugins).

use std::io::{self, Write};

use crate::types::CollectError;

mod ci;
mod colors;
mod exit;
mod format;
pub(crate) mod json;
mod options;
pub(crate) mod plugin;
mod stats;
mod tty;

#[cfg(test)]
pub(crate) mod test_helpers;

pub use ci::CiReporter;
pub(crate) use exit::compute_exit_code;
pub use options::{ReporterOpts, ReporterOptsBuilder};
pub use tty::TtyReporter;

use format::{fmt_summary, fmt_tip_block, fmt_warning_block};
use stats::RunStats;

// Re-export so ci.rs and tty.rs can reach it via `super::sep_width()`
pub(crate) use format::sep_width;

// ─── ParametrizeBuffer ───────────────────────────────────────────────────────

/// Buffers results for a single parametrized function group.
pub(crate) struct ParametrizeBuffer {
    pub fn_name: String,
    pub results: Vec<(crate::types::TestItem, crate::types::TestOutcome, f64)>,
}

impl ParametrizeBuffer {
    pub fn new(fn_name: String) -> Self {
        Self {
            fn_name,
            results: Vec::new(),
        }
    }

    pub fn push(
        &mut self,
        item: crate::types::TestItem,
        outcome: crate::types::TestOutcome,
        ms: f64,
    ) {
        self.results.push((item, outcome, ms));
    }

    pub fn total_ms(&self) -> f64 {
        self.results.iter().map(|(_, _, ms)| ms).sum()
    }

    pub fn any_failed(&self) -> bool {
        self.results.iter().any(|(_, o, _)| o.is_hard_failure())
    }

    pub fn passed_count(&self) -> usize {
        use crate::types::TestOutcome;
        self.results
            .iter()
            .filter(|(_, o, _)| matches!(o, TestOutcome::Passed { .. }))
            .count()
    }
}

// ─── Trait ───────────────────────────────────────────────────────────────────

pub trait Reporter {
    fn test_started(&mut self, item: &crate::types::TestItem);
    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: f64,
    );
    fn finish(&mut self, collect_errors: &[CollectError], interrupted: bool) -> i32;

    /// Record a teardown warning (default: no-op).
    /// `context` identifies what failed (e.g. "end_module(path)" or "end_session").
    /// `error` is the stringified error message.
    fn record_teardown_warning(&mut self, _context: &str, _error: &str) {}
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

/// Fans reporter events to every inner reporter; returns the highest exit code.
pub struct CompositeReporter {
    reporters: Vec<Box<dyn Reporter>>,
}

impl CompositeReporter {
    pub fn new(reporters: Vec<Box<dyn Reporter>>) -> Self {
        Self { reporters }
    }
}

impl Reporter for CompositeReporter {
    fn test_started(&mut self, item: &crate::types::TestItem) {
        for r in &mut self.reporters {
            r.test_started(item);
        }
    }

    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: f64,
    ) {
        for r in &mut self.reporters {
            r.test_completed(item, outcome, duration_ms);
        }
    }

    fn finish(&mut self, collect_errors: &[CollectError], interrupted: bool) -> i32 {
        self.reporters
            .iter_mut()
            .map(|r| r.finish(collect_errors, interrupted))
            .max()
            .unwrap_or(0)
    }

    fn record_teardown_warning(&mut self, context: &str, error: &str) {
        for r in &mut self.reporters {
            r.record_teardown_warning(context, error);
        }
    }
}

pub fn make_reporter(
    opts: ReporterOpts,
    is_tty: bool,
    json_path: Option<camino::Utf8PathBuf>,
    plugin_reporters: Vec<Box<dyn Reporter>>,
) -> Box<dyn Reporter> {
    let json_reporter =
        json_path.map(|path| Box::new(json::JsonReporter::new(path)) as Box<dyn Reporter>);

    let primary: Box<dyn Reporter> = if is_tty {
        Box::new(TtyReporter::new(opts))
    } else {
        Box::new(CiReporter::new(opts))
    };

    let mut reporters = vec![primary];
    if let Some(jr) = json_reporter {
        reporters.push(jr);
    }
    reporters.extend(plugin_reporters);

    if reporters.len() == 1 {
        reporters.into_iter().next().unwrap()
    } else {
        Box::new(CompositeReporter::new(reporters))
    }
}

#[cfg(test)]
mod json_tests {
    use super::*;
    use crate::types::{TestItem, TestOutcome};
    use camino::Utf8PathBuf;

    // Uses "tests/test_mod.py" (not the shared helper's "tests/test_foo.py") because
    // CTRF output tests assert on the exact module path that appears in JSON output.
    fn make_item(name: &str) -> TestItem {
        TestItem {
            node_id: crate::types::NodeId::new("tests/test_mod.py", name, None),
            module_path: Utf8PathBuf::from("tests/test_mod.py"),
            fn_name: name.to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        }
    }

    #[test]
    fn test_json_reporter_writes_ctrf_on_finish() {
        use crate::reporter::json::JsonReporter;
        use tempfile::NamedTempFile;

        let tmp = NamedTempFile::new().unwrap();
        let path = camino::Utf8Path::from_path(tmp.path()).unwrap().to_owned();

        let mut rep = JsonReporter::new(path.clone());

        let item = make_item("test_passes");
        rep.test_started(&item);
        rep.test_completed(
            &item,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            12.5,
        );
        rep.finish(&[], false);

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

        let item = make_item("test_fails");
        rep.test_started(&item);
        rep.test_completed(
            &item,
            &TestOutcome::Failed {
                message: "assert x == 1".to_string(),
                file: "tests/test_mod.py".to_string(),
                lineno: 5,
                source_line: "assert x == 1".to_string(),
                left: "0".to_string(),
                right: "1".to_string(),
                op: "==".to_string(),
                frames: vec![],
            },
            8.0,
        );
        rep.finish(&[], false);

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
        let b = make_item("test_b");
        let a = make_item("test_a");
        rep.test_started(&b);
        rep.test_completed(
            &b,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            5.0,
        );
        rep.test_started(&a);
        rep.test_completed(
            &a,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            5.0,
        );
        rep.finish(&[], false);

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

pub(crate) fn print_collected(total: usize) {
    println!(
        "collected {} item{}\n",
        total,
        if total == 1 { "" } else { "s" }
    );
}

pub(crate) fn print_summary_section(
    stats: &RunStats,
    opts: &ReporterOpts,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> i32 {
    let tip_block = fmt_tip_block(&stats.tip_lines, opts.show_tips, opts.use_color);
    let warn_block = fmt_warning_block(&stats.warning_msgs, opts.show_warnings, opts.use_color);
    let summary = fmt_summary(stats, collect_errors.len(), opts.use_color);
    println!("\n{}", summary);
    if let Some(n) = opts.show_durations {
        let slowest = stats.slowest(n);
        if !slowest.is_empty() {
            println!(
                "\n{}",
                colors::color_dim(&format!("slowest {} tests", slowest.len()), opts.use_color)
            );
            for (node_id, ms) in &slowest {
                println!("  {:>8.2}ms  {}", ms, node_id);
            }
        }
    }
    if !tip_block.is_empty() {
        print!("{}", tip_block);
    }
    if !warn_block.is_empty() {
        print!("{}", warn_block);
    }
    if !tip_block.is_empty() || !warn_block.is_empty() {
        println!(
            "{}",
            colors::color_dim(&"═".repeat(sep_width()), opts.use_color)
        );
    }
    flush();
    compute_exit_code(stats, collect_errors.len(), interrupted)
}

fn flush() {
    let _ = io::stdout().flush();
}

pub(crate) fn print_collect_errors(collect_errors: &[CollectError], use_color: bool) {
    if !collect_errors.is_empty() {
        println!("\nCOLLECTION ERRORS");
        println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
        let last = collect_errors.len() - 1;
        for (i, ce) in collect_errors.iter().enumerate() {
            println!("{}", ce);
            if i < last {
                println!();
            }
        }
    }
}

pub(crate) fn print_strict_abort(formatted_lines: &[String], use_color: bool) {
    println!("\nSTRICT VIOLATIONS");
    println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
    for line in formatted_lines {
        println!("  {}", line);
    }
    println!("strict violations found — aborting (exit 3)");
}

pub(crate) trait StandardReporter {
    fn pre_finish(&mut self);
    fn run_stats(&self) -> &RunStats;
    fn run_opts(&self) -> &ReporterOpts;
}

pub(crate) fn standard_finish(
    r: &mut impl StandardReporter,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> i32 {
    r.pre_finish();
    print_collect_errors(collect_errors, r.run_opts().use_color);
    print_summary_section(r.run_stats(), r.run_opts(), collect_errors, interrupted)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::stats::RunStats;

    #[test]
    fn test_slowest_block_included_when_show_durations_set() {
        use crate::reporter::stats::RunStats;
        let mut stats = RunStats::new();
        stats.record_timing("tests/test_foo.py::test_slow", 500.0);
        stats.record_timing("tests/test_foo.py::test_fast", 10.0);
        let slowest = stats.slowest(1);
        assert_eq!(slowest.len(), 1);
        assert_eq!(slowest[0].0, "tests/test_foo.py::test_slow");
        assert!((slowest[0].1 - 500.0).abs() < 0.01);
    }

    // ── StandardReporter / standard_finish ─────────────────────────────────────

    #[test]
    fn test_standard_finish_calls_pre_finish_and_returns_zero_on_clean_run() {
        struct Stub {
            stats: RunStats,
            opts: ReporterOpts,
            pre_finish_called: bool,
        }
        impl StandardReporter for Stub {
            fn pre_finish(&mut self) {
                self.pre_finish_called = true;
            }
            fn run_stats(&self) -> &RunStats {
                &self.stats
            }
            fn run_opts(&self) -> &ReporterOpts {
                &self.opts
            }
        }
        let mut s = Stub {
            stats: RunStats::new(),
            opts: ReporterOptsBuilder::new().build(),
            pre_finish_called: false,
        };
        let code = standard_finish(&mut s, &[], false);
        assert_eq!(code, 0);
        assert!(s.pre_finish_called);
    }

    #[test]
    fn test_print_collect_errors_is_noop_when_empty() {
        // smoke test — no panic when slice is empty
        print_collect_errors(&[], false);
        print_collect_errors(&[], true);
    }

    #[test]
    fn test_print_collect_errors_prints_when_non_empty() {
        use crate::types::CollectError;
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
        super::print_strict_abort(&lines, false);
    }

    // ── ParametrizeBuffer ─────────────────────────────────────────────────────

    #[test]
    fn test_parametrize_buffer_total_ms_sums_all_durations() {
        use crate::reporter::test_helpers::make_item;
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new("test_add".to_string());
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            10.0,
        );
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            25.5,
        );
        assert!(
            (buf.total_ms() - 35.5).abs() < 0.001,
            "total_ms should sum all durations, got {}",
            buf.total_ms()
        );
    }

    #[test]
    fn test_parametrize_buffer_any_failed_true_when_failure_present() {
        use crate::reporter::test_helpers::{make_failed, make_item};
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new("test_add".to_string());
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            1.0,
        );
        buf.push(
            make_item("test_add"),
            make_failed("oops", "t.py", 1, "assert x"),
            1.0,
        );
        assert!(
            buf.any_failed(),
            "any_failed should be true when a Failed outcome is present"
        );
    }

    #[test]
    fn test_parametrize_buffer_any_failed_false_when_all_pass_or_skip() {
        use crate::reporter::test_helpers::make_item;
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new("test_add".to_string());
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            1.0,
        );
        buf.push(
            make_item("test_add"),
            TestOutcome::Skipped {
                reason: "not ready".to_string(),
            },
            0.0,
        );
        assert!(
            !buf.any_failed(),
            "any_failed should be false when no hard failures are present"
        );
    }

    #[test]
    fn test_parametrize_buffer_passed_count_counts_only_passed_outcomes() {
        use crate::reporter::test_helpers::{make_failed, make_item};
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new("test_add".to_string());
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            1.0,
        );
        buf.push(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            1.0,
        );
        buf.push(
            make_item("test_add"),
            make_failed("oops", "t.py", 1, "assert x"),
            1.0,
        );
        buf.push(
            make_item("test_add"),
            TestOutcome::Skipped {
                reason: "skip".to_string(),
            },
            0.0,
        );
        assert_eq!(
            buf.passed_count(),
            2,
            "passed_count should count only Passed outcomes, not Failed or Skipped"
        );
    }

    // ── CompositeReporter ─────────────────────────────────────────────────────

    #[test]
    fn test_composite_reporter_finish_returns_max_exit_code() {
        struct StubReporter(i32);
        impl Reporter for StubReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: f64,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> i32 {
                self.0
            }
        }

        let mut composite = CompositeReporter::new(vec![
            Box::new(StubReporter(0)),
            Box::new(StubReporter(1)),
            Box::new(StubReporter(0)),
        ]);
        assert_eq!(
            composite.finish(&[], false),
            1,
            "CompositeReporter::finish should return the max exit code across all reporters"
        );
    }

    #[test]
    fn test_composite_reporter_finish_with_no_reporters_returns_zero() {
        let mut composite = CompositeReporter::new(vec![]);
        assert_eq!(
            composite.finish(&[], false),
            0,
            "CompositeReporter with no reporters should return exit code 0"
        );
    }

    // ── make_reporter ─────────────────────────────────────────────────────────

    #[test]
    fn test_make_reporter_returns_single_reporter_when_tty_and_no_extras() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = make_reporter(opts, true, None, vec![]);
        assert_eq!(reporter.finish(&[], false), 0);
    }

    #[test]
    fn test_make_reporter_returns_single_reporter_when_ci_and_no_extras() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = make_reporter(opts, false, None, vec![]);
        assert_eq!(reporter.finish(&[], false), 0);
    }

    #[test]
    fn test_make_reporter_wraps_in_composite_when_json_path_given() {
        use camino::Utf8PathBuf;
        let opts = ReporterOptsBuilder::new().build();
        let path = Utf8PathBuf::from("/tmp/oxitest_report.json");
        let mut reporter = make_reporter(opts, false, Some(path), vec![]);
        assert_eq!(reporter.finish(&[], false), 0);
    }

    #[test]
    fn test_make_reporter_wraps_in_composite_when_plugin_reporters_given() {
        use crate::reporter::test_helpers::make_item;
        use crate::types::TestOutcome;
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
                _: f64,
            ) {
                self.0.fetch_add(1, Ordering::Relaxed);
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> i32 {
                0
            }
        }
        let calls = Arc::new(AtomicUsize::new(0));
        let opts = ReporterOptsBuilder::new().build();
        let plugins: Vec<Box<dyn Reporter>> = vec![Box::new(CountingStub(Arc::clone(&calls)))];
        let mut reporter = make_reporter(opts, true, None, plugins);
        let item = make_item("test_x");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, 1.0);
        assert!(
            calls.load(Ordering::Relaxed) >= 2,
            "plugin reporter should receive test_started and test_completed events"
        );
        assert_eq!(reporter.finish(&[], false), 0);
    }

    #[test]
    fn test_composite_reporter_dispatches_test_started_to_all_reporters() {
        use crate::reporter::test_helpers::make_item;
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
                _: f64,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> i32 {
                0
            }
        }

        let count = Arc::new(Mutex::new(0usize));
        let mut composite = CompositeReporter::new(vec![
            Box::new(CountingReporter(Arc::clone(&count))),
            Box::new(CountingReporter(Arc::clone(&count))),
        ]);
        composite.test_started(&make_item("test_foo"));
        assert_eq!(
            *count.lock().unwrap(),
            2,
            "test_started should be dispatched to every inner reporter"
        );
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
                _: f64,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> i32 {
                0
            }
            fn record_teardown_warning(&mut self, context: &str, error: &str) {
                self.0
                    .lock()
                    .unwrap()
                    .push((context.to_string(), error.to_string()));
            }
        }

        let warnings = Arc::new(Mutex::new(Vec::new()));
        let mut composite = CompositeReporter::new(vec![
            Box::new(WarningCollector(Arc::clone(&warnings))),
            Box::new(WarningCollector(Arc::clone(&warnings))),
        ]);
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
