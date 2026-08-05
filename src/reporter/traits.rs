//! Reporter trait, `ExitVote`, `StandardReporter` trait, and `standard_finish`.

use crate::types::{CollectError, DurationMs, ExitCode};

use super::ReporterOpts;
use super::print::{print_collect_errors, print_summary_section};
use super::session::ReporterSession;
use super::stats;

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
            Self::Abstain => ExitCode::Success,
            Self::Code(c) => c,
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
    fn test_started(&mut self, _item: &crate::types::TestItem) {}
    fn test_completed(
        &mut self,
        item: &crate::types::TestItem,
        outcome: &crate::types::TestOutcome,
        duration_ms: DurationMs,
        parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
    );
    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        session: &ReporterSession,
    ) -> ExitVote;

    /// Record a teardown warning (default: no-op).
    /// `context` identifies what failed (e.g. `end_module(path)` or `end_task`).
    /// `error` is the stringified error message.
    fn record_teardown_warning(&mut self, _context: &str, _error: &str) {}

    /// Record diagnostic entries emitted by the Python bridge (default: no-op).
    fn record_diagnostics(&mut self, _entries: Vec<stats::DiagnosticEntry>) {}

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

/// Reporters whose shutdown differs only in what they must drain first.
pub(crate) trait StandardReporter {
    /// Drain whatever this reporter has buffered, before the summary is printed.
    ///
    /// Called by [`standard_finish`] — **do not call it as well**. Implementations
    /// are not idempotent: `CiReporter` prints `dot_buf` and its deferred
    /// diagnostics without clearing either, so a second call reprints both.
    fn pre_finish(&mut self);
    fn run_opts(&self) -> &ReporterOpts;
}

/// Shared reporter shutdown: drain buffers, print collection errors, then the summary.
///
/// Owns the [`StandardReporter::pre_finish`] call so the ordering cannot be got
/// wrong or forgotten by an implementer.
pub(crate) fn standard_finish(
    r: &mut impl StandardReporter,
    session: &ReporterSession,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitVote {
    r.pre_finish();
    print_collect_errors(collect_errors, r.run_opts().use_color);
    ExitVote::Code(print_summary_section(
        session.stats(),
        r.run_opts(),
        collect_errors,
        interrupted,
    ))
}
