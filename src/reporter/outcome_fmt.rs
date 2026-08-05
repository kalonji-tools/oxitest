//! Rendering helpers for [`TestOutcome`] — dot characters, labels, color/JUnit
//! classification, and CTRF status mapping.
//!
//! These are inherent methods on `TestOutcome` defined in a separate file to keep
//! `types.rs` focused on data definitions.  Rust allows inherent impls in any
//! module within the same crate.

use crate::types::TestOutcome;

/// Classification of a [`TestOutcome`] for color/style selection in TTY output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ColorCategory {
    Pass,
    Fail,
    Error,
    Skip,
    Warn,
    Dim,
    Timeout,
}

/// Classification of a [`TestOutcome`] for JUnit XML element selection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum JunitCategory {
    Passed,
    Failed,
    Error,
    Skipped,
}

impl TestOutcome {
    /// Single character for CI dot-progress output.
    pub(crate) const fn dot_char(&self) -> char {
        match self {
            Self::Passed { tips: None } => '.',
            Self::Passed { .. } => '\u{00B7}', // middot — bare assert passed
            Self::Failed(..) => 'F',
            Self::Error(..) => 'E',
            Self::Skipped { .. } => 's',
            Self::Warned { .. } => '.', // same as clean pass — warning shown in summary
            Self::XFailed { .. } => 'x',
            Self::XPassed { .. } => 'X',
            Self::Timeout { .. } => 'T',
            Self::Flaky { .. } => 'f',
        }
    }

    /// Short display label for TTY output. Empty string for passing tests.
    pub(crate) const fn label(&self) -> &'static str {
        match self {
            Self::Failed(..) => "FAIL ",
            Self::Error(..) => "ERROR",
            Self::Skipped { .. } => "SKIP ",
            Self::Warned { .. } => "WARN ",
            Self::XFailed { .. } => "XFAIL",
            Self::XPassed { .. } => "XPASS",
            Self::Timeout { .. } => "TIME ",
            Self::Flaky { .. } => "FLAKY",
            Self::Passed { .. } => "",
        }
    }

    /// Classifies the outcome for color/style selection in TTY output.
    pub(crate) const fn color_category(&self) -> ColorCategory {
        match self {
            Self::Passed { .. } => ColorCategory::Pass,
            Self::Failed(..) | Self::XPassed { strict: true } => ColorCategory::Fail,
            Self::Error(..) => ColorCategory::Error,
            Self::Skipped { .. } => ColorCategory::Skip,
            Self::Warned { .. } | Self::XPassed { strict: false } | Self::Flaky { .. } => {
                ColorCategory::Warn
            }
            Self::XFailed { .. } => ColorCategory::Dim,
            Self::Timeout { .. } => ColorCategory::Timeout,
        }
    }

    /// Classifies the outcome for JUnit XML element selection.
    pub(crate) const fn junit_category(&self) -> JunitCategory {
        match self {
            Self::Passed { .. }
            | Self::Warned { .. }
            | Self::XPassed { strict: false }
            | Self::Flaky { .. } => JunitCategory::Passed,
            Self::Failed(..) | Self::XPassed { strict: true } => JunitCategory::Failed,
            Self::Error(..) | Self::Timeout { .. } => JunitCategory::Error,
            Self::Skipped { .. } | Self::XFailed { .. } => JunitCategory::Skipped,
        }
    }

    /// Maps the outcome to a CTRF status string (`"passed"`, `"failed"`, or `"skipped"`).
    ///
    /// CTRF defines only three statuses. This method centralises the mapping so
    /// reporter code does not duplicate match arms.
    pub(crate) const fn ctrf_status(&self) -> &'static str {
        match self {
            Self::Passed { .. }
            | Self::Warned { .. }
            | Self::XPassed { strict: false }
            | Self::Flaky { .. } => "passed",
            Self::Failed(..)
            | Self::Error(..)
            | Self::XPassed { strict: true }
            | Self::Timeout { .. } => "failed",
            Self::Skipped { .. } | Self::XFailed { .. } => "skipped",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{FailureDiagnostic, OutcomeKind, TestOutcome};

    // ── dot_char ─────────────────────────────────────────────────────────────

    #[test]
    fn dot_char_all_variants() {
        let cases: Vec<(TestOutcome, char)> = vec![
            (TestOutcome::Passed { tips: None }, '.'),
            (
                TestOutcome::Passed {
                    tips: Some(vec![5].into_boxed_slice()),
                },
                '\u{00B7}',
            ),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
                'F',
            ),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                'E',
            ),
            (
                TestOutcome::Skipped {
                    reason: String::new(),
                },
                's',
            ),
            (
                TestOutcome::Warned {
                    reason: String::new(),
                    tips: None,
                },
                '.',
            ),
            (
                TestOutcome::XFailed {
                    reason: String::new(),
                },
                'x',
            ),
            (TestOutcome::XPassed { strict: true }, 'X'),
            (TestOutcome::XPassed { strict: false }, 'X'),
            (
                TestOutcome::Timeout {
                    message: String::new(),
                },
                'T',
            ),
            (
                TestOutcome::Flaky {
                    message: String::new(),
                    original: OutcomeKind::Failed,
                },
                'f',
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(
                outcome.dot_char(),
                expected,
                "dot_char mismatch for {outcome:?}"
            );
        }
    }

    // ── label ────────────────────────────────────────────────────────────────

    #[test]
    fn label_all_variants() {
        let cases: Vec<(TestOutcome, &str)> = vec![
            (TestOutcome::Passed { tips: None }, ""),
            (
                TestOutcome::Passed {
                    tips: Some(vec![5].into_boxed_slice()),
                },
                "",
            ),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
                "FAIL ",
            ),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                "ERROR",
            ),
            (
                TestOutcome::Skipped {
                    reason: String::new(),
                },
                "SKIP ",
            ),
            (
                TestOutcome::Warned {
                    reason: String::new(),
                    tips: None,
                },
                "WARN ",
            ),
            (
                TestOutcome::XFailed {
                    reason: String::new(),
                },
                "XFAIL",
            ),
            // Both strict variants return same text; color differs in tty.rs
            (TestOutcome::XPassed { strict: true }, "XPASS"),
            (TestOutcome::XPassed { strict: false }, "XPASS"),
            (
                TestOutcome::Timeout {
                    message: String::new(),
                },
                "TIME ",
            ),
            (
                TestOutcome::Flaky {
                    message: String::new(),
                    original: OutcomeKind::Failed,
                },
                "FLAKY",
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(outcome.label(), expected, "label mismatch for {outcome:?}");
        }
    }

    // ── color_category ───────────────────────────────────────────────────────

    #[test]
    fn color_category_all_variants() {
        let cases: Vec<(TestOutcome, ColorCategory)> = vec![
            (TestOutcome::Passed { tips: None }, ColorCategory::Pass),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
                ColorCategory::Fail,
            ),
            (TestOutcome::XPassed { strict: true }, ColorCategory::Fail),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                ColorCategory::Error,
            ),
            (
                TestOutcome::Skipped {
                    reason: String::new(),
                },
                ColorCategory::Skip,
            ),
            (
                TestOutcome::Warned {
                    reason: String::new(),
                    tips: None,
                },
                ColorCategory::Warn,
            ),
            (TestOutcome::XPassed { strict: false }, ColorCategory::Warn),
            (
                TestOutcome::Flaky {
                    message: String::new(),
                    original: OutcomeKind::Failed,
                },
                ColorCategory::Warn,
            ),
            (
                TestOutcome::XFailed {
                    reason: String::new(),
                },
                ColorCategory::Dim,
            ),
            (
                TestOutcome::Timeout {
                    message: String::new(),
                },
                ColorCategory::Timeout,
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(
                outcome.color_category(),
                expected,
                "color_category mismatch for {outcome:?}"
            );
        }
    }

    // ── junit_category ───────────────────────────────────────────────────────

    #[test]
    fn junit_category_all_variants() {
        let cases: Vec<(TestOutcome, JunitCategory)> = vec![
            (TestOutcome::Passed { tips: None }, JunitCategory::Passed),
            (
                TestOutcome::Warned {
                    reason: String::new(),
                    tips: None,
                },
                JunitCategory::Passed,
            ),
            (
                TestOutcome::XPassed { strict: false },
                JunitCategory::Passed,
            ),
            (
                TestOutcome::Flaky {
                    message: String::new(),
                    original: OutcomeKind::Failed,
                },
                JunitCategory::Passed,
            ),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
                JunitCategory::Failed,
            ),
            (TestOutcome::XPassed { strict: true }, JunitCategory::Failed),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                JunitCategory::Error,
            ),
            (
                TestOutcome::Timeout {
                    message: String::new(),
                },
                JunitCategory::Error,
            ),
            (
                TestOutcome::Skipped {
                    reason: String::new(),
                },
                JunitCategory::Skipped,
            ),
            (
                TestOutcome::XFailed {
                    reason: String::new(),
                },
                JunitCategory::Skipped,
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(
                outcome.junit_category(),
                expected,
                "junit_category mismatch for {outcome:?}"
            );
        }
    }
}
