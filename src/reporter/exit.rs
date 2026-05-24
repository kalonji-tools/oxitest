use super::stats::RunStats;

/// Compute the process exit code from the final run statistics.
///
/// Priority (highest wins):
/// - **3** — one or more collection errors (import failures, syntax errors).
/// - **2** — the run was interrupted (e.g. Ctrl-C / SIGINT).
/// - **1** — at least one hard failure (failed, errored, timed out, strict-xpassed, or suite violation).
/// - **0** — all tests passed (or were skipped / xfailed).
pub(crate) fn compute_exit_code(
    stats: &RunStats,
    collect_err_count: usize,
    interrupted: bool,
) -> i32 {
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
