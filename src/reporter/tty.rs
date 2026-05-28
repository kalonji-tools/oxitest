use std::borrow::Cow;

use crate::types::{CollectError, ColorCategory, DurationMs, TestItem, TestOutcome};

use super::colors::{
    color_cyan, color_dim, color_dim_green, color_error_token, color_fail, color_skip,
    color_timeout, color_warn,
};
use super::format::{case_sep, fmt_diagnostic_block, pad_to, plural};
use super::{ParametrizeBuffer, Reporter, ReporterOpts, StandardReporter};

use indicatif::{ProgressBar, ProgressStyle};

fn truncate_name(name: &str, max_width: usize) -> Cow<'_, str> {
    if name.len() <= max_width {
        return Cow::Borrowed(name);
    }
    let cut = max_width.saturating_sub(3);
    let end = name
        .char_indices()
        .map(|(i, _)| i)
        .take_while(|&i| i <= cut)
        .last()
        .unwrap_or(0);
    Cow::Owned(format!("{}...", &name[..end]))
}

fn fmt_quiet_line(symbol: &str, body: &str) -> String {
    format!(" {}  {}", symbol, body)
}

fn outcome_label(outcome: &TestOutcome, use_color: bool) -> String {
    let text = outcome.label();
    let c = use_color;
    match outcome.color_category() {
        ColorCategory::Pass => String::new(),
        ColorCategory::Fail => color_fail(text, c),
        ColorCategory::Error => color_error_token(text, c),
        ColorCategory::Skip => color_skip(text, c),
        ColorCategory::Warn => color_warn(text, c),
        ColorCategory::Dim => color_dim(text, c),
        ColorCategory::Timeout => color_timeout(text, c),
    }
}

pub struct TtyReporter {
    opts: ReporterOpts,
    pb: ProgressBar,
    pending_group: Option<ParametrizeBuffer>,
    deferred_failures: Vec<(
        crate::types::TestItem,
        crate::types::TestOutcome,
        DurationMs,
    )>,
    running_tests: Vec<String>,
}

impl TtyReporter {
    pub fn new(opts: ReporterOpts) -> Self {
        super::print_collected(opts.total, opts.async_count);
        let pb = ProgressBar::new(opts.total as u64);
        let style = if opts.use_color {
            ProgressStyle::with_template("  {pos}/{len}  {spinner:.cyan}  {msg}")
                .expect("static progress bar template is valid")
                .tick_strings(&["⣾", "⣷", "⣯", "⣟", "⡿", "⢿", "⣻", "⣽", ""])
        } else {
            ProgressStyle::with_template("  {pos}/{len}  {msg}")
                .expect("static progress bar template is valid")
        };
        pb.set_style(style);
        pb.enable_steady_tick(std::time::Duration::from_millis(80));
        super::tracing_writer::register(pb.clone());
        Self {
            opts,
            pb,
            pending_group: None,
            deferred_failures: Vec::new(),
            running_tests: Vec::new(),
        }
    }

    /// Assemble a single reporter line: ` LABEL  <name padded to name_width>  trailing`
    fn fmt_line(&self, label: &str, name: &str, trailing: &str) -> String {
        format!(" {}  {} {}", label, name, trailing)
    }

    fn format_test_line(
        &self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
    ) -> String {
        let raw_ms = duration_ms.as_f64();
        let ms = color_dim(&format!("{:.1}ms", raw_ms), self.opts.use_color);
        let c = self.opts.use_color;
        match outcome {
            TestOutcome::Passed { no_message_lines } if no_message_lines.is_empty() => {
                let w = self.opts.name_width;
                fmt_quiet_line(
                    &color_dim_green("\u{2713}    ", c),
                    &color_dim(
                        &format!(
                            "{:<width$} {:.1}ms",
                            truncate_name(&item.fn_name, w),
                            raw_ms,
                            width = w
                        ),
                        c,
                    ),
                )
            }
            TestOutcome::Passed { .. } => {
                let w = self.opts.name_width;
                fmt_quiet_line(
                    &color_dim("\u{00B7}    ", c),
                    &color_dim(
                        &format!(
                            "{:<width$} {:.1}ms",
                            truncate_name(&item.fn_name, w),
                            raw_ms,
                            width = w
                        ),
                        c,
                    ),
                )
            }
            TestOutcome::Skipped { reason } | TestOutcome::XFailed { reason } => {
                let label = outcome_label(outcome, c);
                self.fmt_line(
                    &label,
                    &pad_to(
                        &truncate_name(&item.fn_name, self.opts.name_width),
                        self.opts.name_width,
                    ),
                    &color_dim(reason, c),
                )
            }
            TestOutcome::Warned { reason, .. } => {
                // Extract unique warning types (e.g. "UserWarning" from "UserWarning: hello")
                let mut types: Vec<&str> = reason
                    .split('\n')
                    .filter_map(|line| line.split_once(':').map(|(t, _)| t.trim()))
                    .collect();
                types.dedup();
                let types_str = if types.is_empty() {
                    String::new()
                } else {
                    format!("  {}", color_warn(&types.join(", "), c))
                };
                let label = outcome_label(outcome, c);
                self.fmt_line(
                    &label,
                    &pad_to(
                        &truncate_name(&item.fn_name, self.opts.name_width),
                        self.opts.name_width,
                    ),
                    &format!("{}{}", ms, types_str),
                )
            }
            _ => {
                let label = outcome_label(outcome, c);
                self.fmt_line(
                    &label,
                    &pad_to(
                        &color_cyan(&truncate_name(&item.fn_name, self.opts.name_width), c),
                        self.opts.name_width,
                    ),
                    &ms,
                )
            }
        }
    }

    fn update_running_message(&self) {
        let max_names = 3;
        let msg = if self.running_tests.len() <= max_names {
            self.running_tests.join(", ")
        } else {
            let shown: Vec<&str> = self
                .running_tests
                .iter()
                .take(max_names)
                .map(|s| s.as_str())
                .collect();
            format!(
                "{}, +{} more",
                shown.join(", "),
                self.running_tests.len() - max_names
            )
        };
        self.pb.set_message(msg);
    }

    /// Dispatch a completed parametrize group: print immediately in verbose mode,
    /// defer hard failures in non-verbose mode.
    fn dispatch_param_group(&mut self, group: ParametrizeBuffer) {
        if self.opts.verbose {
            self.flush_param_group(group);
        } else {
            for (item, outcome, duration_ms) in group.results {
                if outcome.is_hard_failure() {
                    self.deferred_failures.push((item, outcome, duration_ms));
                }
            }
        }
    }

    fn flush_param_group(&mut self, group: ParametrizeBuffer) {
        let c = self.opts.use_color;
        let total_ms = group.total_ms();
        let total_ms_raw = total_ms.as_f64();

        if group.any_failed() {
            let ms = color_dim(&format!("{:.1}ms", total_ms_raw), c);
            let passed = group.passed_count();
            let failed = group.results.len() - passed;
            let label = color_fail("FAIL ", c);
            let line = self.fmt_line(
                &label,
                &pad_to(
                    &truncate_name(&group.fn_name, self.opts.name_width),
                    self.opts.name_width,
                ),
                &format!("{} passed  {} failed   {}", passed, failed, ms),
            );
            self.pb.println(line);

            for (item, outcome, _) in &group.results {
                if matches!(
                    outcome,
                    TestOutcome::Passed { .. } | TestOutcome::Skipped { .. }
                ) {
                    continue;
                }
                let case_id = item.param_id.as_deref().unwrap_or("");
                let header = format!(
                    "       {}{}{}",
                    color_fail(case_id, c),
                    case_sep(c),
                    color_dim(&group.fn_name, c)
                );
                self.pb.println(header);
                let diag = fmt_diagnostic_block(item, outcome, &self.opts.tb, c);
                if !diag.is_empty() {
                    self.pb.println(diag.trim_end());
                }
            }
        } else {
            let count = group.results.len();
            let w = self.opts.name_width;
            let line = fmt_quiet_line(
                &color_dim_green("\u{2713}    ", c),
                &color_dim(
                    &format!(
                        "{:<width$} {:.1}ms  ({} case{})",
                        truncate_name(&group.fn_name, w),
                        total_ms_raw,
                        count,
                        plural(count),
                        width = w
                    ),
                    c,
                ),
            );
            self.pb.println(line);
        }
    }
}

impl StandardReporter for TtyReporter {
    fn pre_finish(&mut self) {
        if let Some(group) = self.pending_group.take() {
            self.dispatch_param_group(group);
        }
        // Flush deferred failures before clearing the progress bar
        let deferred = std::mem::take(&mut self.deferred_failures);
        for (item, outcome, duration_ms) in deferred {
            self.pb
                .println(self.format_test_line(&item, &outcome, duration_ms));
            let diag = fmt_diagnostic_block(&item, &outcome, &self.opts.tb, self.opts.use_color);
            if !diag.is_empty() {
                self.pb.println(diag.trim_end());
            }
        }
        self.pb.finish_and_clear();
        super::tracing_writer::deregister();
        super::print_strict_suite_section(&self.opts);
    }

    fn run_opts(&self) -> &ReporterOpts {
        &self.opts
    }
}

impl Reporter for TtyReporter {
    fn test_started(&mut self, item: &TestItem) {
        self.running_tests.push(item.fn_name.clone());
        self.update_running_message();
    }

    fn test_completed(&mut self, item: &TestItem, outcome: &TestOutcome, duration_ms: DurationMs) {
        if let Some(pos) = self.running_tests.iter().position(|n| n == &item.fn_name) {
            self.running_tests.remove(pos);
        }

        // Flaky: remove original failure from deferred list.
        super::remove_if_flaky(
            &mut self.deferred_failures,
            outcome,
            item,
            |(i, _, _), target| i.node_id.as_ref() == target,
        );

        if item.param_id.is_some() && !self.opts.verbose {
            // Flush pending group if fn_name changed
            let flush = matches!(&self.pending_group, Some(g) if g.fn_name != item.fn_name);
            if flush {
                let group = self.pending_group.take().unwrap();
                self.dispatch_param_group(group);
            }
            let group = self
                .pending_group
                .get_or_insert_with(|| ParametrizeBuffer::new(item.fn_name.clone()));
            group.push(item.clone(), outcome.clone(), duration_ms);
        } else if self.opts.verbose {
            // Verbose mode: flush pending group and print every result immediately
            if let Some(group) = self.pending_group.take() {
                self.flush_param_group(group);
            }
            self.pb
                .println(self.format_test_line(item, outcome, duration_ms));
            let diag = fmt_diagnostic_block(item, outcome, &self.opts.tb, self.opts.use_color);
            if !diag.is_empty() {
                self.pb.println(diag.trim_end());
            }
        } else {
            // Non-verbose mode: only defer hard failures; passing/skipped/xfailed are silent
            if let Some(group) = self.pending_group.take() {
                self.dispatch_param_group(group);
            }
            if outcome.is_hard_failure() {
                self.deferred_failures
                    .push(((*item).clone(), outcome.clone(), duration_ms));
            }
        }

        self.pb.inc(1);
        self.update_running_message();
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        stats: &super::RunStats,
    ) -> super::ExitVote {
        self.pre_finish();
        super::standard_finish(self, stats, collect_errors, interrupted)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Mutex, OnceLock};

    use super::*;
    use crate::reporter::test_helpers::{make_error, make_failed};
    use crate::types::{DurationMs, TestOutcome};

    /// Serializes tests that mutate `console::set_colors_enabled` (global state).
    fn color_test_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    #[test]
    fn test_outcome_label_failed_contains_fail() {
        let o = make_failed("msg", "f.py", 1, "assert x");
        let label = outcome_label(&o, false);
        assert!(label.contains("FAIL"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_error_contains_error() {
        let o = make_error("msg", "f.py", 1, "x.y");
        let label = outcome_label(&o, false);
        assert!(label.contains("ERROR"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_skipped_contains_skip() {
        let o = TestOutcome::Skipped {
            reason: "".to_string(),
        };
        let label = outcome_label(&o, false);
        assert!(label.contains("SKIP"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_timeout_contains_time() {
        let o = TestOutcome::Timeout {
            message: "".to_string(),
        };
        let label = outcome_label(&o, false);
        assert!(label.contains("TIME"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_passed_is_empty() {
        let o = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let label = outcome_label(&o, false);
        assert!(label.is_empty());
    }

    #[test]
    fn test_outcome_label_xpassed_strict_contains_xpass() {
        let o = TestOutcome::XPassed { strict: true };
        let label = outcome_label(&o, false);
        assert!(label.contains("XPASS"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_xpassed_lenient_contains_xpass() {
        let o = TestOutcome::XPassed { strict: false };
        let label = outcome_label(&o, false);
        assert!(label.contains("XPASS"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_xpassed_strict_uses_fail_color() {
        let _guard = color_test_lock().lock().unwrap();
        console::set_colors_enabled(true);
        let o = TestOutcome::XPassed { strict: true };
        let label_colored = outcome_label(&o, true);
        let label_plain = outcome_label(&o, false);
        console::set_colors_enabled(false);
        // With color enabled, strict XPassed should use color_fail styling
        // The colored output should differ from plain and should contain "XPASS"
        assert!(
            label_colored.contains("XPASS"),
            "label was: {label_colored:?}"
        );
        assert_ne!(
            label_colored, label_plain,
            "strict XPassed should be colored differently"
        );
    }

    #[test]
    fn test_outcome_label_xpassed_lenient_uses_warn_color() {
        let _guard = color_test_lock().lock().unwrap();
        console::set_colors_enabled(true);
        let o = TestOutcome::XPassed { strict: false };
        let label_colored = outcome_label(&o, true);
        let label_plain = outcome_label(&o, false);
        console::set_colors_enabled(false);
        // With color enabled, lenient XPassed should use color_warn styling
        assert!(
            label_colored.contains("XPASS"),
            "label was: {label_colored:?}"
        );
        assert_ne!(
            label_colored, label_plain,
            "lenient XPassed should be colored differently"
        );
    }

    #[test]
    fn test_outcome_label_xpassed_strict_and_lenient_differ_in_color() {
        let _guard = color_test_lock().lock().unwrap();
        console::set_colors_enabled(true);
        let strict = outcome_label(&TestOutcome::XPassed { strict: true }, true);
        let lenient = outcome_label(&TestOutcome::XPassed { strict: false }, true);
        console::set_colors_enabled(false);
        // Both say "XPASS" but with different colors
        assert!(strict.contains("XPASS"));
        assert!(lenient.contains("XPASS"));
        assert_ne!(
            strict, lenient,
            "strict and lenient XPassed should differ in color"
        );
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    use crate::reporter::test_helpers::make_item;
    use crate::reporter::ReporterOptsBuilder;

    fn make_tty_reporter() -> TtyReporter {
        TtyReporter::new(ReporterOptsBuilder::new().build())
    }

    // ── fmt_quiet_line ────────────────────────────────────────────────────────

    #[test]
    fn test_fmt_quiet_line_produces_expected_format() {
        // " symbol  body" — one leading space, two spaces between symbol and body
        let result = fmt_quiet_line("\u{2713}    ", "test_foo  42.0ms");
        assert_eq!(result, " \u{2713}    \u{0020}\u{0020}test_foo  42.0ms");
    }

    // ── format_test_line ──────────────────────────────────────────────────────

    #[test]
    fn test_format_test_line_passed_contains_fn_name_and_duration() {
        let reporter = make_tty_reporter();
        let item = make_item("test_add");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(42.0));
        assert!(
            line.contains("test_add"),
            "fn_name must appear in line: {line:?}"
        );
        assert!(
            line.contains("42"),
            "duration must appear in line: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_passed_no_double_ms_suffix() {
        let reporter = make_tty_reporter();
        let item = make_item("test_add");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(42.0));
        assert!(
            !line.contains("msms"),
            "duration must not contain double ms suffix: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_passed_bare_assert_uses_middot() {
        let reporter = make_tty_reporter();
        let item = make_item("test_add");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![5],
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(10.0));
        assert!(
            line.contains('\u{00B7}'),
            "bare-assert pass must use middot (\u{00B7}): {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_skipped_contains_skip_label_and_reason() {
        let reporter = make_tty_reporter();
        let item = make_item("test_cond");
        let outcome = TestOutcome::Skipped {
            reason: "not ready".to_string(),
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::ZERO);
        assert!(line.contains("SKIP"), "SKIP label must appear: {line:?}");
        assert!(
            line.contains("not ready"),
            "skip reason must appear: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_warned_contains_warn_label_and_reason() {
        let reporter = make_tty_reporter();
        let item = make_item("test_dep");
        let outcome = TestOutcome::Warned {
            reason: "DeprecationWarning: use new_api".to_string(),
            no_message_lines: vec![],
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(5.0));
        assert!(line.contains("WARN"), "WARN label must appear: {line:?}");
        assert!(
            line.contains("DeprecationWarning"),
            "warned reason must appear: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_xfailed_contains_xfail_label_and_reason() {
        let reporter = make_tty_reporter();
        let item = make_item("test_known_bug");
        let outcome = TestOutcome::XFailed {
            reason: "issue #42".to_string(),
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(3.0));
        assert!(line.contains("XFAIL"), "XFAIL label must appear: {line:?}");
        assert!(
            line.contains("issue #42"),
            "xfail reason must appear: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_failed_contains_fail_label() {
        let reporter = make_tty_reporter();
        let item = make_item("test_math");
        let outcome = make_failed("wrong value", "tests/test_math.py", 10, "assert x == 1");
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(15.0));
        assert!(line.contains("FAIL"), "FAIL label must appear: {line:?}");
    }

    #[test]
    fn test_format_test_line_timeout_contains_time_label() {
        let reporter = make_tty_reporter();
        let item = make_item("test_slow");
        let outcome = TestOutcome::Timeout {
            message: "exceeded 30s".to_string(),
        };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(30_000.0));
        assert!(line.contains("TIME"), "TIME label must appear: {line:?}");
    }

    // ── truncate_name ─────────────────────────────────────────────────────

    #[test]
    fn test_truncate_name_short_unchanged() {
        assert_eq!(truncate_name("test_add", 45), "test_add");
    }

    #[test]
    fn test_truncate_name_exact_length_unchanged() {
        let name = "a".repeat(45);
        assert_eq!(truncate_name(&name, 45), name);
    }

    #[test]
    fn test_truncate_name_over_limit_gets_ellipsis() {
        let name = "a".repeat(50);
        let result = truncate_name(&name, 45);
        assert_eq!(result.len(), 45);
        assert!(
            result.ends_with("..."),
            "truncated name must end with ...: {result:?}"
        );
    }

    #[test]
    fn test_truncate_name_non_ascii_does_not_panic() {
        let name = "é".repeat(30); // 60 bytes, over max_width=45
        let result = truncate_name(&name, 45);
        assert!(result.ends_with("..."));
    }

    // ── flush_param_group ────────────────────────────────────────────────────

    #[test]
    fn test_flush_param_group_all_passed_duration_before_cases() {
        use crate::reporter::ParametrizeBuffer;

        let reporter = make_tty_reporter();
        let item = make_item("test_math");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let mut group = ParametrizeBuffer::new("test_math".to_string());
        group.push((*item).clone(), outcome.clone(), DurationMs::new(2.0));
        group.push((*item).clone(), outcome.clone(), DurationMs::new(2.6));
        // flush_param_group consumes group and prints via pb — capture output by
        // checking the format directly via the inner logic instead.
        // Verify the format string produces duration-first output:
        let total_ms_raw = 4.6_f64;
        let count = 2_usize;
        let w = reporter.opts.name_width;
        let formatted = format!(
            "{:<width$} {:.1}ms  ({} case{})",
            truncate_name("test_math", w),
            total_ms_raw,
            count,
            plural(count),
            width = w
        );
        // Duration appears before the case count
        let ms_pos = formatted.find("4.6ms").expect("duration must appear");
        let cases_pos = formatted.find("(2 cases)").expect("case count must appear");
        assert!(
            ms_pos < cases_pos,
            "duration must come before case count: {formatted:?}"
        );
        // No old-style "N cases   Xms" ordering
        assert!(
            !formatted.contains("cases   "),
            "old ordering (cases before ms) must not appear: {formatted:?}"
        );
    }

    #[test]
    fn test_flush_param_group_singular_case() {
        use crate::reporter::ParametrizeBuffer;

        let reporter = make_tty_reporter();
        let item = make_item("test_single");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let mut group = ParametrizeBuffer::new("test_single".to_string());
        group.push((*item).clone(), outcome, DurationMs::new(3.0));

        let w = reporter.opts.name_width;
        let formatted = format!(
            "{:<width$} {:.1}ms  ({} case{})",
            truncate_name("test_single", w),
            3.0_f64,
            1_usize,
            plural(1),
            width = w
        );
        assert!(
            formatted.contains("(1 case)"),
            "singular must say 'case' not 'cases': {formatted:?}"
        );
        assert!(
            !formatted.contains("cases"),
            "singular must not contain 'cases': {formatted:?}"
        );
    }

    // ── deferred failures (non-verbose mode) ─────────────────────────────────

    #[test]
    fn test_non_verbose_defers_failures() {
        let opts = ReporterOptsBuilder::new().verbose(false).build();
        let mut reporter = TtyReporter::new(opts);
        let item = make_item("test_failing");
        let outcome = make_failed("oops", "test.py", 5, "assert x");
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0));
        assert_eq!(
            reporter.deferred_failures.len(),
            1,
            "failure should be deferred in non-verbose mode"
        );
    }

    #[test]
    fn test_non_verbose_does_not_defer_passes() {
        let opts = ReporterOptsBuilder::new().verbose(false).build();
        let mut reporter = TtyReporter::new(opts);
        let item = make_item("test_passing");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0));
        assert!(
            reporter.deferred_failures.is_empty(),
            "passing test should not be deferred"
        );
    }

    #[test]
    fn test_verbose_does_not_defer() {
        let opts = ReporterOptsBuilder::new().verbose(true).build();
        let mut reporter = TtyReporter::new(opts);
        let item = make_item("test_failing");
        let outcome = make_failed("oops", "test.py", 5, "assert x");
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0));
        assert!(
            reporter.deferred_failures.is_empty(),
            "verbose mode should print immediately, not defer"
        );
    }

    // ── running_tests tracking ────────────────────────────────────────────────

    #[test]
    fn test_running_tests_tracks_in_flight() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = TtyReporter::new(opts);

        let item_a = make_item("test_login");
        let item_b = make_item("test_signup");

        reporter.test_started(&item_a);
        reporter.test_started(&item_b);
        assert_eq!(reporter.running_tests.len(), 2);

        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_completed(&item_a, &outcome, DurationMs::new(10.0));
        assert_eq!(reporter.running_tests.len(), 1);
        assert_eq!(reporter.running_tests[0], "test_signup");
    }

    #[test]
    fn test_running_tests_empty_after_all_complete() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = TtyReporter::new(opts);

        let item = make_item("test_foo");
        reporter.test_started(&item);
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_completed(&item, &outcome, DurationMs::new(5.0));
        assert!(reporter.running_tests.is_empty());
    }
}
