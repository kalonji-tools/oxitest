use crate::reporter::stats::RunStats;
use crate::types::OutcomeKind;

use crate::colors::{
    color_cyan, color_dim, color_error_token, color_fail, color_pass, color_skip, color_timeout,
    color_warn,
};

use super::diagnostic::sep_width;

pub(crate) fn plural(n: usize) -> &'static str {
    if n == 1 { "" } else { "s" }
}

/// Push a colored stat entry into `parts` when `count > 0`.
fn push_stat(
    parts: &mut Vec<String>,
    count: usize,
    label: &str,
    color_fn: fn(&str, bool) -> String,
    use_color: bool,
) {
    if count > 0 {
        parts.push(color_fn(&format!("{count} {label}"), use_color));
    }
}

/// Like [`push_stat`] but appends a plural suffix to the label.
fn push_stat_plural(
    parts: &mut Vec<String>,
    count: usize,
    label: &str,
    color_fn: fn(&str, bool) -> String,
    use_color: bool,
) {
    if count > 0 {
        parts.push(color_fn(
            &format!("{count} {label}{}", plural(count)),
            use_color,
        ));
    }
}

pub(crate) fn fmt_summary(stats: &RunStats, collect_err_count: usize, use_color: bool) -> String {
    let sep = color_dim(&"═".repeat(sep_width()), use_color);
    let mut parts: Vec<String> = Vec::new();
    push_stat(
        &mut parts,
        stats.counts.get(OutcomeKind::Failed),
        "failed",
        color_fail,
        use_color,
    );
    push_stat_plural(
        &mut parts,
        stats.counts.get(OutcomeKind::Error),
        "error",
        color_error_token,
        use_color,
    );
    let xpassed = stats.counts.get(OutcomeKind::XPassed);
    if xpassed > 0 {
        let xpassed_str = format!("{xpassed} xpassed");
        if stats.strict.xpassed_strict > 0 {
            parts.push(color_fail(&xpassed_str, use_color));
        } else {
            parts.push(color_warn(&xpassed_str, use_color));
        }
    }
    push_stat_plural(
        &mut parts,
        stats.counts.get(OutcomeKind::Timeout),
        "timeout",
        color_timeout,
        use_color,
    );
    push_stat(
        &mut parts,
        stats.counts.get(OutcomeKind::Passed),
        "passed",
        color_pass,
        use_color,
    );
    push_stat(
        &mut parts,
        stats.counts.get(OutcomeKind::Skipped),
        "skipped",
        color_skip,
        use_color,
    );
    push_stat(
        &mut parts,
        stats.counts.get(OutcomeKind::XFailed),
        "xfailed",
        color_dim,
        use_color,
    );
    push_stat_plural(
        &mut parts,
        stats.counts.get(OutcomeKind::Warned),
        "warning",
        color_warn,
        use_color,
    );
    push_stat(
        &mut parts,
        stats.counts.get(OutcomeKind::Flaky),
        "flaky",
        color_warn,
        use_color,
    );
    if collect_err_count > 0 {
        parts.push(format!(
            "{} collection error{}",
            collect_err_count,
            plural(collect_err_count)
        ));
    }
    if parts.is_empty() {
        parts.push("no tests ran".to_string());
    }
    let middle = parts.join(" · ");
    format!("{}\n  {}\n{}", sep, middle, sep)
}

pub(crate) fn fmt_tip_block(
    tip_lines: &[crate::reporter::stats::TipLine],
    show_tips: bool,
    use_color: bool,
) -> String {
    if tip_lines.is_empty() {
        return String::new();
    }
    let label = color_cyan("tip", use_color);
    if show_tips {
        let locations: Vec<String> = tip_lines
            .iter()
            .map(|t| color_dim(&format!("        {}:{}", t.file, t.lineno), use_color))
            .collect();
        format!(
            "  {}   {} assertions without messages:\n{}\n",
            label,
            tip_lines.len(),
            locations.join("\n")
        )
    } else {
        format!(
            "  {}   {} assertions without messages  {}\n",
            label,
            tip_lines.len(),
            color_dim("(--tips to expand)", use_color)
        )
    }
}

pub(crate) fn fmt_warning_block(
    warning_msgs: &[crate::reporter::stats::WarningEntry],
    show_warnings: bool,
    use_color: bool,
) -> String {
    if warning_msgs.is_empty() {
        return String::new();
    }
    let label = color_warn("warn", use_color);
    if show_warnings {
        let mut block = format!("  {}  {} warnings:\n", label, warning_msgs.len());
        for w in warning_msgs {
            block.push_str(&format!(
                "        {} {}\n",
                color_dim("┌─", use_color),
                color_dim(&w.context, use_color),
            ));
            for line in w.message.split('\n') {
                if !line.is_empty() {
                    block.push_str(&format!(
                        "        {}  {}\n",
                        color_dim("│", use_color),
                        color_warn(line, use_color),
                    ));
                }
            }
            block.push_str(&format!("        {}\n", color_dim("└─", use_color)));
        }
        block
    } else {
        let count = warning_msgs.len();
        format!(
            "  {}  {} warning{}  {}\n",
            label,
            count,
            plural(count),
            color_dim("(--warnings to expand)", use_color)
        )
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;
    use crate::reporter::stats::RunStats;

    #[allow(clippy::too_many_arguments)]
    fn make_stats(
        passed: usize,
        failed: usize,
        errored: usize,
        skipped: usize,
        warned: usize,
        xfailed: usize,
        xpassed: usize,
        xpassed_strict: usize,
    ) -> RunStats {
        use crate::reporter::stats::OutcomeCounters;
        use crate::types::OutcomeKind;
        let mut counts = OutcomeCounters::default();
        counts.by_kind[OutcomeKind::Passed as usize] = passed;
        counts.by_kind[OutcomeKind::Failed as usize] = failed;
        counts.by_kind[OutcomeKind::Error as usize] = errored;
        counts.by_kind[OutcomeKind::Skipped as usize] = skipped;
        counts.by_kind[OutcomeKind::Warned as usize] = warned;
        counts.by_kind[OutcomeKind::XFailed as usize] = xfailed;
        counts.by_kind[OutcomeKind::XPassed as usize] = xpassed;
        let mut strict = crate::reporter::stats::StrictCounts::default();
        strict.xpassed_strict = xpassed_strict;
        RunStats {
            counts,
            strict,
            ..RunStats::new()
        }
    }

    #[test]
    fn test_summary_all_passed() {
        let s = fmt_summary(&make_stats(5, 0, 0, 0, 0, 0, 0, 0), 0, false);
        assert!(s.contains("5 passed"));
        assert!(!s.contains("failed"));
    }

    #[test]
    fn test_summary_mixed_joined_with_middot() {
        let s = fmt_summary(&make_stats(3, 2, 0, 1, 0, 0, 0, 0), 0, false);
        assert!(s.contains("2 failed"));
        assert!(s.contains("3 passed"));
        assert!(s.contains("1 skipped"));
        assert!(s.contains(" · "));
    }

    #[test]
    fn test_summary_error_plural() {
        let s = fmt_summary(&make_stats(0, 0, 2, 0, 0, 0, 0, 0), 0, false);
        assert!(s.contains("2 errors"));
    }

    #[test]
    fn test_summary_error_singular() {
        let s = fmt_summary(&make_stats(0, 0, 1, 0, 0, 0, 0, 0), 0, false);
        assert!(s.contains("1 error"));
        assert!(!s.contains("1 errors"));
    }

    #[test]
    fn test_summary_warning_plural() {
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 2, 0, 0, 0), 0, false);
        assert!(s.contains("2 warnings"));
    }

    #[test]
    fn test_summary_warning_singular() {
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 1, 0, 0, 0), 0, false);
        assert!(s.contains("1 warning"));
        assert!(!s.contains("1 warnings"));
    }

    #[test]
    fn test_summary_no_tests_ran() {
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 0, 0, 0, 0), 0, false);
        assert!(s.contains("no tests ran"));
    }

    #[test]
    fn test_summary_xfailed() {
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 0, 2, 0, 0), 0, false);
        assert!(s.contains("2 xfailed"));
    }

    #[test]
    fn test_summary_xpassed() {
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 0, 0, 1, 0), 0, false);
        assert!(s.contains("1 xpassed"));
    }

    #[test]
    fn test_summary_xpassed_lenient_is_yellow() {
        // All xpassed are lenient (xpassed_strict=0) — should contain "xpassed" but colored yellow
        let s = fmt_summary(&make_stats(0, 0, 0, 0, 0, 0, 1, 0), 0, false);
        assert!(s.contains("1 xpassed"));
    }

    #[test]
    fn test_tip_block_empty_returns_empty() {
        assert!(fmt_tip_block(&[], false, false).is_empty());
        assert!(fmt_tip_block(&[], true, false).is_empty());
    }

    #[test]
    fn test_tip_block_collapsed_shows_count_and_hint() {
        use crate::reporter::stats::TipLine;
        let tips = vec![TipLine {
            file: camino::Utf8PathBuf::from("tests/test_foo.py"),
            lineno: crate::types::LineNo::new(12),
        }];
        let s = fmt_tip_block(&tips, false, false);
        assert!(s.contains("1 assertions without messages"));
        assert!(s.contains("--tips to expand"));
        assert!(!s.contains("tests/test_foo.py"));
    }

    #[test]
    fn test_tip_block_expanded_shows_locations() {
        use crate::reporter::stats::TipLine;
        let tips = vec![
            TipLine {
                file: camino::Utf8PathBuf::from("tests/test_foo.py"),
                lineno: crate::types::LineNo::new(12),
            },
            TipLine {
                file: camino::Utf8PathBuf::from("tests/test_bar.py"),
                lineno: crate::types::LineNo::new(7),
            },
        ];
        let s = fmt_tip_block(&tips, true, false);
        assert!(s.contains("2 assertions without messages"));
        assert!(s.contains("tests/test_foo.py:12"));
        assert!(s.contains("tests/test_bar.py:7"));
        assert!(!s.contains("--tips to expand"));
    }

    #[test]
    fn test_warning_block_empty_returns_empty() {
        assert!(fmt_warning_block(&[], false, false).is_empty());
        assert!(fmt_warning_block(&[], true, false).is_empty());
    }

    #[test]
    fn test_warning_block_collapsed_plural() {
        use crate::reporter::stats::WarningEntry;
        let warnings = vec![
            WarningEntry {
                context: Arc::from("tests/test_foo.py::test_a"),
                message: "DeprecationWarning".to_string(),
            },
            WarningEntry {
                context: Arc::from("tests/test_foo.py::test_b"),
                message: "DeprecationWarning".to_string(),
            },
        ];
        let s = fmt_warning_block(&warnings, false, false);
        assert!(s.contains("2 warnings"));
        assert!(s.contains("--warnings to expand"));
        assert!(!s.contains("test_a"));
    }

    #[test]
    fn test_warning_block_collapsed_singular() {
        use crate::reporter::stats::WarningEntry;
        let warnings = vec![WarningEntry {
            context: Arc::from("tests/test_foo.py::test_a"),
            message: "DeprecationWarning".to_string(),
        }];
        let s = fmt_warning_block(&warnings, false, false);
        assert!(s.contains("1 warning"));
        assert!(!s.contains("1 warnings"));
    }

    #[test]
    fn test_warning_block_expanded_shows_node_and_reason() {
        use crate::reporter::stats::WarningEntry;
        let warnings = vec![WarningEntry {
            context: Arc::from("tests/test_foo.py::test_a"),
            message: "use new_api() instead".to_string(),
        }];
        let s = fmt_warning_block(&warnings, true, false);
        assert!(s.contains("tests/test_foo.py::test_a"));
        assert!(s.contains("use new_api() instead"));
        assert!(!s.contains("--warnings to expand"));
    }

    #[test]
    fn test_summary_flaky_appears_when_nonzero() {
        let mut counts = crate::reporter::stats::OutcomeCounters::default();
        counts.by_kind[crate::types::OutcomeKind::Flaky as usize] = 2;
        let s = fmt_summary(
            &RunStats {
                counts,
                ..RunStats::new()
            },
            0,
            false,
        );
        assert!(s.contains("2 flaky"));
    }

    #[test]
    fn test_summary_flaky_absent_when_zero() {
        let s = fmt_summary(&RunStats::new(), 0, false);
        assert!(!s.contains("flaky"));
    }

    #[test]
    fn test_summary_timeout_appears_when_nonzero() {
        let mut counts = crate::reporter::stats::OutcomeCounters::default();
        counts.by_kind[crate::types::OutcomeKind::Timeout as usize] = 2;
        let s = fmt_summary(
            &RunStats {
                counts,
                ..RunStats::new()
            },
            0,
            false,
        );
        assert!(s.contains("2 timeout"));
    }

    #[test]
    fn test_summary_timeout_absent_when_zero() {
        let s = fmt_summary(&RunStats::new(), 0, false);
        assert!(!s.contains("timeout"));
    }

    #[test]
    fn test_plural_singular() {
        assert_eq!(plural(1), "");
    }

    #[test]
    fn test_plural_zero() {
        assert_eq!(plural(0), "s");
    }

    #[test]
    fn test_plural_many() {
        assert_eq!(plural(5), "s");
    }

    mod snapshot_tests {
        use super::*;
        use insta::assert_snapshot;

        #[test]
        fn summary_all_passed() {
            let stats = make_stats(5, 0, 0, 0, 0, 0, 0, 0);
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }

        #[test]
        fn summary_mixed_outcomes() {
            let stats = make_stats(10, 2, 1, 3, 0, 0, 0, 0);
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }

        #[test]
        fn summary_all_failed() {
            let stats = make_stats(0, 8, 0, 0, 0, 0, 0, 0);
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }

        #[test]
        fn summary_with_warnings_and_flaky() {
            let mut counts = crate::reporter::stats::OutcomeCounters::default();
            counts.by_kind[crate::types::OutcomeKind::Passed as usize] = 15;
            counts.by_kind[crate::types::OutcomeKind::Warned as usize] = 2;
            counts.by_kind[crate::types::OutcomeKind::Flaky as usize] = 1;
            let stats = RunStats {
                counts,
                ..RunStats::new()
            };
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }

        #[test]
        fn summary_no_tests_ran() {
            let stats = RunStats::new();
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }

        #[test]
        fn summary_xfail_and_xpass() {
            let stats = make_stats(3, 0, 0, 0, 0, 2, 1, 0);
            assert_snapshot!(fmt_summary(&stats, 0, false));
        }
    }
}
