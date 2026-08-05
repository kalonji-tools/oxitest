//! Transition: Ready -> Executed

use pyo3::prelude::*;

use super::super::execution::{self, ExecutionContext};
use super::super::{ExecutionResults, Pipeline, PipelinePhase};
use crate::types::ExitCode;
use crate::{bridge, parallel, reporter};

impl Pipeline {
    // 11. execute: Ready -> Executed
    pub(crate) fn execute(self, py: Python<'_>) -> Result<Self, ExitCode> {
        let PipelinePhase::Ready {
            session,
            clean_items,
            violated_items,
            all_violations,
            suite_lines,
        } = self.phase
        else {
            unreachable!("execute called outside Ready phase")
        };
        let mut shared = self.shared;

        let total = violated_items.len() + clean_items.len();
        let fn_count = {
            let mut seen = std::collections::HashSet::new();
            for item in clean_items.iter().chain(violated_items.iter()) {
                seen.insert((item.module_path(), &item.fn_name));
            }
            seen.len()
        };
        let async_count = clean_items.iter().filter(|i| i.is_async).count();
        let max_name_width = clean_items
            .iter()
            .chain(violated_items.iter())
            .map(|i| i.fn_name.len())
            .max()
            .unwrap_or(30);
        shared.cache.invalidate(&clean_items);

        // Fetch plugin reporters from Python registry.
        let plugin_reporters: Vec<Box<dyn reporter::Reporter>> =
            if !shared.cfg.features.plugins.is_empty() {
                bridge::get_plugin_reporters(py, &session)
                    .unwrap_or_default()
                    .into_iter()
                    .map(|obj| {
                        Box::new(reporter::plugin::PyPluginReporter::new(obj))
                            as Box<dyn reporter::Reporter>
                    })
                    .collect()
            } else {
                vec![]
            };

        let (json_path, junit_path) = (shared.json_path(), shared.junit_xml_path());

        let mut rep = reporter::make_reporter(
            shared
                .base
                .clone()
                .total(total)
                .fn_count(fn_count)
                .async_count(async_count)
                .name_width(max_name_width)
                .strict_suite_lines(suite_lines)
                .build(),
            shared.is_tty,
            json_path,
            junit_path,
            plugin_reporters,
        );

        // Drain diagnostics emitted during collection (conftest loading, etc.)
        let collection_diags = bridge::drain_session_diagnostics(py, &session);
        if !collection_diags.is_empty() {
            rep.record_diagnostics(collection_diags);
        }

        // Drain pipeline-side pending diagnostics (e.g. doctest coverage from
        // the `collect` transition) into the reporter.
        let pending = std::mem::take(&mut shared.pending_diagnostics);
        if !pending.is_empty() {
            rep.record_diagnostics(pending);
        }

        // Serialized here, once, because here is where a `Result` already flows
        // (ADR-0011). Everything downstream carries bytes and cannot fail at it.
        let payloads = match parallel::WorkerPayloads::new(
            &shared.conftest_files,
            &shared.fixture_modules,
            &shared.cfg.features.plugins,
            &shared.cfg.features.plugin_settings,
        ) {
            Ok(payloads) => payloads,
            // Reported as a *collect error*, not as a diagnostic.
            // `compute_exit_code` never reads diagnostics, so a diagnostic here
            // would print an error and still exit 0 — a run that executed zero
            // tests reporting success, which is the exact failure ADR-0011
            // Rule 0 calls a silent default.
            Err(err) => {
                let errors = [crate::types::CollectError::PyError(format!(
                    "could not serialize the fixture and plugin data every worker needs: {err}"
                ))];
                return Err(rep
                    .finish(&errors, false, &reporter::ReporterSession::new(0))
                    .code());
            }
        };

        let exec_ctx = ExecutionContext {
            cfg: &shared.cfg,
            cache: &shared.cache,
            session: &session,
            fixture_modules: &shared.fixture_modules,
            payloads: &payloads,
            python_bin: &shared.python_bin,
            ast_weight: shared.ast_weight,
        };

        let parallel::PhaseResult {
            interrupted,
            timings,
        } = execution::execute(
            py,
            &clean_items,
            violated_items,
            all_violations,
            &exec_ctx,
            rep.as_mut(),
        );

        Ok(Self {
            shared,
            phase: PipelinePhase::Executed {
                session,
                items: clean_items,
                execution_results: ExecutionResults {
                    timings,
                    interrupted,
                    reporter: rep,
                },
            },
        })
    }
}
