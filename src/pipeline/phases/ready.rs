use pyo3::prelude::*;

use super::super::{execution, Executed, ExecutionResults, Pipeline, Ready};
use crate::cache::TimingCache;
use crate::types::ExitCode;
use crate::{bridge, parallel, reporter};

impl Pipeline<Ready> {
    pub(crate) fn execute(self, py: Python<'_>) -> Result<Pipeline<Executed>, ExitCode> {
        let (
            mut shared,
            Ready {
                test_files,
                conftest_files,
                session,
                clean_items,
                violated_items,
                all_violations,
                suite_lines,
            },
        ) = self.into_parts();

        let total = violated_items.len() + clean_items.len();
        let fn_count = {
            let mut seen = std::collections::HashSet::new();
            for item in clean_items.iter().chain(violated_items.iter()) {
                seen.insert((&item.module_path, &item.fn_name));
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
        let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !shared.cfg.plugins.is_empty() {
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

        let (json_path, junit_path) = match &shared.command {
            crate::config::Command::Run(a) => (a.json.clone(), a.junit_xml.clone()),
            _ => (None, None),
        };

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

        let exec_ctx = execution::ExecutionContext {
            cfg: &shared.cfg,
            cache: &shared.cache,
            session: &session,
            conftest_files: &conftest_files,
            python_bin: &shared.python_bin,
            ast_weight_ms: shared.ast_weight_ms,
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

        Ok(shared.into_pipeline(Executed {
            test_files,
            conftest_files,
            session,
            items: clean_items,
            execution_results: ExecutionResults {
                timings,
                interrupted,
                reporter: rep,
            },
        }))
    }
}
