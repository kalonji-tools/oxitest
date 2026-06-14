//! Retry logic for failed tests.
//!
//! After the initial test phase, failed tests are re-run serially up to
//! `retries` times. A test that fails initially but passes on retry is
//! labeled as "flaky" (exit 0). A test that fails all retries remains
//! a hard failure (exit 1).

use std::sync::Arc;

use crate::bridge;
use crate::pipeline::execution;
use crate::reporter::Reporter;
use crate::types::{NodeId, OutcomeKind, TestItem, TestOutcome, TestTiming};

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
    pub session: &'a bridge::FixtureSession,
    pub timeout_secs: Option<u64>,
    pub opts: execution::DebugOptions<'a>,
}

/// Identify test items whose timings show a failure outcome.
///
/// Returns each failed item paired with its original outcome kind so the
/// retry logic can thread it into `TestOutcome::Flaky`.
pub(crate) fn identify_failed_items(
    items: &[Arc<TestItem>],
    timings: &[TestTiming],
) -> Vec<(Arc<TestItem>, OutcomeKind)> {
    let failed_map: std::collections::HashMap<&str, OutcomeKind> = timings
        .iter()
        .filter(|t| t.outcome.is_failure())
        .map(|t| (t.node_id.as_ref(), t.outcome))
        .collect();
    items
        .iter()
        .filter_map(|i| {
            failed_map
                .get(i.node_id.as_ref())
                .map(|&kind| (i.clone(), kind))
        })
        .collect()
}

/// Merge retry timings into the original timing list.
pub(crate) fn merge_flaky_timings(
    original: Vec<TestTiming>,
    flaky_ids: &[NodeId],
    retry_timings: Vec<TestTiming>,
) -> Vec<TestTiming> {
    let flaky_set: std::collections::HashSet<&str> =
        flaky_ids.iter().map(|id| id.as_ref()).collect();
    let mut merged: Vec<TestTiming> = original
        .into_iter()
        .filter(|t| !flaky_set.contains(t.node_id.as_ref()))
        .collect();
    merged.extend(retry_timings);
    merged
}

/// Re-run failed tests serially, up to `max_retries` attempts each.
///
/// Returns which tests are flaky (passed on retry) and updated timings.
/// Tests that pass on any retry are reported as `TestOutcome::Flaky` to the reporter.
/// Tests that fail all retries are NOT re-reported (original failure stands).
pub(crate) fn run_retries(
    ctx: &RetryContext<'_>,
    failed_items: &[(Arc<TestItem>, OutcomeKind)],
    rep: &mut dyn Reporter,
) -> RetryResult {
    let mut flaky_ids = Vec::new();
    let mut retry_timings = Vec::new();

    for (item, original_kind) in failed_items {
        let mut passed = false;

        for attempt in 1..=ctx.max_retries {
            if ctx.delay_secs > 0 {
                std::thread::sleep(std::time::Duration::from_secs(ctx.delay_secs));
            }

            let retry_opts = execution::DebugOptions {
                debug_mode: None,
                ..ctx.opts
            };
            let (outcome, duration_ms) =
                execution::run_timed(ctx.py, item, ctx.session, ctx.timeout_secs, retry_opts);

            if !outcome.is_hard_failure() {
                let flaky_outcome = TestOutcome::Flaky {
                    message: format!("passed on retry {} of {}", attempt, ctx.max_retries),
                    original: *original_kind,
                };
                rep.test_started(item);
                rep.test_completed(item, &flaky_outcome, duration_ms, None);

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
    use crate::reporter::test_helpers::make_timing;
    use crate::types::TestItem;

    #[test]
    fn test_retry_result_default_empty() {
        let result = RetryResult {
            flaky_ids: vec![],
            retry_timings: vec![],
        };
        assert!(result.flaky_ids.is_empty());
        assert!(result.retry_timings.is_empty());
    }

    #[test]
    fn identify_failed_items_returns_only_failures() {
        let item_a = TestItem::builder_raw("tests/test_a.py::test_pass").arc();
        let item_b = TestItem::builder_raw("tests/test_a.py::test_fail").arc();
        let item_c = TestItem::builder_raw("tests/test_a.py::test_err").arc();
        let items = vec![item_a, item_b, item_c];
        let timings = vec![
            make_timing("tests/test_a.py::test_pass", OutcomeKind::Passed),
            make_timing("tests/test_a.py::test_fail", OutcomeKind::Failed),
            make_timing("tests/test_a.py::test_err", OutcomeKind::Error),
        ];
        let failed = identify_failed_items(&items, &timings);
        assert_eq!(failed.len(), 2);
        assert!(failed.iter().any(|(i, _)| i.node_id.contains("test_fail")));
        assert!(failed.iter().any(|(i, _)| i.node_id.contains("test_err")));
    }

    #[test]
    fn identify_failed_items_empty_when_all_pass() {
        let item = TestItem::builder_raw("tests/test_a.py::test_ok").arc();
        let items = vec![item];
        let timings = vec![make_timing("tests/test_a.py::test_ok", OutcomeKind::Passed)];
        let failed = identify_failed_items(&items, &timings);
        assert!(failed.is_empty());
    }

    #[test]
    fn merge_flaky_timings_replaces_failed_entries() {
        let original = vec![
            make_timing("tests/test_a.py::test_pass", OutcomeKind::Passed),
            make_timing("tests/test_a.py::test_flaky", OutcomeKind::Failed),
        ];
        let flaky_ids = vec![NodeId::from_raw("tests/test_a.py::test_flaky")];
        let retry = vec![make_timing(
            "tests/test_a.py::test_flaky",
            OutcomeKind::Flaky,
        )];
        let merged = merge_flaky_timings(original, &flaky_ids, retry);
        assert_eq!(merged.len(), 2);
        let flaky = merged
            .iter()
            .find(|t| t.node_id.contains("test_flaky"))
            .unwrap();
        assert_eq!(flaky.outcome, OutcomeKind::Flaky);
        let pass = merged
            .iter()
            .find(|t| t.node_id.contains("test_pass"))
            .unwrap();
        assert_eq!(pass.outcome, OutcomeKind::Passed);
    }

    #[test]
    fn merge_flaky_timings_no_flaky_returns_original() {
        let original = vec![
            make_timing("tests/test_a.py::test_a", OutcomeKind::Passed),
            make_timing("tests/test_a.py::test_b", OutcomeKind::Failed),
        ];
        let merged = merge_flaky_timings(original, &[], vec![]);
        assert_eq!(merged.len(), 2);
    }
}
