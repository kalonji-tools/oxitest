use super::stats::RunStats;
use crate::types::{ExitCode, OutcomeKind};

/// Compute the process exit code from the final run statistics.
///
/// Priority (highest wins):
/// - **CollectError** (3) — one or more collection errors (import failures, syntax errors).
/// - **Interrupted** (2) — the run was interrupted (e.g. Ctrl-C / SIGINT).
/// - **Failure** (1) — at least one hard failure (failed, errored, timed out, strict-xpassed, or suite violation).
/// - **Success** (0) — all tests passed (or were skipped / xfailed).
pub(crate) fn compute_exit_code(
    stats: &RunStats,
    collect_err_count: usize,
    interrupted: bool,
) -> ExitCode {
    if collect_err_count > 0 {
        return ExitCode::CollectError;
    }
    if interrupted {
        return ExitCode::Interrupted;
    }
    if stats.counts.get(OutcomeKind::Failed) > 0
        || stats.counts.get(OutcomeKind::Error) > 0
        || stats.counts.xpassed_strict > 0
        || stats.counts.get(OutcomeKind::Timeout) > 0
        || stats.counts.strict_suite > 0
    {
        return ExitCode::Failure;
    }
    ExitCode::Success
}

#[cfg(test)]
mod tests {
    use super::*;

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
        stats.counts.xpassed_strict = 1;
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
        stats.counts.strict_suite = 2;
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
    fn test_exit_code_interrupted_takes_priority_over_failures() {
        let mut stats = RunStats::new();
        stats.counts.by_kind[OutcomeKind::Failed as usize] = 1;
        // interrupted must return Interrupted, even with failures
        assert_eq!(compute_exit_code(&stats, 0, true), ExitCode::Interrupted);
    }
}
