//! Reporter trait, `ExitVote`, `StandardReporter` trait, and `standard_finish`.

use crate::types::{CollectError, DurationMs, ExitCode};

use super::print::{print_collect_errors, print_summary_section};
use super::session::ReporterSession;
use super::stats;
use super::ReporterOpts;

// ─── ExitVote ────────────────────────────────────────────────────────────────

/// Exit code vote from a reporter.
#[derive(Debug, Clone, Copy)]
pub enum ExitVote {
    /// Reporter does not influence exit code.
    Abstain,
    /// Reporter votes for this exit code.
    Code(ExitCode),
}

impl ExitVote {
    /// Extract the exit code, treating `Abstain` as `ExitCode::Success`.
    pub fn code(self) -> ExitCode {
        match self {
            ExitVote::Abstain => ExitCode::Success,
            ExitVote::Code(c) => c,
        }
    }
}

// ─── Trait ───────────────────────────────────────────────────────────────────

/// Event sink for test results, progress, and the final summary.
///
/// Lifecycle per test: `test_started` → `test_completed`. After all tests,
/// `finish` is called once with any collection errors and an interrupted flag.
/// `finish` returns an [`ExitVote`] that contributes to the process exit code.
///
/// Implementers: [`TtyReporter`](super::TtyReporter),
/// [`CiReporter`](super::CiReporter),
/// [`JsonReporter`](super::json::JsonReporter),
/// [`PyPluginReporter`](super::plugin::PyPluginReporter),
/// [`CompositeReporter`](super::composite::CompositeReporter).
pub trait Reporter {
    fn test_started(&mut self, item: &crate::types::TestItem);
    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: DurationMs,
    );
    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        session: &ReporterSession,
    ) -> ExitVote;

    /// Record a teardown warning (default: no-op).
    /// `context` identifies what failed (e.g. "end_module(path)" or "end_session").
    /// `error` is the stringified error message.
    fn record_teardown_warning(&mut self, _context: &str, _error: &str) {}

    /// Set fixture cache statistics for display in the summary.
    fn set_fixture_cache_stats(
        &mut self,
        _hits: usize,
        _misses: usize,
        _breakdown: Vec<stats::FixtureCacheEntry>,
    ) {
    }

    /// Set fixture timing data for display in the summary.
    fn set_fixture_timings(&mut self, _timings: Vec<stats::FixtureTimingEntry>) {}
}

// ─── StandardReporter ────────────────────────────────────────────────────────

pub(crate) trait StandardReporter {
    fn pre_finish(&mut self);
    fn run_opts(&self) -> &ReporterOpts;
}

pub(crate) fn standard_finish(
    r: &mut impl StandardReporter,
    session: &ReporterSession,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitVote {
    print_collect_errors(collect_errors, r.run_opts().use_color);
    ExitVote::Code(print_summary_section(
        session.stats(),
        r.run_opts(),
        collect_errors,
        interrupted,
    ))
}
