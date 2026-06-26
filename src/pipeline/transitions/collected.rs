//! Transitions from Collected phase: validate, strict_or_skip

use pyo3::prelude::*;

use super::super::helpers;
use super::super::{Pipeline, PipelinePhase};
use super::apply_query_dsl_filter;
use crate::types::ExitCode;
use crate::{bridge, config, filter, types};

impl Pipeline {
    // 9. validate: Collected -> Collected
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Collected {
            ref session,
            ref items,
            ..
        } = self.phase
        else {
            unreachable!("validate called outside Collected phase")
        };

        let errors = bridge::validate_fixture_names(py, session, items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(self);
        }

        let registered = bridge::registered_fixture_names(py, session).unwrap_or_default();
        let full_message = super::super::format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Err(helpers::early_exit_with_error(&[err], &|| {
            self.make_error_reporter()
        }))
    }

    // 10. strict_or_skip: Collected -> Ready
    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Collected {
            session,
            items,
            raw_violations,
        } = self.phase
        else {
            unreachable!("strict_or_skip called outside Collected phase")
        };
        let shared = self.shared;

        let result =
            helpers::apply_strict_mode(&shared.cfg, items, raw_violations, shared.use_color)?;

        // ── Item-level filtering (formerly PreFilter::filter) ────────────

        // Node ID filter (positional node IDs).
        let source_files = shared.cfg.filter.source_files();
        let items = filter::filter_by_node_ids(
            result.clean_items,
            &shared.cfg.filter.node_ids,
            &source_files,
        );

        let expression = match &shared.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };

        // Query DSL filter (-E).
        let items = if let Some(expr_str) = expression.as_deref() {
            apply_query_dsl_filter(items, expr_str, &shared)?
        } else {
            items
        };

        // Last-failed filter (--failed=only / --failed=first).
        let total_before_failed_filter = items.len();
        let items = match shared.cfg.filter.failed {
            Some(config::FailedMode::Only) => {
                let failed_ids = shared.cache.last_failed_ids();
                if failed_ids.is_empty() {
                    tracing::info!(
                        count = items.len(),
                        "no recorded failures — running all tests"
                    );
                    items
                } else {
                    let filtered = filter::filter_last_failed(items, &failed_ids);
                    tracing::info!(
                        running = filtered.len(),
                        total = total_before_failed_filter,
                        "running tests in --failed=only mode"
                    );
                    filtered
                }
            }
            Some(config::FailedMode::First) => {
                let failed_ids = shared.cache.last_failed_ids();
                filter::sort_failed_first(items, &failed_ids)
            }
            None => items,
        };

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Ready {
                session,
                clean_items: items,
                violated_items: result.violated_items,
                all_violations: result.all_violations,
                suite_lines: result.suite_lines,
            },
        })
    }
}
