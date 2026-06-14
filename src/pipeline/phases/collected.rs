use pyo3::prelude::*;

use super::super::{helpers, Collected, Pipeline, PipelineShared, Ready};
use crate::cache::OutcomeCache;
use crate::types::ExitCode;
use crate::{bridge, config, filter, query, reporter, types};

fn format_dsl_error(expression: &str, error: query::ast::DslError) -> String {
    use miette::{GraphicalReportHandler, GraphicalTheme};

    let report = miette::Report::new(error).with_source_code(expression.to_string());
    let mut buf = String::new();
    let handler = GraphicalReportHandler::new_themed(GraphicalTheme::unicode_nocolor());
    let _ = handler.render_report(&mut buf, report.as_ref());
    buf
}

fn dsl_error_exit(
    expression: &str,
    error: query::ast::DslError,
    shared: &PipelineShared,
) -> ExitCode {
    let msg = format_dsl_error(expression, error);
    shared
        .make_error_reporter()
        .finish(
            &[types::CollectError::PyError(msg)],
            false,
            &reporter::ReporterSession::new(0),
        )
        .code()
}

fn apply_query_dsl_filter(
    items: Vec<std::sync::Arc<types::TestItem>>,
    expression: &str,
    shared: &PipelineShared,
) -> Result<Vec<std::sync::Arc<types::TestItem>>, ExitCode> {
    let tokens = match query::compile::lex(expression) {
        Ok(t) => t,
        Err(e) => return Err(dsl_error_exit(expression, e, shared)),
    };
    let parsed = match query::compile::parse(tokens) {
        Ok(p) => p,
        Err(e) => return Err(dsl_error_exit(expression, e, shared)),
    };
    if let Err(e) = query::eval::validate_predicates(&parsed, &query::resource::ResourceKind::Tests)
    {
        return Err(dsl_error_exit(expression, e, shared));
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

impl Pipeline<Collected> {
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        let session = &self.state.session;
        let errors = bridge::validate_fixture_names(py, session, &self.state.items)
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

    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Pipeline<Ready>, ExitCode> {
        let (
            shared,
            Collected {
                session,
                items,
                raw_violations,
            },
        ) = self.into_parts();

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

        Ok(shared.into_pipeline(Ready {
            session,
            clean_items: items,
            violated_items: result.violated_items,
            all_violations: result.all_violations,
            suite_lines: result.suite_lines,
        }))
    }
}
