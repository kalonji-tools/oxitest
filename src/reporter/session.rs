use std::sync::Arc;

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
        self.stats.record_timing(&item.node_id, duration_ms);
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
        self.stats.fixture_cache = Some(stats::FixtureCacheStats {
            hits,
            misses,
            breakdown,
        });
    }

    pub(crate) fn set_fixture_timings(&mut self, timings: Vec<stats::FixtureTimingEntry>) {
        self.stats.fixture_timings = timings;
    }

    pub(crate) fn record_teardown_warning(&mut self, context: &str, error: &str) {
        self.stats.diagnostics.entries.push(stats::DiagnosticEntry {
            severity: stats::DiagnosticSeverity::Warning,
            context: Arc::from(context),
            message: error.to_string(),
            file: None,
            lineno: None,
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
        let outcome = TestOutcome::Passed { tips: None };
        session.record_outcome(&item, &outcome, DurationMs::new(42.5));

        assert_eq!(
            session
                .stats()
                .counts
                .get(crate::types::OutcomeKind::Passed),
            1
        );
        assert_eq!(session.stats().timings.len(), 1);
    }

    #[test]
    fn record_strict_suite_delegates_to_stats() {
        let mut session = ReporterSession::new(3);
        session.record_strict_suite();
        assert_eq!(session.stats().strict.suite_violations, 3);
    }

    #[test]
    fn set_fixture_cache_stats_stores_values() {
        let mut session = ReporterSession::new(0);
        session.set_fixture_cache_stats(5, 2, vec![]);
        assert_eq!(session.stats().fixture_cache.as_ref().unwrap().hits, 5);
        assert_eq!(session.stats().fixture_cache.as_ref().unwrap().misses, 2);
    }

    #[test]
    fn set_fixture_timings_stores_values() {
        let mut session = ReporterSession::new(0);
        let entry = stats::FixtureTimingEntry {
            name: "db".into(),
            total_setup: crate::types::DurationMs::new(100.0),
            setup_count: 1,
            total_teardown: crate::types::DurationMs::new(10.0),
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
        assert_eq!(session.stats().diagnostics.entries.len(), 1);
        assert_eq!(
            &*session.stats().diagnostics.entries[0].context,
            "end_module(test.py)"
        );
        assert_eq!(
            session.stats().diagnostics.entries[0].message,
            "RuntimeError: boom"
        );
    }
}
