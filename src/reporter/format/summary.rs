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

pub(crate) fn fmt_diagnostics_block(
    entries: &[crate::reporter::stats::DiagnosticEntry],
    show_diagnostics: bool,
    use_color: bool,
) -> String {
    use crate::reporter::stats::DiagnosticSeverity;

    if entries.is_empty() {
        return String::new();
    }

    // Deduplicate by (severity, context, message), counting occurrences.
    // Use Vec to preserve insertion order (first-seen order).
    let mut dedup_keys: Vec<(DiagnosticSeverity, String, String)> = Vec::new();
    let mut dedup_counts: Vec<usize> = Vec::new();
    for e in entries {
        let key = (e.severity.clone(), e.context.to_string(), e.message.clone());
        if let Some(pos) = dedup_keys.iter().position(|k| *k == key) {
            dedup_counts[pos] += 1;
        } else {
            dedup_keys.push(key);
            dedup_counts.push(1);
        }
    }

    // Sort by severity: Error (0) first, then Warning (1), then Notice (2).
    let mut indices: Vec<usize> = (0..dedup_keys.len()).collect();
    indices.sort_by_key(|&i| match dedup_keys[i].0 {
        DiagnosticSeverity::Error => 0,
        DiagnosticSeverity::Warning => 1,
        DiagnosticSeverity::Notice => 2,
    });

    if !show_diagnostics {
        // Collapsed: count per severity
        let mut error_count = 0usize;
        let mut warn_count = 0usize;
        let mut notice_count = 0usize;
        for (i, count) in dedup_counts.iter().enumerate() {
            match dedup_keys[i].0 {
                DiagnosticSeverity::Error => error_count += count,
                DiagnosticSeverity::Warning => warn_count += count,
                DiagnosticSeverity::Notice => notice_count += count,
            }
        }
        let mut parts = Vec::new();
        if error_count > 0 {
            parts.push(color_fail(
                &format!("{} error{}", error_count, plural(error_count)),
                use_color,
            ));
        }
        if warn_count > 0 {
            parts.push(color_warn(
                &format!("{} warning{}", warn_count, plural(warn_count)),
                use_color,
            ));
        }
        if notice_count > 0 {
            parts.push(color_dim(
                &format!("{} notice{}", notice_count, plural(notice_count)),
                use_color,
            ));
        }
        let summary = parts.join(" · ");
        return format!(
            "  {}  {}\n",
            summary,
            color_dim("(--warnings to expand)", use_color)
        );
    }

    // Expanded: one box per unique diagnostic, sorted by severity
    let mut block = String::new();
    for &idx in &indices {
        let (ref sev, ref context, ref message) = dedup_keys[idx];
        let count = dedup_counts[idx];

        let line_color: fn(&str, bool) -> String = match sev {
            DiagnosticSeverity::Error => color_fail,
            DiagnosticSeverity::Warning => color_warn,
            DiagnosticSeverity::Notice => color_dim,
        };

        let count_suffix = if count > 1 {
            format!(" (×{})", count)
        } else {
            String::new()
        };

        block.push_str(&format!(
            "        {} {}{}\n",
            color_dim("┌─", use_color),
            color_dim(context, use_color),
            count_suffix,
        ));
        for line in message.split('\n') {
            if !line.is_empty() {
                block.push_str(&format!(
                    "        {}  {}\n",
                    color_dim("│", use_color),
                    line_color(line, use_color),
                ));
            }
        }
        block.push_str(&format!("        {}\n", color_dim("└─", use_color)));
    }
    block
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
    fn test_diagnostics_block_empty_returns_empty() {
        assert!(fmt_diagnostics_block(&[], false, false).is_empty());
        assert!(fmt_diagnostics_block(&[], true, false).is_empty());
    }

    #[test]
    fn test_diagnostics_block_collapsed_shows_counts() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        let warnings = vec![
            DiagnosticEntry {
                severity: DiagnosticSeverity::Warning,
                context: Arc::from("tests/test_foo.py::test_a"),
                message: "DeprecationWarning".to_string(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Warning,
                context: Arc::from("tests/test_foo.py::test_b"),
                message: "DeprecationWarning".to_string(),
                file: None,
                lineno: None,
            },
        ];
        let s = fmt_diagnostics_block(&warnings, false, false);
        assert!(s.contains("2 warnings"));
        assert!(s.contains("--warnings to expand"));
        assert!(!s.contains("test_a"));
    }

    #[test]
    fn test_diagnostics_block_collapsed_singular() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        let warnings = vec![DiagnosticEntry {
            severity: DiagnosticSeverity::Warning,
            context: Arc::from("tests/test_foo.py::test_a"),
            message: "DeprecationWarning".to_string(),
            file: None,
            lineno: None,
        }];
        let s = fmt_diagnostics_block(&warnings, false, false);
        assert!(s.contains("1 warning"));
        assert!(!s.contains("1 warnings"));
    }

    #[test]
    fn test_diagnostics_block_expanded_shows_context_and_message() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        let warnings = vec![DiagnosticEntry {
            severity: DiagnosticSeverity::Warning,
            context: Arc::from("tests/test_foo.py::test_a"),
            message: "use new_api() instead".to_string(),
            file: None,
            lineno: None,
        }];
        let s = fmt_diagnostics_block(&warnings, true, false);
        assert!(s.contains("tests/test_foo.py::test_a"));
        assert!(s.contains("use new_api() instead"));
        assert!(!s.contains("--warnings to expand"));
    }

    #[test]
    fn test_diagnostics_block_deduplicates_identical_entries() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        let entries = vec![
            DiagnosticEntry {
                severity: DiagnosticSeverity::Warning,
                context: Arc::from("teardown"),
                message: "RuntimeError: boom".to_string(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Warning,
                context: Arc::from("teardown"),
                message: "RuntimeError: boom".to_string(),
                file: None,
                lineno: None,
            },
        ];
        let s = fmt_diagnostics_block(&entries, true, false);
        // Should show ×2 count, not two separate boxes
        assert!(s.contains("×2"), "duplicate entries should show ×2 count");
        // Should only have one ┌─ (one box, not two)
        assert_eq!(
            s.matches("┌─").count(),
            1,
            "should deduplicate into one box"
        );
    }

    #[test]
    fn test_diagnostics_block_sorts_by_severity() {
        use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};
        let entries = vec![
            DiagnosticEntry {
                severity: DiagnosticSeverity::Notice,
                context: Arc::from("notice-ctx"),
                message: "info message".to_string(),
                file: None,
                lineno: None,
            },
            DiagnosticEntry {
                severity: DiagnosticSeverity::Error,
                context: Arc::from("error-ctx"),
                message: "bad thing".to_string(),
                file: None,
                lineno: None,
            },
        ];
        let s = fmt_diagnostics_block(&entries, true, false);
        let error_pos = s.find("error-ctx").expect("should contain error-ctx");
        let notice_pos = s.find("notice-ctx").expect("should contain notice-ctx");
        assert!(
            error_pos < notice_pos,
            "errors should appear before notices"
        );
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
