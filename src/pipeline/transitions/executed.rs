//! Transitions from Executed phase: retry, finalize

use pyo3::prelude::*;

use super::super::execution;
use super::super::helpers;
use super::super::{ExecutionResults, Pipeline, PipelinePhase};
use crate::reporter;
use crate::types::ExitCode;

impl Pipeline {
    // 12. retry: Executed -> Executed
    pub(crate) fn retry(mut self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        // Extract config values before borrowing self.phase mutably.
        let max_retries = self.cfg.exec.retries;
        let delay_secs = self.cfg.exec.retries_delay_secs;
        let timeout_secs = self.cfg.exec.timeout_secs;
        let keep_tmp_str = self.cfg.output.keep_tmp.as_str();
        let show_locals = self.cfg.output.show_locals;
        let show_internals = self.cfg.output.show_internals;

        let PipelinePhase::Executed {
            ref session,
            ref items,
            ref mut execution_results,
        } = self.phase
        else {
            unreachable!("retry called outside Executed phase")
        };

        let not_interrupted = !execution_results.interrupted;
        if max_retries == 0 || !not_interrupted {
            return Ok(self);
        }

        let failed_items = crate::retry::identify_failed_items(items, &execution_results.timings);
        if failed_items.is_empty() {
            return Ok(self);
        }

        let retry_ctx = crate::retry::RetryContext {
            py,
            max_retries,
            delay_secs,
            session,
            timeout_secs,
            opts: execution::DebugOptions {
                debug_mode: None,
                keep_tmp: keep_tmp_str,
                show_locals,
                show_internals,
            },
        };
        let crate::retry::RetryResult {
            flaky_ids,
            retry_timings,
        } = crate::retry::run_retries(
            &retry_ctx,
            &failed_items,
            execution_results.reporter.as_mut(),
        );

        let original_timings = std::mem::take(&mut execution_results.timings);
        execution_results.timings =
            crate::retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

        Ok(self)
    }

    // 13. finalize: Executed -> terminal
    pub(crate) fn finalize(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::Executed {
            session,
            items: _,
            execution_results,
        } = self.phase
        else {
            unreachable!("finalize called outside Executed phase")
        };
        let mut shared = self.shared;
        let ExecutionResults {
            timings,
            interrupted,
            mut reporter,
        } = execution_results;
        if let Ok(ft) = reporter::bridge::get_fixture_timings(&session, py)
            && !ft.is_empty()
        {
            reporter.set_fixture_timings(ft);
        }

        helpers::finalize(
            &mut shared.cache,
            &timings,
            shared.cfg.features.cache_max_age,
            &shared.rootdir,
        );

        if let Ok(stats) = reporter::bridge::get_cache_stats(&session, py)
            && stats.hits + stats.misses > 0
        {
            reporter.set_fixture_cache_stats(stats.hits, stats.misses, stats.breakdown);
        }

        let code = reporter
            .finish(&[], interrupted, &reporter::ReporterSession::new(0))
            .code();
        Ok(code)
    }
}
