//! Retry logic for failed tests.
//!
//! After the initial test phase, failed tests are re-run serially up to
//! `retries` times. A test that fails initially but passes on retry is
//! labeled as "flaky" (exit 0). A test that fails all retries remains
//! a hard failure (exit 1).

use std::sync::Arc;

use crate::pipeline::traits::{Session, TestRunner};
use crate::reporter::Reporter;
use crate::types::{DurationMs, NodeId, OutcomeKind, TestItem, TestOutcome, TestTiming};

/// Result of the retry phase.
pub(crate) struct RetryResult {
    /// Node IDs of tests that passed on retry (flaky).
    pub flaky_ids: Vec<NodeId>,
    /// Updated timings — flaky tests get their retry timing.
    pub retry_timings: Vec<TestTiming>,
}

/// Context for running retries — bundles parameters to stay under clippy's argument limit.
pub(crate) struct RetryContext<'a> {
    pub py: pyo3::Python<'a>,
    pub max_retries: usize,
    pub delay_secs: u64,
    pub session: &'a dyn Session,
    pub runner: &'a dyn TestRunner,
    pub timeout_secs: Option<u64>,
}

/// Re-run failed tests serially, up to `max_retries` attempts each.
///
/// Returns which tests are flaky (passed on retry) and updated timings.
/// Tests that pass on any retry are reported as `TestOutcome::Flaky` to the reporter.
/// Tests that fail all retries are NOT re-reported (original failure stands).
pub(crate) fn run_retries(
    ctx: &RetryContext<'_>,
    failed_items: &[Arc<TestItem>],
    rep: &mut dyn Reporter,
) -> RetryResult {
    let mut flaky_ids = Vec::new();
    let mut retry_timings = Vec::new();

    for item in failed_items {
        let mut passed = false;

        for attempt in 1..=ctx.max_retries {
            if ctx.delay_secs > 0 {
                std::thread::sleep(std::time::Duration::from_secs(ctx.delay_secs));
            }

            let start = std::time::Instant::now();
            let outcome = ctx
                .runner
                .run_test(ctx.py, item, ctx.session, ctx.timeout_secs);
            let duration_ms = DurationMs::new(start.elapsed().as_secs_f64() * 1000.0);

            if !outcome.is_hard_failure() {
                let flaky_outcome = TestOutcome::Flaky {
                    message: format!("passed on retry {} of {}", attempt, ctx.max_retries),
                };
                rep.test_started(item);
                rep.test_completed(item, &flaky_outcome, duration_ms);

                retry_timings.push(TestTiming {
                    node_id: item.node_id.clone(),
                    duration_ms,
                    outcome: OutcomeKind::Flaky,
                });

                flaky_ids.push(item.node_id.clone());
                passed = true;
                break;
            }
        }

        if !passed {
            // All retries failed — keep original timing (already recorded).
        }
    }

    RetryResult {
        flaky_ids,
        retry_timings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_retry_result_default_empty() {
        let result = RetryResult {
            flaky_ids: vec![],
            retry_timings: vec![],
        };
        assert!(result.flaky_ids.is_empty());
        assert!(result.retry_timings.is_empty());
    }
}
