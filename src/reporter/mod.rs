//! Test result reporting — terminal output, CI formatting, and JSON export.
//!
//! Defines the [`Reporter`] trait and its concrete implementations:
//! [`TtyReporter`](tty::TtyReporter) (progress bars, colors),
//! [`CiReporter`](ci::CiReporter) (GitHub Actions annotations),
//! [`JsonReporter`](json::JsonReporter) (CTRF format), and
//! [`PyPluginReporter`](plugin::PyPluginReporter) (user-supplied Python plugins).

use std::io::{self, Write};

use crate::types::{CollectError, DurationMs, ExitCode};

mod ci;
mod colors;
mod exit;
mod format;
pub(crate) mod json;
pub(crate) mod junit;
mod options;
pub(crate) mod plugin;
mod stats;
pub(crate) mod tracing_writer;
mod tty;

#[cfg(test)]
pub(crate) mod test_helpers;

pub use ci::CiReporter;
pub(crate) use exit::compute_exit_code;
pub use options::{ReporterOpts, ReporterOptsBuilder};
pub use tty::TtyReporter;

use format::{fmt_summary, fmt_tip_block, fmt_warning_block};
pub(crate) use stats::RunStats;

// Re-export so ci.rs and tty.rs can reach it via `super::sep_width()`
pub(crate) use format::sep_width;

// ─── ParametrizeBuffer ───────────────────────────────────────────────────────

/// Buffers results for all cases of a single parametrized test function.
///
/// Parametrized cases are accumulated here so the reporter can emit a combined
/// summary line (e.g. `test_add — 3 passed, 1 failed`) once all cases finish,
/// rather than one line per case.
pub(crate) struct ParametrizeBuffer {
    pub fn_name: String,
    pub results: Vec<(
        crate::types::TestItem,
        crate::types::TestOutcome,
        DurationMs,
    )>,
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
        ms: DurationMs,
    ) {
        self.results.push((item, outcome, ms));
    }

    pub fn total_ms(&self) -> DurationMs {
        self.results
            .iter()
            .fold(DurationMs::ZERO, |acc, (_, _, ms)| acc + *ms)
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

// ─── ExitVote ────────────────────────────────────────────────────────────────

/// Exit code vote from a reporter.
#[derive(Debug, Clone, Copy)]
pub enum ExitVote {
    /// Reporter does not influence exit code.
    Abstain,
    /// Reporter votes for this exit code.
    Code(ExitCode),
}

impl ExitVote {
    /// Extract the exit code, treating `Abstain` as `ExitCode::Success`.
    pub fn code(self) -> ExitCode {
        match self {
            ExitVote::Abstain => ExitCode::Success,
            ExitVote::Code(c) => c,
        }
    }
}

// ─── Trait ───────────────────────────────────────────────────────────────────

/// Event sink for test results, progress, and the final summary.
///
/// Lifecycle per test: `test_started` → `test_completed`. After all tests,
/// `finish` is called once with any collection errors and an interrupted flag.
/// `finish` returns an [`ExitVote`] that contributes to the process exit code.
///
/// Implementers: [`TtyReporter`], [`CiReporter`], [`JsonReporter`], [`PyPluginReporter`],
/// [`CompositeReporter`].
pub trait Reporter {
    fn test_started(&mut self, item: &crate::types::TestItem);
    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: DurationMs,
    );
    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        stats: &RunStats,
    ) -> ExitVote;

    /// Record a teardown warning (default: no-op).
    /// `context` identifies what failed (e.g. "end_module(path)" or "end_session").
    /// `error` is the stringified error message.
    fn record_teardown_warning(&mut self, _context: &str, _error: &str) {}
}

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

// ─── Shared helpers ───────────────────────────────────────────────────────────

/// Fans all reporter events to a list of inner reporters.
///
/// Owns the single [`RunStats`] for the run: records stats once in
/// `test_completed` and passes them to sub-reporters via `finish`.
/// `finish` collects [`ExitVote`]s from every inner reporter and returns the
/// maximum code voted (treating `Abstain` as 0).
pub struct CompositeReporter {
    reporters: Vec<Box<dyn Reporter>>,
    stats: RunStats,
    strict_suite_count: usize,
}

impl CompositeReporter {
    pub fn new(reporters: Vec<Box<dyn Reporter>>, strict_suite_count: usize) -> Self {
        Self {
            reporters,
            stats: RunStats::new(),
            strict_suite_count,
        }
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
        duration_ms: DurationMs,
    ) {
        self.stats.record(item, outcome);
        self.stats.record_timing(item.node_id.as_ref(), duration_ms);
        for r in &mut self.reporters {
            r.test_completed(item, outcome, duration_ms);
        }
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        _stats: &RunStats,
    ) -> ExitVote {
        self.stats.record_strict_suite(self.strict_suite_count);
        self.reporters
            .iter_mut()
            .map(|r| r.finish(collect_errors, interrupted, &self.stats))
            .filter_map(|v| match v {
                ExitVote::Code(c) => Some(c),
                ExitVote::Abstain => None,
            })
            .max()
            .map_or(ExitVote::Code(ExitCode::Success), ExitVote::Code)
    }

    fn record_teardown_warning(&mut self, context: &str, error: &str) {
        self.stats
            .warning_msgs
            .push((context.to_string(), error.to_string()));
        for r in &mut self.reporters {
            r.record_teardown_warning(context, error);
        }
    }
}

/// Build the active reporter from resolved options.
///
/// Chooses [`TtyReporter`] or [`CiReporter`] based on `is_tty`, then wraps
/// all reporters (including optional JSON, JUnit, and plugin reporters) in a
/// [`CompositeReporter`] which owns the single [`RunStats`] for the run.
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
    use crate::types::{LineNo, TestItem, TestOutcome};
    use camino::Utf8PathBuf;

    // Uses "tests/test_mod.py" (not the shared helper's "tests/test_foo.py") because
    // CTRF output tests assert on the exact module path that appears in JSON output.
    fn make_item(name: &str) -> TestItem {
        TestItem {
            node_id: crate::types::NodeId::new("tests/test_mod.py", name, None),
            module_path: Utf8PathBuf::from("tests/test_mod.py"),
            fn_name: name.to_string(),
            lineno: LineNo::new(1),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
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
            DurationMs::new(12.5),
        );
        rep.finish(&[], false, &RunStats::new());

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
                file: Utf8PathBuf::from("tests/test_mod.py"),
                lineno: LineNo::new(5),
                source_line: "assert x == 1".to_string(),
                left: "0".to_string(),
                right: "1".to_string(),
                op: "==".to_string(),
                frames: vec![],
            },
            DurationMs::new(8.0),
        );
        rep.finish(&[], false, &RunStats::new());

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
            DurationMs::new(5.0),
        );
        rep.test_started(&a);
        rep.test_completed(
            &a,
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(5.0),
        );
        rep.finish(&[], false, &RunStats::new());

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

pub(crate) fn print_collected(total: usize, async_count: usize) {
    if async_count > 0 {
        println!(
            "collected {} item{} ({} async)\n",
            total,
            if total == 1 { "" } else { "s" },
            async_count,
        );
    } else {
        println!(
            "collected {} item{}\n",
            total,
            if total == 1 { "" } else { "s" }
        );
    }
}

pub(crate) fn print_summary_section(
    stats: &RunStats,
    opts: &ReporterOpts,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitCode {
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

pub(crate) fn print_strict_suite_section(opts: &ReporterOpts) {
    if !opts.strict_suite_lines.is_empty() {
        let hdr = format!(
            "STRICT {}",
            colors::color_dim(
                &"═".repeat(sep_width().saturating_sub("STRICT ".len())),
                opts.use_color,
            )
        );
        println!("\n{}", hdr);
        for line in &opts.strict_suite_lines {
            println!("  {}", line);
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
    fn run_opts(&self) -> &ReporterOpts;
}

pub(crate) fn standard_finish(
    r: &mut impl StandardReporter,
    stats: &RunStats,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitVote {
    print_collect_errors(collect_errors, r.run_opts().use_color);
    ExitVote::Code(print_summary_section(
        stats,
        r.run_opts(),
        collect_errors,
        interrupted,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::stats::RunStats;

    #[test]
    fn test_slowest_block_included_when_show_durations_set() {
        use crate::reporter::stats::RunStats;
        let mut stats = RunStats::new();
        stats.record_timing("tests/test_foo.py::test_slow", DurationMs::new(500.0));
        stats.record_timing("tests/test_foo.py::test_fast", DurationMs::new(10.0));
        let slowest = stats.slowest(1);
        assert_eq!(slowest.len(), 1);
        assert_eq!(slowest[0].0, "tests/test_foo.py::test_slow");
        assert!((slowest[0].1 - 500.0).abs() < 0.01);
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
        let stats = RunStats::new();
        let vote = standard_finish(&mut s, &stats, &[], false);
        assert_eq!(vote.code(), ExitCode::Success);
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
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(10.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(25.5),
        );
        assert!(
            (buf.total_ms().as_f64() - 35.5).abs() < 0.001,
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
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            make_failed("oops", "t.py", 1, "assert x"),
            DurationMs::new(1.0),
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
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            TestOutcome::Skipped {
                reason: "not ready".to_string(),
            },
            DurationMs::ZERO,
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
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            make_failed("oops", "t.py", 1, "assert x"),
            DurationMs::new(1.0),
        );
        buf.push(
            (*make_item("test_add")).clone(),
            TestOutcome::Skipped {
                reason: "skip".to_string(),
            },
            DurationMs::ZERO,
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
        struct StubReporter(ExitVote);
        impl Reporter for StubReporter {
            fn test_started(&mut self, _: &crate::types::TestItem) {}
            fn test_completed(
                &mut self,
                _: &crate::types::TestItem,
                _: &crate::types::TestOutcome,
                _: DurationMs,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
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
            composite.finish(&[], false, &RunStats::new()).code(),
            ExitCode::Failure,
            "CompositeReporter::finish should return the max exit code across all reporters"
        );
    }

    #[test]
    fn test_composite_reporter_finish_with_no_reporters_returns_zero() {
        let mut composite = CompositeReporter::new(vec![], 0);
        assert_eq!(
            composite.finish(&[], false, &RunStats::new()).code(),
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
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
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
            composite.finish(&[], false, &RunStats::new()).code(),
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
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
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
            composite.finish(&[], false, &RunStats::new()).code(),
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
            reporter.finish(&[], false, &RunStats::new()).code(),
            ExitCode::Success
        );
    }

    #[test]
    fn test_make_reporter_returns_single_reporter_when_ci_and_no_extras() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = make_reporter(opts, false, None, None, vec![]);
        assert_eq!(
            reporter.finish(&[], false, &RunStats::new()).code(),
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
            reporter.finish(&[], false, &RunStats::new()).code(),
            ExitCode::Success
        );
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
                _: DurationMs,
            ) {
                self.0.fetch_add(1, Ordering::Relaxed);
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
                ExitVote::Abstain
            }
        }
        let calls = Arc::new(AtomicUsize::new(0));
        let opts = ReporterOptsBuilder::new().build();
        let plugins: Vec<Box<dyn Reporter>> = vec![Box::new(CountingStub(Arc::clone(&calls)))];
        let mut reporter = make_reporter(opts, true, None, None, plugins);
        let item = make_item("test_x");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(1.0));
        assert!(
            calls.load(Ordering::Relaxed) >= 2,
            "plugin reporter should receive test_started and test_completed events"
        );
        assert_eq!(
            reporter.finish(&[], false, &RunStats::new()).code(),
            ExitCode::Success
        );
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
                _: DurationMs,
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
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
        composite.test_started(&make_item("test_foo"));
        assert_eq!(
            *count.lock().unwrap(),
            2,
            "test_started should be dispatched to every inner reporter"
        );
    }

    // ── remove_if_flaky ──────────────────────────────────────────────────────

    #[test]
    fn test_remove_if_flaky_removes_matching_entry() {
        use crate::reporter::test_helpers::make_item;
        use crate::types::TestOutcome;

        let item = make_item("test_a");
        let mut deferred = vec![
            "tests/test_foo.py::test_a".to_string(),
            "tests/test_foo.py::test_b".to_string(),
        ];
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
        };
        super::remove_if_flaky(&mut deferred, &outcome, &item, |d, target| {
            d.contains(target)
        });
        assert_eq!(deferred, vec!["tests/test_foo.py::test_b"]);
    }

    #[test]
    fn test_remove_if_flaky_noop_for_non_flaky_outcome() {
        use crate::reporter::test_helpers::make_item;
        use crate::types::TestOutcome;

        let item = make_item("test_a");
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
        use crate::reporter::test_helpers::make_item;
        use crate::types::{TestItem, TestOutcome};
        use std::sync::Arc;

        let item_a = make_item("test_a");
        let item_b = make_item("test_b");
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
            ) {
            }
            fn finish(&mut self, _: &[CollectError], _: bool, _: &RunStats) -> ExitVote {
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
