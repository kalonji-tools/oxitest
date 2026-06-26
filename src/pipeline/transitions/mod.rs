//! Pipeline phase transitions.
//!
//! Each transition method consumes `Pipeline` and produces
//! `Result<Pipeline, ExitCode>`. Each method guards on the expected
//! `PipelinePhase` variant with a `let ... else { unreachable!(...) }`.

mod collected;
mod empty;
mod executed;
mod files_collected;
mod prescanned;
mod ready;
mod session_ready;

use super::{PipelineShared, reporter, types};
use crate::{filter, query};

// ─── Helper functions (shared across phases) ──────────────────────────────────

pub(super) fn format_dsl_error(expression: &str, error: query::ast::DslError) -> String {
    use miette::{GraphicalReportHandler, GraphicalTheme};

    let report = miette::Report::new(error).with_source_code(expression.to_string());
    let mut buf = String::new();
    let handler = GraphicalReportHandler::new_themed(GraphicalTheme::unicode_nocolor());
    let _ = handler.render_report(&mut buf, report.as_ref());
    buf
}

pub(super) fn dsl_error_exit(
    expression: &str,
    error: query::ast::DslError,
    shared: &PipelineShared,
) -> crate::types::ExitCode {
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

pub(super) fn apply_query_dsl_filter(
    items: Vec<std::sync::Arc<types::TestItem>>,
    expression: &str,
    shared: &PipelineShared,
) -> Result<Vec<std::sync::Arc<types::TestItem>>, crate::types::ExitCode> {
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

pub(super) fn item_to_query_entry(item: &types::TestItem) -> query::resource::QueryEntry {
    let mut fields = std::collections::HashMap::new();
    fields.insert("name".to_string(), item.node_id.to_string());
    fields.insert("source".to_string(), item.module_path().to_string());
    fields.insert("mark".to_string(), item.markers.join(","));
    fields.insert("async".to_string(), item.is_async.to_string());
    query::resource::QueryEntry { fields }
}

/// Pre-computed filter state used by `file_passes_all_filters`.
pub(super) struct FilterPredicates<'a> {
    pub(super) node_ids: &'a [String],
    pub(super) expression: Option<&'a str>,
    pub(super) failed_ids: &'a std::collections::HashSet<String>,
    pub(super) node_id_source_files: &'a std::collections::HashSet<camino::Utf8PathBuf>,
    pub(super) has_node_ids: bool,
    pub(super) has_failed_filter: bool,
}

/// Returns true if the file should be imported given the active filters.
///
/// Uses early-return false for each failing filter (AND semantics, short-circuit).
pub(super) fn file_passes_all_filters(
    path: &camino::Utf8Path,
    items: &[crate::prescan::PrescanItem],
    preds: &FilterPredicates<'_>,
    module_markers: &std::collections::HashMap<camino::Utf8PathBuf, Vec<String>>,
) -> bool {
    if preds.has_node_ids {
        // Only apply node ID filtering to files that came from node ID args,
        // not bare path args. Files from bare paths pass through unconditionally.
        let is_node_id_source =
            preds.node_id_source_files.is_empty() || preds.node_id_source_files.contains(path);
        if is_node_id_source && !filter::file_matches_node_ids(items, path.as_str(), preds.node_ids)
        {
            return false;
        }
    }

    if let Some(expr) = preds.expression {
        let module_marks = module_markers.get(path).map(|v| v.as_slice());
        if !filter::file_matches_expression(items, path.as_str(), expr, module_marks) {
            return false;
        }
    }

    if preds.has_failed_filter
        && !preds.failed_ids.is_empty()
        && !filter::file_matches_last_failed(items, path.as_str(), preds.failed_ids)
    {
        return false;
    }

    true
}
