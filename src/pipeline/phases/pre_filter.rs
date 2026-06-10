use pyo3::prelude::*;

use super::super::{Pipeline, PipelineShared, PreFilter, Ready};
use crate::cache::OutcomeCache;
use crate::types::ExitCode;
use crate::{config, filter, query, reporter, types};

fn apply_query_dsl_filter(
    items: Vec<std::sync::Arc<types::TestItem>>,
    expression: &str,
    shared: &PipelineShared,
) -> Result<Vec<std::sync::Arc<types::TestItem>>, ExitCode> {
    let tokens = match query::compile::lex(expression) {
        Ok(t) => t,
        Err(e) => {
            return Err(shared
                .make_error_reporter()
                .finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -E expression: {e}"
                    ))],
                    false,
                    &reporter::ReporterSession::new(0),
                )
                .code());
        }
    };
    let parsed = match query::compile::parse(tokens) {
        Ok(p) => p,
        Err(e) => {
            return Err(shared
                .make_error_reporter()
                .finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -E expression: {e}"
                    ))],
                    false,
                    &reporter::ReporterSession::new(0),
                )
                .code());
        }
    };
    if let Err(e) = query::eval::validate_predicates(&parsed, &query::resource::ResourceKind::Tests)
    {
        return Err(shared
            .make_error_reporter()
            .finish(
                &[types::CollectError::PyError(format!(
                    "invalid -E expression: {e}"
                ))],
                false,
                &reporter::ReporterSession::new(0),
            )
            .code());
    }
    Ok(items
        .into_iter()
        .filter(|item| {
            let entry = item_to_query_entry(item);
            query::eval::eval(&parsed, &entry)
        })
        .collect())
}

fn item_to_query_entry(item: &types::TestItem) -> query::resource::QueryEntry {
    let mut fields = std::collections::HashMap::new();
    fields.insert("name".to_string(), item.node_id.to_string());
    fields.insert("source".to_string(), item.module_path.to_string());
    fields.insert("mark".to_string(), item.markers.join(","));
    fields.insert("async".to_string(), item.is_async.to_string());
    query::resource::QueryEntry { fields }
}

impl Pipeline<PreFilter> {
    pub(crate) fn filter(self, _py: Python<'_>) -> Result<Pipeline<Ready>, ExitCode> {
        let (
            shared,
            PreFilter {
                test_files,
                conftest_files,
                session,
                clean_items: items,
                violated_items,
                all_violations,
                suite_lines,
            },
        ) = self.into_parts();

        // Node ID filter (positional node IDs).
        let items = filter::filter_by_node_ids(
            items,
            &shared.cfg.node_ids,
            &shared.cfg.node_id_source_files,
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
        let items = match shared.cfg.failed {
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

        Ok(shared.into_pipeline(Ready {
            test_files,
            conftest_files,
            session,
            clean_items: items,
            violated_items,
            all_violations,
            suite_lines,
        }))
    }
}
