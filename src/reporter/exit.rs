use super::stats::RunStats;
use crate::types::{ExitCode, OutcomeKind};

/// Compute the process exit code from the final run statistics.
///
/// Priority (highest wins):
/// - **`UsageError`** (4) — one or more tests errored because the suite is wired
///   wrong: a fixture the test cannot see, or a fixture dependency whose lifetime
///   cannot hold. A misconfigured suite makes its own assertion results
///   untrustworthy, so the more specific signal wins (#1761). This agrees with
///   `CompositeReporter`, which already folds 4 above 3 (#1863) — the two must
///   not disagree depending on which reporters are active.
/// - **`CollectError`** (3) — one or more collection errors (import failures, syntax errors).
/// - **Interrupted** (2) — the run was interrupted (e.g. Ctrl-C / SIGINT).
/// - **Failure** (1) — at least one hard failure (failed, errored, timed out, strict-xpassed, or suite violation).
/// - **Success** (0) — all tests passed (or were skipped / xfailed).
pub fn compute_exit_code(
    stats: &RunStats,
    collect_err_count: usize,
    interrupted: bool,
) -> ExitCode {
    if !stats.usage_error_ids.is_empty() {
        return ExitCode::UsageError;
    }
    if collect_err_count > 0 {
        return ExitCode::CollectError;
    }
    if interrupted {
        return ExitCode::Interrupted;
    }
    if stats.counts.get(OutcomeKind::Failed) > 0
        || stats.counts.get(OutcomeKind::Error) > 0
        || stats.strict.xpassed_strict > 0
        || stats.counts.get(OutcomeKind::Timeout) > 0
        || stats.strict.suite_violations > 0
    {
        return ExitCode::Failure;
    }
    ExitCode::Success
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::NodeId;

    #[test]
    fn test_exit_code_zero_when_all_pass() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Success);
    }

    #[test]
    fn test_exit_code_one_when_failed() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_one_when_errored() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Error as usize] = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_one_when_xpassed_strict() {
        let mut stats = RunStats::new();
        stats.strict.xpassed_strict = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_one_when_timeout() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Timeout as usize] = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_two_when_interrupted() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 0, true), ExitCode::Interrupted);
    }

    #[test]
    fn test_exit_code_three_when_collect_error() {
        let stats = RunStats::new();
        assert_eq!(compute_exit_code(&stats, 1, false), ExitCode::CollectError);
    }

    #[test]
    fn test_exit_code_one_when_strict_suite_violations() {
        let mut stats = RunStats::new();
        stats.strict.suite_violations = 2;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_zero_when_only_flaky() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Flaky as usize] = 3;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Success);
    }

    #[test]
    fn test_exit_code_one_when_failed_and_flaky() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Flaky as usize] = 2;
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        assert_eq!(compute_exit_code(&stats, 0, false), ExitCode::Failure);
    }

    #[test]
    fn test_exit_code_collect_error_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        stats.counts.by_kind[OutcomeKind::Timeout as usize] = 1;
        // collect_err_count > 0 must return CollectError, even with failures
        assert_eq!(compute_exit_code(&stats, 1, false), ExitCode::CollectError);
    }

    #[test]
    fn test_exit_code_four_when_a_usage_error_was_recorded() {
        let mut stats = RunStats::new();
        stats
            .usage_error_ids
            .insert(NodeId::from_raw("t.py::test_wired_wrong"));
        assert_eq!(
            compute_exit_code(&stats, 0, false),
            ExitCode::UsageError,
            "a suite that is wired wrong must not report the same code as a suite whose assertions failed — telling those two apart in CI is the whole purpose of #1761"
        );
    }

    #[test]
    fn test_usage_error_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        stats
            .usage_error_ids
            .insert(NodeId::from_raw("t.py::test_wired_wrong"));
        assert_eq!(
            compute_exit_code(&stats, 0, false),
            ExitCode::UsageError,
            "a misconfigured suite makes its own assertion results untrustworthy, so the more specific signal wins; ranking 1 above 4 would hide the wiring error behind whichever test happened to fail"
        );
    }

    #[test]
    fn test_usage_error_takes_priority_over_collect_errors() {
        let mut stats = RunStats::new();
        stats
            .usage_error_ids
            .insert(NodeId::from_raw("t.py::test_wired_wrong"));
        assert_eq!(
            compute_exit_code(&stats, 1, false),
            ExitCode::UsageError,
            "CompositeReporter already folds 4 above 3 by as_i32 (#1863); if this ladder ranked them the other way the exit code would depend on which reporters were active"
        );
    }

    #[test]
    fn test_no_usage_error_leaves_the_ladder_unchanged() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        assert_eq!(
            compute_exit_code(&stats, 0, false),
            ExitCode::Failure,
            "an ordinary failing run must still exit 1 — a rung that fired on a zero counter would turn every failing suite into a usage error and destroy the distinction it was added to make"
        );
    }

    #[test]
    fn test_a_flaky_retry_clears_only_its_own_usage_error() {
        // Two tests, two node ids. One holds a wiring error. The other holds an
        // ordinary error and then passes on retry. Clearing the second must not
        // cancel the first, or an unrelated flaky test silently drops a
        // misconfigured suite back to exit 1.
        let mut stats = RunStats::new();
        stats
            .usage_error_ids
            .insert(NodeId::from_raw("t.py::test_wired_wrong"));
        stats.counts.by_kind[OutcomeKind::Error as usize] = 2;

        stats.record_flaky(
            &NodeId::from_raw("t.py::test_unrelated"),
            OutcomeKind::Error,
        );

        assert_eq!(
            compute_exit_code(&stats, 0, false),
            ExitCode::UsageError,
            "a different test passing on retry must leave the wiring error standing; clearing on outcome kind alone would cancel it and report the suite as merely failing"
        );
    }

    #[test]
    fn test_a_flaky_retry_clears_its_own_usage_error() {
        // The mirror of the test above: when the retried test *is* the one that
        // held the wiring error, the run no longer holds one.
        let mut stats = RunStats::new();
        stats
            .usage_error_ids
            .insert(NodeId::from_raw("t.py::test_wired_wrong"));
        stats.counts.by_kind[OutcomeKind::Error as usize] = 1;

        stats.record_flaky(
            &NodeId::from_raw("t.py::test_wired_wrong"),
            OutcomeKind::Error,
        );

        assert_eq!(
            compute_exit_code(&stats, 0, false),
            ExitCode::Success,
            "exit-codes.md promises that a flaky test does not affect the code; a violation that is never cleared would break that promise for the one test it belongs to"
        );
    }

    #[test]
    fn test_exit_code_interrupted_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        // interrupted must return Interrupted, even with failures
        assert_eq!(compute_exit_code(&stats, 0, true), ExitCode::Interrupted);
    }
}
