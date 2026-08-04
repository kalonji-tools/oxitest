//! Exit codes, timing records, and failure tracking.

use super::{DurationMs, NodeId, OutcomeKind, TestOutcome};

/// Process exit code with named variants for each documented exit status.
///
/// - `Success` (0) — all tests passed (or were skipped / xfailed).
/// - `Failure` (1) — at least one hard failure.
/// - `Interrupted` (2) — the run was interrupted (e.g. Ctrl-C).
/// - `CollectError` (3) — one or more collection errors.
/// - `UsageError` (4) — invalid CLI arguments or conflicting flags.
///
/// **Severity is defined by [`as_i32`](ExitCode::as_i32), and the variant
/// declaration order below means nothing.** `PartialOrd`/`Ord` are deliberately
/// not derived: a derived `Ord` on a fieldless enum compares by declaration
/// *position*, so reordering these five lines would silently invert exit-code
/// precedence for the two call sites that take a maximum — `CompositeReporter`
/// (`src/reporter/composite.rs`) and `write_strict_abort_report`
/// (`src/pipeline/helpers.rs`). Both compare `as_i32()` explicitly (#1863).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitCode {
    Success,
    Failure,
    Interrupted,
    CollectError,
    UsageError,
}

impl ExitCode {
    pub fn as_i32(self) -> i32 {
        match self {
            Self::Success => 0,
            Self::Failure => 1,
            Self::Interrupted => 2,
            Self::CollectError => 3,
            Self::UsageError => 4,
        }
    }
}

impl From<ExitCode> for i32 {
    fn from(code: ExitCode) -> Self {
        code.as_i32()
    }
}

impl std::fmt::Display for ExitCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_i32())
    }
}

/// Result record for a single test execution, returned from the run phases.
#[derive(Debug, Clone)]
pub struct TestTiming {
    pub node_id: NodeId,
    pub duration_ms: DurationMs,
    pub outcome: OutcomeKind,
}

/// Tracks hard failures across the test run and implements the `--maxfail` stop condition.
///
/// Every call to [`record`](FailureAccumulator::record) returns `true` once the failure
/// count reaches `maxfail`. A `maxfail` of 0 disables the limit (never stops early).
pub(crate) struct FailureAccumulator {
    count: usize,
    max: usize,
}

impl FailureAccumulator {
    pub(crate) fn new(maxfail: usize) -> Self {
        Self {
            count: 0,
            max: maxfail,
        }
    }

    /// Record an outcome. Returns `true` if execution should stop (maxfail reached).
    #[must_use = "caller must check whether maxfail was reached"]
    pub(crate) fn record(&mut self, outcome: &TestOutcome) -> bool {
        if outcome.is_hard_failure() {
            self.count += 1;
        }
        self.max > 0 && self.count >= self.max
    }
}

#[cfg(test)]
mod timing_tests {
    use super::*;

    #[test]
    fn test_timing_fields() {
        let t = TestTiming {
            node_id: NodeId::from_raw("tests/test_foo.py::test_a"),
            duration_ms: DurationMs::new(42.5),
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(t.outcome, OutcomeKind::Passed);
        assert!((t.duration_ms.as_f64() - 42.5).abs() < 0.01);
        assert_eq!(t.node_id.to_string(), "tests/test_foo.py::test_a");
    }

    #[test]
    fn test_timing_outcome_is_enum() {
        let timing = TestTiming {
            node_id: NodeId::from_raw("test_mod::test_fn"),
            duration_ms: DurationMs::new(42.0),
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(timing.outcome, OutcomeKind::Passed);
    }
}

#[cfg(test)]
mod failure_accumulator_tests {
    use super::super::FailureDiagnostic;
    use super::*;

    #[test]
    fn test_no_maxfail_never_stops() {
        let mut acc = FailureAccumulator::new(0);
        let outcome = TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new())));
        assert!(!acc.record(&outcome));
        assert!(!acc.record(&outcome));
    }

    #[test]
    fn test_maxfail_stops_at_threshold() {
        let mut acc = FailureAccumulator::new(2);
        let fail = TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new())));
        let pass = TestOutcome::Passed { tips: None };
        assert!(!acc.record(&pass));
        assert!(!acc.record(&fail));
        assert!(acc.record(&fail)); // 2nd failure = stop
    }
}

#[cfg(test)]
mod exit_code_tests {
    use super::*;

    #[test]
    fn test_documented_exit_code_numbers() {
        for (code, expected) in [
            (ExitCode::Success, 0),
            (ExitCode::Failure, 1),
            (ExitCode::Interrupted, 2),
            (ExitCode::CollectError, 3),
            (ExitCode::UsageError, 4),
        ] {
            assert_eq!(
                code.as_i32(),
                expected,
                "exit codes are a published contract that CI configs branch on, and since #1863 as_i32() is also the only thing ordering exit-code precedence — editing a number here silently rewrites both at once"
            );
        }
    }

    #[test]
    fn test_usage_error_outranks_collect_error() {
        assert!(
            ExitCode::UsageError.as_i32() > ExitCode::CollectError.as_i32(),
            "write_strict_abort_report reports an unwritable --json file as UsageError over its CollectError baseline; invert this and a strict-abort run whose report never reached disk exits 3, which CI reads as an ordinary collection error while the missing artifact goes unexplained"
        );
    }
}
