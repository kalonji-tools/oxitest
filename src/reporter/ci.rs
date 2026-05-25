use crate::types::{CollectError, DurationMs, TestItem, TestOutcome};

use super::colors::{color_dim, color_error_token, color_fail};
use super::format::{case_sep, fmt_diagnostic_block};
use super::{sep_width, Reporter, ReporterOpts, StandardReporter};

pub struct CiReporter {
    opts: ReporterOpts,
    deferred_diags: Vec<String>,
    pub(super) dot_buf: String,
}

impl CiReporter {
    pub fn new(opts: ReporterOpts) -> Self {
        super::print_collected(opts.total, opts.async_count);
        Self {
            opts,
            deferred_diags: Vec::new(),
            dot_buf: String::new(),
        }
    }

    fn push_deferred_diag(&mut self, item: &TestItem, outcome: &TestOutcome) {
        let c = self.opts.use_color;

        // --tb=line: compact one-liner per failure
        if self.opts.tb == crate::config::TbStyle::Line {
            let (label, message, lineno) = match outcome {
                TestOutcome::Failed {
                    message, lineno, ..
                } => ("FAILED", message.as_str(), *lineno),
                TestOutcome::Error {
                    message, lineno, ..
                } => ("ERROR", message.as_str(), *lineno),
                TestOutcome::Timeout { message } => ("TIMEOUT", message.as_str(), 0),
                _ => return,
            };
            let line = format!("{:<7} {}   :{}   {}", label, item.node_id, lineno, message);
            self.deferred_diags.push(line);
            return;
        }

        let diag = fmt_diagnostic_block(item, outcome, &self.opts.tb, self.opts.use_color);
        if diag.is_empty() {
            return;
        }
        let header = if let Some(pid) = &item.param_id {
            let sep = case_sep(c);
            match outcome {
                TestOutcome::Failed { .. } => {
                    format!(
                        "{}{}{}",
                        color_fail(pid, c),
                        sep,
                        color_dim(&item.fn_name, c)
                    )
                }
                _ => format!(
                    "{}{}{}",
                    color_error_token(pid, c),
                    sep,
                    color_dim(&item.fn_name, c)
                ),
            }
        } else {
            match outcome {
                TestOutcome::Failed { .. } => color_fail(&format!("FAILED  {}", item.node_id), c),
                _ => color_error_token(&format!("ERROR   {}", item.node_id), c),
            }
        };
        self.deferred_diags.push(format!("{}\n{}", header, diag));
    }
}

impl StandardReporter for CiReporter {
    fn pre_finish(&mut self) {
        if !self.dot_buf.is_empty() {
            println!("{}", self.dot_buf);
        }
        if !self.deferred_diags.is_empty() {
            let hdr = format!(
                "FAILURES {}",
                color_dim(
                    &"═".repeat(sep_width().saturating_sub(8)),
                    self.opts.use_color
                )
            );
            println!("\n{}", hdr);
            for d in &self.deferred_diags {
                println!("{}", d);
            }
        }
        super::print_strict_suite_section(&self.opts);
    }

    fn run_opts(&self) -> &ReporterOpts {
        &self.opts
    }
}

impl Reporter for CiReporter {
    fn test_started(&mut self, _item: &TestItem) {}

    fn test_completed(&mut self, item: &TestItem, outcome: &TestOutcome, _duration_ms: DurationMs) {
        self.dot_buf.push(outcome.dot_char());
        match outcome {
            TestOutcome::Failed { .. }
            | TestOutcome::Error { .. }
            | TestOutcome::Timeout { .. } => self.push_deferred_diag(item, outcome),
            TestOutcome::Flaky { .. } => {
                let node_str = item.node_id.as_ref();
                self.deferred_diags.retain(|d| !d.contains(node_str));
            }
            _ => {}
        }
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
    use super::*;
    use crate::config::TbStyle;
    use crate::reporter::test_helpers::{make_error, make_failed, make_item};
    use crate::types::TestOutcome;

    fn make_ci_reporter(tb: TbStyle) -> CiReporter {
        CiReporter::new(
            crate::reporter::ReporterOptsBuilder::new()
                .total(3)
                .tb(tb)
                .build(),
        )
    }

    #[test]
    fn test_ci_reporter_all_pass_dots() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(0.1));
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(0.1));
        assert_eq!(reporter.dot_buf, "..");
    }

    #[test]
    fn test_ci_reporter_bare_assert_shows_middot() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![5],
        };
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(0.1));
        assert_eq!(reporter.dot_buf, "\u{00B7}");
    }

    #[test]
    fn test_ci_reporter_fail_shows_f() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        let outcome = make_failed("oops", "t.py", 1, "assert x");
        reporter.test_started(&item);
        reporter.test_completed(&item, &outcome, DurationMs::new(1.0));
        assert_eq!(reporter.dot_buf, "F");
    }

    #[test]
    fn test_ci_reporter_skip_shows_s() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        reporter.test_completed(
            &item,
            &TestOutcome::Skipped {
                reason: "reason".to_string(),
            },
            DurationMs::ZERO,
        );
        assert_eq!(reporter.dot_buf, "s");
    }

    #[test]
    fn test_ci_reporter_error_shows_e() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        let outcome = make_error("AttributeError: x", "t.py", 5, "obj.x");
        reporter.test_completed(&item, &outcome, DurationMs::ZERO);
        assert_eq!(reporter.dot_buf, "E");
    }

    #[test]
    fn test_exit_code_all_passed() {
        let mut reporter = CiReporter::new(
            crate::reporter::ReporterOptsBuilder::new()
                .total(1)
                .tb(TbStyle::No)
                .build(),
        );
        let item = make_item("test_a");
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        let mut stats = crate::reporter::RunStats::new();
        stats.record(&item, &outcome);
        reporter.test_completed(&item, &outcome, DurationMs::ZERO);
        assert_eq!(reporter.finish(&[], false, &stats).code(), 0);
    }

    #[test]
    fn test_exit_code_with_failure() {
        let mut reporter = CiReporter::new(
            crate::reporter::ReporterOptsBuilder::new()
                .total(1)
                .tb(TbStyle::No)
                .build(),
        );
        let item = make_item("test_a");
        let outcome = make_failed("x", "f.py", 1, "assert");
        let mut stats = crate::reporter::RunStats::new();
        stats.record(&item, &outcome);
        reporter.test_completed(&item, &outcome, DurationMs::ZERO);
        assert_eq!(reporter.finish(&[], false, &stats).code(), 1);
    }

    #[test]
    fn test_exit_code_interrupted() {
        let mut reporter = CiReporter::new(
            crate::reporter::ReporterOptsBuilder::new()
                .total(2)
                .tb(TbStyle::No)
                .build(),
        );
        let item = make_item("test_a");
        let outcome = make_failed("x", "f.py", 1, "assert");
        let mut stats = crate::reporter::RunStats::new();
        stats.record(&item, &outcome);
        reporter.test_completed(&item, &outcome, DurationMs::ZERO);
        assert_eq!(reporter.finish(&[], true, &stats).code(), 2);
    }

    #[test]
    fn test_ci_reporter_xfail_shows_x() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        reporter.test_completed(
            &item,
            &TestOutcome::XFailed {
                reason: "known bug".to_string(),
            },
            DurationMs::ZERO,
        );
        assert_eq!(reporter.dot_buf, "x");
    }

    #[test]
    fn test_ci_reporter_xpassed_shows_capital_x() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        reporter.test_completed(
            &item,
            &TestOutcome::XPassed { strict: true },
            DurationMs::ZERO,
        );
        assert_eq!(reporter.dot_buf, "X");
    }

    #[test]
    fn test_ci_reporter_xpassed_lenient_shows_capital_x() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = make_item("test_a");
        reporter.test_completed(
            &item,
            &TestOutcome::XPassed { strict: false },
            DurationMs::ZERO,
        );
        assert_eq!(reporter.dot_buf, "X");
    }

    #[test]
    fn test_exit_code_collect_error() {
        use crate::types::CollectError;
        let mut reporter = CiReporter::new(
            crate::reporter::ReporterOptsBuilder::new()
                .tb(TbStyle::No)
                .build(),
        );
        let errors = vec![CollectError::PyError("import failed".to_string())];
        assert_eq!(
            reporter
                .finish(&errors, false, &crate::reporter::RunStats::new())
                .code(),
            3
        );
    }

    #[test]
    fn test_ci_reporter_parametrize_fail_uses_case_id_format() {
        let mut reporter = make_ci_reporter(TbStyle::Short);
        let item = TestItem {
            node_id: crate::types::NodeId::new("tests/test_foo.py", "test_add", Some("neg")),
            module_path: camino::Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_add".to_string(),
            lineno: 5,
            markers: vec![],
            param_id: Some("neg".to_string()),
            param_values: vec![("x".to_string(), "-1".to_string())],
            is_async: false,
        };
        let outcome = make_failed("", "tests/test_foo.py", 5, "assert x > 0");
        reporter.test_completed(&item, &outcome, DurationMs::new(1.0));
        assert_eq!(reporter.dot_buf, "F");
        assert_eq!(reporter.deferred_diags.len(), 1);
        assert!(
            reporter.deferred_diags[0].contains("neg"),
            "deferred diag must contain case id"
        );
        assert!(
            reporter.deferred_diags[0].contains("test_add"),
            "deferred diag must contain fn name"
        );
    }

    #[test]
    fn test_line_mode_emits_one_liner() {
        let mut reporter = make_ci_reporter(TbStyle::Line);
        let item = make_item("test_bar");
        let outcome = make_failed("values differ", "test_foo.py", 5, "assert x == y");
        reporter.test_completed(&item, &outcome, DurationMs::new(10.0));
        assert_eq!(reporter.deferred_diags.len(), 1);
        let line = &reporter.deferred_diags[0];
        assert!(line.contains("FAILED"), "must contain FAILED label");
        assert!(
            line.contains("test_foo.py::test_bar"),
            "must contain node_id"
        );
        assert!(line.contains(":5"), "must contain lineno");
        assert!(line.contains("values differ"), "must contain message");
    }

    #[test]
    fn test_line_mode_error_emits_one_liner() {
        let mut reporter = make_ci_reporter(TbStyle::Line);
        let item = make_item("test_deep");
        let outcome = make_error("ValueError: something broke", "test_foo.py", 10, "obj.x");
        reporter.test_completed(&item, &outcome, DurationMs::new(5.0));
        assert_eq!(reporter.deferred_diags.len(), 1);
        let line = &reporter.deferred_diags[0];
        assert!(line.contains("ERROR"), "must contain ERROR label");
        assert!(line.contains(":10"), "must contain lineno");
        assert!(
            line.contains("ValueError: something broke"),
            "must contain message"
        );
    }
}
