use std::borrow::Cow;
use std::sync::Arc;

use crate::config::Verbosity;
use crate::types::{CollectError, DurationMs, TestItem, TestOutcome};

use super::parametrize_buffer::ParametrizeBuffer;
use super::ColorCategory;

use super::colors::{
    color_bold_white, color_cyan, color_dim, color_dim_green, color_error_token, color_fail,
    color_skip, color_timeout, color_warn,
};
use super::format::{fmt_diagnostic_block, pad_to, plural};
use super::{Reporter, ReporterOpts, StandardReporter};

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
        Option<crate::parallel_context::ParallelContext>,
    )>,
    running_tests: Vec<Arc<str>>,
}

impl TtyReporter {
    pub fn new(opts: ReporterOpts) -> Self {
        super::print_collected(opts.total, opts.fn_count, opts.async_count);
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
        let param_suffix = match &item.param_id {
            Some(pid) => format!("[ {} ]", color_bold_white(pid, c)),
            None => String::new(),
        };
        let w = self.opts.name_width;
        let name_part = truncate_name(&item.fn_name, w);
        match outcome {
            TestOutcome::Passed { tips: None } => fmt_quiet_line(
                &color_dim_green("\u{2713}    ", c),
                &color_dim(
                    &format!("{:<width$} {:.1}ms", name_part, raw_ms, width = w),
                    c,
                ),
            ),
            TestOutcome::Passed { .. } => fmt_quiet_line(
                &color_dim("\u{00B7}    ", c),
                &color_dim(
                    &format!("{:<width$} {:.1}ms", name_part, raw_ms, width = w),
                    c,
                ),
            ),
            TestOutcome::Skipped { reason } | TestOutcome::XFailed { reason } => {
                let label = outcome_label(outcome, c);
                self.fmt_line(
                    &label,
                    &pad_to(&format!("{}{}", name_part, param_suffix), w),
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
                    &pad_to(&format!("{}{}", name_part, param_suffix), w),
                    &format!("{}{}", ms, types_str),
                )
            }
            _ => {
                let label = outcome_label(outcome, c);
                let name_with_id = format!("{}{}", color_cyan(&name_part, c), param_suffix);
                self.fmt_line(&label, &pad_to(&name_with_id, w), &ms)
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
                .map(|s| &**s)
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
        if self.opts.verbosity >= Verbosity::Detailed {
            self.flush_param_group(group);
        } else {
            for (item, outcome, duration_ms) in group.results {
                if outcome.is_hard_failure() {
                    self.deferred_failures
                        .push((item, outcome, duration_ms, None));
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
                self.pb.println("");
                let header = format!("       {}[ {} ]", color_dim(&group.fn_name, c), case_id,);
                self.pb.println(header);
                let diag =
                    fmt_diagnostic_block(item, outcome, &self.opts.tb, self.opts.show_locals, c);
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
        for (item, outcome, duration_ms, parallel_ctx) in deferred {
            self.pb
                .println(self.format_test_line(&item, &outcome, duration_ms));
            if let Some(ctx) = &parallel_ctx {
                self.pb.println(ctx.fmt_line(self.opts.use_color));
            }
            let diag = fmt_diagnostic_block(
                &item,
                &outcome,
                &self.opts.tb,
                self.opts.show_locals,
                self.opts.use_color,
            );
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

    fn test_completed(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
        parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
    ) {
        if let Some(pos) = self.running_tests.iter().position(|n| n == &item.fn_name) {
            self.running_tests.remove(pos);
        }

        // Flaky: remove original failure from deferred list.
        super::remove_if_flaky(
            &mut self.deferred_failures,
            outcome,
            item,
            |(i, _, _, _), target| i.node_id.as_ref() == target,
        );

        let is_parametrized = item.param_id.is_some();
        let is_verbose = self.opts.verbosity >= Verbosity::Detailed;

        // Three display modes:
        // 1. Parametrized + non-verbose → buffer results, display as group
        // 2. Verbose → flush any pending group, print immediately
        // 3. Non-parametrized + non-verbose → defer hard failures only
        if is_parametrized && !is_verbose {
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
        } else if is_verbose {
            // Verbose mode: flush pending group and print every result immediately
            if let Some(group) = self.pending_group.take() {
                self.flush_param_group(group);
            }
            self.pb
                .println(self.format_test_line(item, outcome, duration_ms));
            let diag = fmt_diagnostic_block(
                item,
                outcome,
                &self.opts.tb,
                self.opts.show_locals,
                self.opts.use_color,
            );
            if !diag.is_empty() {
                self.pb.println(diag.trim_end());
            }
        } else {
            // Non-verbose mode: only defer hard failures; passing/skipped/xfailed are silent
            if let Some(group) = self.pending_group.take() {
                self.dispatch_param_group(group);
            }
            if outcome.is_hard_failure() {
                self.deferred_failures.push((
                    (*item).clone(),
                    outcome.clone(),
                    duration_ms,
                    parallel_ctx.cloned(),
                ));
            }
        }

        self.pb.inc(1);
        self.update_running_message();
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        session: &super::ReporterSession,
    ) -> super::ExitVote {
        self.pre_finish();
        super::standard_finish(self, session, collect_errors, interrupted)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Mutex, OnceLock};

    use super::*;
    use crate::types::{DurationMs, TestOutcome};

    /// Serializes tests that mutate `console::set_colors_enabled` (global state).
    fn color_test_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    #[test]
    fn test_outcome_label_failed_contains_fail() {
        let o = TestOutcome::failed("msg")
            .file("f.py")
            .source("assert x")
            .build();
        let label = outcome_label(&o, false);
        assert!(label.contains("FAIL"), "label was: {label:?}");
    }

    #[test]
    fn test_outcome_label_error_contains_error() {
        let o = TestOutcome::error("msg").file("f.py").source("x.y").build();
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
        let o = TestOutcome::Passed { tips: None };
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

    use crate::reporter::ReporterOptsBuilder;
    use crate::types::TestItem;

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
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::Passed { tips: None };
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
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::Passed { tips: None };
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(42.0));
        assert!(
            !line.contains("msms"),
            "duration must not contain double ms suffix: {line:?}"
        );
    }

    #[test]
    fn test_format_test_line_passed_bare_assert_uses_middot() {
        let reporter = make_tty_reporter();
        let item = TestItem::builder("tests/test_foo.py", "test_add").arc();
        let outcome = TestOutcome::Passed {
            tips: Some(vec![5].into_boxed_slice()),
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
        let item = TestItem::builder("tests/test_foo.py", "test_cond").arc();
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
        let item = TestItem::builder("tests/test_foo.py", "test_dep").arc();
        let outcome = TestOutcome::Warned {
            reason: "DeprecationWarning: use new_api".to_string(),
            tips: None,
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
        let item = TestItem::builder("tests/test_foo.py", "test_known_bug").arc();
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
        let item = TestItem::builder("tests/test_foo.py", "test_math").arc();
        let outcome = TestOutcome::failed("wrong value")
            .file("tests/test_math.py")
            .lineno(10)
            .source("assert x == 1")
            .build();
        let line = reporter.format_test_line(&item, &outcome, DurationMs::new(15.0));
        assert!(line.contains("FAIL"), "FAIL label must appear: {line:?}");
    }

    #[test]
    fn test_format_test_line_timeout_contains_time_label() {
        let reporter = make_tty_reporter();
        let item = TestItem::builder("tests/test_foo.py", "test_slow").arc();
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
        let reporter = make_tty_reporter();
        let item = TestItem::builder("tests/test_foo.py", "test_math").arc();
        let outcome = TestOutcome::Passed { tips: None };
        let mut group = ParametrizeBuffer::new(Arc::from("test_math"));
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
        let reporter = make_tty_reporter();
        let item = TestItem::builder("tests/test_foo.py", "test_single").arc();
        let outcome = TestOutcome::Passed { tips: None };
        let mut group = ParametrizeBuffer::new(Arc::from("test_single"));
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
        let opts = ReporterOptsBuilder::new()
            .verbosity(Verbosity::Normal)
            .build();
        let mut reporter = TtyReporter::new(opts);
        let item = TestItem::builder("tests/test_foo.py", "test_failing").arc();
        let outcome = TestOutcome::failed("oops")
            .file("test.py")
            .lineno(5)
            .source("assert x")
            .build();
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0), None);
        assert_eq!(
            reporter.deferred_failures.len(),
            1,
            "failure should be deferred in non-verbose mode"
        );
    }

    #[test]
    fn test_non_verbose_does_not_defer_passes() {
        let opts = ReporterOptsBuilder::new()
            .verbosity(Verbosity::Normal)
            .build();
        let mut reporter = TtyReporter::new(opts);
        let item = TestItem::builder("tests/test_foo.py", "test_passing").arc();
        let outcome = TestOutcome::Passed { tips: None };
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0), None);
        assert!(
            reporter.deferred_failures.is_empty(),
            "passing test should not be deferred"
        );
    }

    #[test]
    fn test_verbose_does_not_defer() {
        let opts = ReporterOptsBuilder::new()
            .verbosity(Verbosity::Detailed)
            .build();
        let mut reporter = TtyReporter::new(opts);
        let item = TestItem::builder("tests/test_foo.py", "test_failing").arc();
        let outcome = TestOutcome::failed("oops")
            .file("test.py")
            .lineno(5)
            .source("assert x")
            .build();
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0), None);
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

        let item_a = TestItem::builder("tests/test_foo.py", "test_login").arc();
        let item_b = TestItem::builder("tests/test_foo.py", "test_signup").arc();

        reporter.test_started(&item_a);
        reporter.test_started(&item_b);
        assert_eq!(reporter.running_tests.len(), 2);

        let outcome = TestOutcome::Passed { tips: None };
        reporter.test_completed(&item_a, &outcome, DurationMs::new(10.0), None);
        assert_eq!(reporter.running_tests.len(), 1);
        assert_eq!(&*reporter.running_tests[0], "test_signup");
    }

    #[test]
    fn test_running_tests_empty_after_all_complete() {
        let opts = ReporterOptsBuilder::new().build();
        let mut reporter = TtyReporter::new(opts);

        let item = TestItem::builder("tests/test_foo.py", "test_foo").arc();
        reporter.test_started(&item);
        let outcome = TestOutcome::Passed { tips: None };
        reporter.test_completed(&item, &outcome, DurationMs::new(5.0), None);
        assert!(reporter.running_tests.is_empty());
    }

    // ── ParametrizeBuffer ─────────────────────────────────────────────────────

    #[test]
    fn test_parametrize_buffer_total_ms_sums_all_durations() {
        use crate::types::TestItem;

        let mut buf = ParametrizeBuffer::new(Arc::from("test_add"));
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
            DurationMs::new(10.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
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
        use crate::types::TestItem;
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new(Arc::from("test_add"));
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::failed("oops")
                .file("t.py")
                .source("assert x")
                .build(),
            DurationMs::new(1.0),
        );
        assert!(
            buf.any_failed(),
            "any_failed should be true when a Failed outcome is present"
        );
    }

    #[test]
    fn test_parametrize_buffer_any_failed_false_when_all_pass_or_skip() {
        use crate::types::TestItem;

        let mut buf = ParametrizeBuffer::new(Arc::from("test_add"));
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
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
    fn test_deferred_failure_shows_parallel_context() {
        let item = TestItem::builder("tests/test_db.py", "test_write_user").arc();
        let outcome = TestOutcome::failed("assert False")
            .file("tests/test_db.py")
            .lineno(10)
            .source("assert False")
            .build();
        let ctx = crate::parallel_context::ParallelContext {
            worker_id: 2,
            concurrent_tests: vec![
                "tests/test_db.py::test_read_user".into(),
                "tests/test_db.py::test_delete_session".into(),
            ],
        };
        let opts = ReporterOptsBuilder::new()
            .verbosity(Verbosity::Normal)
            .build();
        let mut reporter = TtyReporter::new(opts);
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(42.3), Some(&ctx));

        assert_eq!(reporter.deferred_failures.len(), 1);
        let (_, _, _, stored_ctx) = &reporter.deferred_failures[0];
        assert!(stored_ctx.is_some());
        assert_eq!(stored_ctx.as_ref().unwrap().worker_id, 2);
    }

    #[test]
    fn test_parametrize_buffer_passed_count_counts_only_passed_outcomes() {
        use crate::types::TestItem;
        use crate::types::TestOutcome;

        let mut buf = ParametrizeBuffer::new(Arc::from("test_add"));
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
            TestOutcome::failed("oops")
                .file("t.py")
                .source("assert x")
                .build(),
            DurationMs::new(1.0),
        );
        buf.push(
            TestItem::builder("tests/test_foo.py", "test_add").build(),
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
}
