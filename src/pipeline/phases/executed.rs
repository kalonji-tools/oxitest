use pyo3::prelude::*;

use super::super::{helpers, Executed, ExecutionResults, Pipeline};
use crate::pipeline::execution::DebugOptions;
use crate::types::ExitCode;
use crate::{reporter, retry};

impl Pipeline<Executed> {
    pub(crate) fn retry(mut self, py: Python<'_>) -> Result<Pipeline<Executed>, ExitCode> {
        let not_interrupted = !self.state.execution_results.interrupted;
        if self.cfg.exec.retries == 0 || !not_interrupted {
            return Ok(self);
        }

        let failed_items =
            retry::identify_failed_items(&self.state.items, &self.state.execution_results.timings);
        if failed_items.is_empty() {
            return Ok(self);
        }

        let retry_ctx = retry::RetryContext {
            py,
            max_retries: self.cfg.exec.retries,
            delay_secs: self.cfg.exec.retries_delay_secs,
            session: &self.state.session,
            timeout_secs: self.cfg.exec.timeout_secs,
            opts: DebugOptions {
                debug_mode: None,
                keep_tmp: self.cfg.output.keep_tmp.as_ref().map(|m| m.as_str()),
                show_locals: self.cfg.output.show_locals,
                show_internals: self.cfg.output.show_internals,
            },
        };
        let retry::RetryResult {
            flaky_ids,
            retry_timings,
        } = retry::run_retries(
            &retry_ctx,
            &failed_items,
            self.state.execution_results.reporter.as_mut(),
        );

        let original_timings = std::mem::take(&mut self.state.execution_results.timings);
        self.state.execution_results.timings =
            retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

        Ok(self)
    }

    pub(crate) fn finalize(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let (mut shared, state) = self.into_parts();
        let ExecutionResults {
            timings,
            interrupted,
            mut reporter,
        } = state.execution_results;

        if let Ok(ft) = reporter::bridge::get_fixture_timings(&state.session, py) {
            if !ft.is_empty() {
                reporter.set_fixture_timings(ft);
            }
        }

        helpers::finalize(
            &mut shared.cache,
            &timings,
            shared.cfg.features.cache_max_age,
            &shared.rootdir,
        );

        if let Ok(stats) = reporter::bridge::get_cache_stats(&state.session, py) {
            if stats.hits + stats.misses > 0 {
                reporter.set_fixture_cache_stats(stats.hits, stats.misses, stats.breakdown);
            }
        }

        let code = reporter
            .finish(&[], interrupted, &reporter::ReporterSession::new(0))
            .code();
        Ok(code)
    }
}
