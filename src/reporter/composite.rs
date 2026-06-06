//! `CompositeReporter` — fans all reporter events to a list of inner reporters.

use crate::types::{CollectError, DurationMs, ExitCode};

use super::session::ReporterSession;
use super::stats;
use super::traits::{ExitVote, Reporter};

/// Fans all reporter events to a list of inner reporters.
///
/// Owns the [`ReporterSession`] for the run: records stats once in
/// `test_completed` and passes the session to sub-reporters via `finish`.
/// `finish` collects [`ExitVote`]s from every inner reporter and returns the
/// maximum code voted (treating `Abstain` as 0).
pub struct CompositeReporter {
    reporters: Vec<Box<dyn Reporter>>,
    session: ReporterSession,
}

impl CompositeReporter {
    pub fn new(reporters: Vec<Box<dyn Reporter>>, strict_suite_count: usize) -> Self {
        Self {
            reporters,
            session: ReporterSession::new(strict_suite_count),
        }
    }
}

impl Reporter for CompositeReporter {
    fn test_started(&mut self, item: &crate::types::TestItem) {
        for r in &mut self.reporters {
            r.test_started(item);
        }
    }

    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: DurationMs,
    ) {
        self.session.record_outcome(item, outcome, duration_ms);
        for r in &mut self.reporters {
            r.test_completed(item, outcome, duration_ms);
        }
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        _session: &ReporterSession,
    ) -> ExitVote {
        self.session.record_strict_suite();
        self.reporters
            .iter_mut()
            .map(|r| r.finish(collect_errors, interrupted, &self.session))
            .filter_map(|v| match v {
                ExitVote::Code(c) => Some(c),
                ExitVote::Abstain => None,
            })
            .max()
            .map_or(ExitVote::Code(ExitCode::Success), ExitVote::Code)
    }

    fn record_teardown_warning(&mut self, context: &str, error: &str) {
        self.session.record_teardown_warning(context, error);
        for r in &mut self.reporters {
            r.record_teardown_warning(context, error);
        }
    }

    fn set_fixture_cache_stats(
        &mut self,
        hits: usize,
        misses: usize,
        breakdown: Vec<stats::FixtureCacheEntry>,
    ) {
        self.session
            .set_fixture_cache_stats(hits, misses, breakdown);
    }

    fn set_fixture_timings(&mut self, timings: Vec<stats::FixtureTimingEntry>) {
        self.session.set_fixture_timings(timings);
    }
}
