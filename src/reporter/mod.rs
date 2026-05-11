use std::io::{self, Write};

use crate::types::CollectError;

mod ci;
mod colors;
mod format;
pub(crate) mod json;
mod stats;
mod tty;

#[cfg(test)]
mod test_helpers;

pub use ci::CiReporter;
pub use tty::TtyReporter;

use format::{fmt_summary, fmt_tip_block, fmt_warning_block};
use stats::RunStats;

// Re-export so ci.rs and tty.rs can reach it via `super::sep_width()`
pub(crate) use format::sep_width;

// ─── Options ─────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct ReporterOpts {
    pub(crate) total: usize,
    pub(crate) use_color: bool,
    pub(crate) tb: crate::config::TbStyle,
    pub(crate) show_tips: bool,
    pub(crate) show_warnings: bool,
    pub(crate) verbose: bool,
    pub(crate) show_durations: Option<usize>,
    pub(crate) strict_suite_lines: Vec<String>,
}

// ─── Builder ──────────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct ReporterOptsBuilder {
    total: usize,
    use_color: bool,
    tb: crate::config::TbStyle,
    show_tips: bool,
    show_warnings: bool,
    verbose: bool,
    show_durations: Option<usize>,
    strict_suite_lines: Vec<String>,
}

impl ReporterOptsBuilder {
    /// Sensible defaults for tests: total=0, use_color=false, tb=Short,
    /// show_tips=false, show_warnings=false, verbose=false.
    pub fn new() -> Self {
        Self {
            total: 0,
            use_color: false,
            tb: crate::config::TbStyle::Short,
            show_tips: false,
            show_warnings: false,
            verbose: false,
            show_durations: None,
            strict_suite_lines: vec![],
        }
    }

    /// Derive all fields from CLI. Sets total=0 — call `.total(n)` before `.build()`.
    pub fn from_cli(cli: &crate::config::Cli, use_color: bool) -> Self {
        Self {
            total: 0,
            use_color,
            tb: cli.tb.clone(),
            show_tips: cli.tips || cli.verbose,
            show_warnings: cli.warnings || cli.verbose,
            verbose: cli.verbose,
            show_durations: cli.durations,
            strict_suite_lines: vec![],
        }
    }

    pub fn total(self, n: usize) -> Self {
        Self { total: n, ..self }
    }

    pub fn verbose(self, v: bool) -> Self {
        Self { verbose: v, ..self }
    }

    pub fn strict_suite_lines(self, lines: Vec<String>) -> Self {
        Self {
            strict_suite_lines: lines,
            ..self
        }
    }

    pub fn build(self) -> ReporterOpts {
        ReporterOpts {
            total: self.total,
            use_color: self.use_color,
            tb: self.tb,
            show_tips: self.show_tips,
            show_warnings: self.show_warnings,
            verbose: self.verbose,
            show_durations: self.show_durations,
            strict_suite_lines: self.strict_suite_lines,
        }
    }
}

impl Default for ReporterOptsBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl ReporterOptsBuilder {
    pub fn tb(self, tb: crate::config::TbStyle) -> Self {
        Self { tb, ..self }
    }
}

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
}

pub fn make_reporter(
    opts: ReporterOpts,
    is_tty: bool,
    json_path: Option<camino::Utf8PathBuf>,
) -> Box<dyn Reporter> {
    let json_reporter =
        json_path.map(|path| Box::new(json::JsonReporter::new(path)) as Box<dyn Reporter>);

    let primary: Box<dyn Reporter> = if is_tty {
        Box::new(TtyReporter::new(opts))
    } else {
        Box::new(CiReporter::new(opts))
    };

    match json_reporter {
        Some(jr) => Box::new(CompositeReporter::new(vec![primary, jr])),
        None => primary,
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

fn compute_exit_code(stats: &RunStats, collect_err_count: usize, interrupted: bool) -> i32 {
    if collect_err_count > 0 {
        return 3;
    }
    if interrupted {
        return 2;
    }
    if stats.failed > 0
        || stats.errored > 0
        || stats.xpassed_strict > 0
        || stats.timeout > 0
        || stats.strict_suite > 0
    {
        return 1;
    }
    0
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

pub(crate) fn print_strict_abort(violations: &[crate::strict::StrictViolation], use_color: bool) {
    println!("\nSTRICT VIOLATIONS");
    println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
    for v in violations {
        println!("  {}", crate::strict::format_violation_line(v));
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
    fn test_exit_code_zero_when_all_pass() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 0, false), 0);
    }

    #[test]
    fn test_exit_code_one_when_failed() {
        let mut stats = RunStats::new();
        stats.failed = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), 1);
    }

    #[test]
    fn test_exit_code_one_when_errored() {
        let mut stats = RunStats::new();
        stats.errored = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), 1);
    }

    #[test]
    fn test_exit_code_one_when_xpassed_strict() {
        let mut stats = RunStats::new();
        stats.xpassed_strict = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), 1);
    }

    #[test]
    fn test_exit_code_one_when_timeout() {
        let mut stats = RunStats::new();
        stats.timeout = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), 1);
    }

    #[test]
    fn test_exit_code_two_when_interrupted() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 0, true), 2);
    }

    #[test]
    fn test_exit_code_three_when_collect_error() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 1, false), 3);
    }

    #[test]
    fn test_exit_code_one_when_strict_suite_violations() {
        let mut stats = RunStats::new();
        stats.strict_suite = 2;
        assert_eq!(compute_exit_code(&stats, 0, false), 1);
    }

    #[test]
    fn test_exit_code_collect_error_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.failed = 1;
        stats.timeout = 1;
        // collect_err_count > 0 must return 3, even with failures
        assert_eq!(compute_exit_code(&stats, 1, false), 3);
    }

    #[test]
    fn test_exit_code_interrupted_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.failed = 1;
        // interrupted must return 2, even with failures
        assert_eq!(compute_exit_code(&stats, 0, true), 2);
    }

    // ── ReporterOptsBuilder ────────────────────────────────────────────────────

    fn test_cli(verbose: bool, tips: bool, warnings: bool) -> crate::config::Cli {
        use clap::Parser;
        let base = crate::config::Cli::try_parse_from(["oxitest"])
            .expect("default CLI parse must succeed");
        crate::config::Cli {
            verbose,
            tips,
            warnings,
            ..base
        }
    }

    #[test]
    fn test_builder_new_defaults() {
        let opts = ReporterOptsBuilder::new().build();
        assert_eq!(opts.total, 0);
        assert!(!opts.use_color);
        assert!(!opts.show_tips);
        assert!(!opts.show_warnings);
        assert!(!opts.verbose);
        assert_eq!(opts.tb, crate::config::TbStyle::Short);
    }

    #[test]
    fn test_builder_total_override() {
        let opts = ReporterOptsBuilder::new().total(42).build();
        assert_eq!(opts.total, 42);
    }

    #[test]
    fn test_builder_verbose_override() {
        let opts = ReporterOptsBuilder::new().verbose(true).build();
        assert!(opts.verbose);
    }

    #[test]
    fn test_builder_tb_override() {
        let opts = ReporterOptsBuilder::new()
            .tb(crate::config::TbStyle::No)
            .build();
        assert_eq!(opts.tb, crate::config::TbStyle::No);
    }

    #[test]
    fn test_builder_from_cli_verbose_implies_show_tips_and_warnings() {
        let cli = test_cli(true, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_tips);
        assert!(opts.show_warnings);
        assert!(opts.verbose);
    }

    #[test]
    fn test_builder_from_cli_tips_flag_without_verbose() {
        let cli = test_cli(false, true, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_tips);
        assert!(!opts.show_warnings);
        assert!(!opts.verbose);
    }

    #[test]
    fn test_builder_from_cli_warnings_flag_without_verbose() {
        let cli = test_cli(false, false, true);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(!opts.show_tips);
        assert!(opts.show_warnings);
    }

    #[test]
    fn test_builder_from_cli_use_color_passed_through() {
        let cli = test_cli(false, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, true).build();
        assert!(opts.use_color);
    }

    #[test]
    fn test_builder_from_cli_total_default_is_zero() {
        let cli = test_cli(false, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert_eq!(opts.total, 0);
    }

    #[test]
    fn test_builder_durations_from_cli() {
        use clap::Parser;
        let cli = crate::config::Cli::try_parse_from(["oxitest", "--durations", "5"]).unwrap();
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert_eq!(opts.show_durations, Some(5));
    }

    #[test]
    fn test_builder_durations_absent_is_none() {
        use clap::Parser;
        let cli = crate::config::Cli::try_parse_from(["oxitest"]).unwrap();
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_durations.is_none());
    }

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

    #[test]
    fn test_builder_clone_allows_two_builds_from_same_base() {
        let base = ReporterOptsBuilder::new().total(5);
        let a = base.clone().verbose(false).build();
        let b = base.verbose(true).build();
        assert_eq!(a.total, 5);
        assert!(!a.verbose);
        assert_eq!(b.total, 5);
        assert!(b.verbose);
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
        use crate::strict::StrictViolation;
        use crate::types::NodeId;
        let violations = vec![
            StrictViolation::BareAssert {
                node_id: NodeId::from_raw("tests/test_foo.py::test_x"),
                lines: vec![5, 12],
            },
            StrictViolation::MarkerNoDescription {
                marker_name: "db".to_string(),
            },
        ];
        // Should not panic. Output goes to stdout (captured in test harness).
        super::print_strict_abort(&violations, false);
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
}
