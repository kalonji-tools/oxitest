use crate::types::OutcomeKind;

use super::TestCache;

impl TestCache {
    /// Returns node IDs whose last recorded outcome was a failure
    /// (failed, error, or timeout).
    pub fn last_failed_ids(&self) -> std::collections::HashSet<String> {
        self.inner
            .timings
            .iter()
            .filter(|(_, entry)| {
                entry
                    .last_outcome
                    .as_ref()
                    .is_some_and(OutcomeKind::is_retryable_failure)
            })
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// Record outcomes from `&[TestTiming]` directly.
    pub fn record_timing_outcomes(&mut self, timings: &[crate::types::TestTiming]) {
        let mut changed = false;
        for t in timings {
            if let Some(entry) = self.inner.timings.get_mut(t.node_id.as_ref()) {
                entry.last_outcome = Some(t.outcome);
                if t.outcome == OutcomeKind::Flaky {
                    entry.flaky_count = entry.flaky_count.saturating_add(1);
                }
                changed = true;
            }
        }
        if changed {
            self.dirty = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::test_helpers::{cache_with_entries, make_timing};
    use super::*;
    use crate::types::{DurationMs, NodeId, OutcomeKind, TestTiming};

    #[test]
    fn last_failed_ids_returns_failed_and_error_and_timeout() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
            ("tests/test_foo.py::test_c", 30.0),
            ("tests/test_foo.py::test_d", 40.0),
        ]);
        let timings = vec![
            make_timing("tests/test_foo.py::test_a", 10.0, OutcomeKind::Failed),
            make_timing("tests/test_foo.py::test_b", 20.0, OutcomeKind::Passed),
            make_timing("tests/test_foo.py::test_c", 30.0, OutcomeKind::Error),
            make_timing("tests/test_foo.py::test_d", 40.0, OutcomeKind::Timeout),
        ];
        cache.record_timing_outcomes(&timings);
        let failed = cache.last_failed_ids();
        assert!(failed.contains("tests/test_foo.py::test_a"));
        assert!(!failed.contains("tests/test_foo.py::test_b"));
        assert!(failed.contains("tests/test_foo.py::test_c"));
        assert!(failed.contains("tests/test_foo.py::test_d"));
    }

    #[test]
    fn last_failed_ids_returns_empty_on_cold_cache() {
        let cache = TestCache::empty();
        assert!(cache.last_failed_ids().is_empty());
    }

    #[test]
    fn record_timing_outcomes_sets_last_outcome_on_known_entries() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Failed,
        )];
        cache.record_timing_outcomes(&timings);
        assert_eq!(
            cache.inner.timings["tests/test_foo.py::test_a"].last_outcome,
            Some(OutcomeKind::Failed)
        );
        assert!(cache.dirty);
    }

    #[test]
    fn record_timing_outcomes_ignores_unknown_node_ids() {
        let mut cache = TestCache::empty();
        let timings = vec![make_timing(
            "tests/test_foo.py::test_unknown",
            10.0,
            OutcomeKind::Failed,
        )];
        cache.record_timing_outcomes(&timings);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty);
    }

    #[test]
    fn record_timing_outcomes_sets_dirty() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Passed,
        )];
        cache.record_timing_outcomes(&timings);
        assert!(cache.dirty);
    }

    #[test]
    fn test_flaky_count_increments_on_flaky_outcome() {
        let mut cache = TestCache::empty();
        let timings = vec![TestTiming {
            node_id: NodeId::from_raw("tests/test_a.py::test_x"),
            duration_ms: DurationMs::new(100.0),
            outcome: OutcomeKind::Flaky,
        }];
        cache.merge_timings(&timings, 50);
        cache.record_timing_outcomes(&timings);
        let failed_ids = cache.last_failed_ids();
        assert!(
            !failed_ids.contains("tests/test_a.py::test_x"),
            "flaky should not be in last_failed_ids"
        );
        assert_eq!(
            cache.inner.timings["tests/test_a.py::test_x"].flaky_count, 1,
            "flaky_count should be incremented"
        );
    }
}
