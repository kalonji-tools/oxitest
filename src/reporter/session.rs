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
        self.stats
            .warning_msgs
            .push((context.to_string(), error.to_string()));
    }
}
