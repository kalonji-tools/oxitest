use crate::types::{DurationMs, TestItem, TestOutcome};

use super::stats::{self, RunStats};

pub(crate) struct ReporterSession {
    stats: RunStats,
    strict_suite_count: usize,
}

impl ReporterSession {
    pub(crate) fn new(strict_suite_count: usize) -> Self {
        Self {
            stats: RunStats::new(),
            strict_suite_count,
        }
    }

    pub(crate) fn record_outcome(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
    ) {
        self.stats.record(item, outcome);
        self.stats.record_timing(item.node_id.as_ref(), duration_ms);
    }

    pub(crate) fn record_strict_suite(&mut self) {
        self.stats.record_strict_suite(self.strict_suite_count);
    }

    pub(crate) fn stats(&self) -> &RunStats {
        &self.stats
    }

    pub(crate) fn set_fixture_cache_stats(
        &mut self,
        hits: usize,
        misses: usize,
        breakdown: Vec<stats::FixtureCacheEntry>,
    ) {
        self.stats.fixture_cache_hits = hits;
        self.stats.fixture_cache_misses = misses;
        self.stats.fixture_cache_breakdown = breakdown;
    }

    pub(crate) fn set_fixture_timings(&mut self, timings: Vec<stats::FixtureTimingEntry>) {
        self.stats.fixture_timings = timings;
    }

    pub(crate) fn record_teardown_warning(&mut self, context: &str, error: &str) {
        self.stats.warning_msgs.push(stats::WarningEntry {
            context: context.to_string(),
            message: error.to_string(),
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DurationMs, TestItem, TestOutcome};

    #[test]
    fn record_outcome_updates_stats_and_timing() {
        let mut session = ReporterSession::new(0);
        let item = TestItem::builder("tests/test_foo.py", "test_a").build();
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        session.record_outcome(&item, &outcome, DurationMs::new(42.5));

        assert_eq!(session.stats().passed, 1);
        assert_eq!(session.stats().timings.len(), 1);
    }

    #[test]
    fn record_strict_suite_delegates_to_stats() {
        let mut session = ReporterSession::new(3);
        session.record_strict_suite();
        assert_eq!(session.stats().strict_suite, 3);
    }

    #[test]
    fn set_fixture_cache_stats_stores_values() {
        let mut session = ReporterSession::new(0);
        session.set_fixture_cache_stats(5, 2, vec![]);
        assert_eq!(session.stats().fixture_cache_hits, 5);
        assert_eq!(session.stats().fixture_cache_misses, 2);
    }

    #[test]
    fn set_fixture_timings_stores_values() {
        let mut session = ReporterSession::new(0);
        let entry = stats::FixtureTimingEntry {
            name: "db".into(),
            total_setup_ms: 100.0,
            setup_count: 1,
            total_teardown_ms: 10.0,
            teardown_count: 1,
        };
        session.set_fixture_timings(vec![entry]);
        assert_eq!(session.stats().fixture_timings.len(), 1);
        assert_eq!(session.stats().fixture_timings[0].name, "db");
    }

    #[test]
    fn record_teardown_warning_appends_to_stats() {
        let mut session = ReporterSession::new(0);
        session.record_teardown_warning("end_module(test.py)", "RuntimeError: boom");
        assert_eq!(session.stats().warning_msgs.len(), 1);
        assert_eq!(
            session.stats().warning_msgs[0].context,
            "end_module(test.py)"
        );
        assert_eq!(
            session.stats().warning_msgs[0].message,
            "RuntimeError: boom"
        );
    }
}
