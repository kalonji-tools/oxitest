use super::super::{MetadataFiltered, Pipeline, Prescanned};
use crate::cache::OutcomeCache;
use crate::collector;
use crate::types::ExitCode;
use crate::{config, filter};

/// Pre-computed filter state used by [`file_passes_all_filters`].
struct FilterPredicates<'a> {
    node_ids: &'a [String],
    expression: Option<&'a str>,
    failed_ids: &'a std::collections::HashSet<String>,
    node_id_source_files: &'a std::collections::HashSet<camino::Utf8PathBuf>,
    has_node_ids: bool,
    has_failed_filter: bool,
}

impl Pipeline<Prescanned> {
    pub(crate) fn filter_metadata(self) -> Result<Pipeline<MetadataFiltered>, ExitCode> {
        let has_node_ids = !self.cfg.filter.node_ids.is_empty();
        let has_expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.is_some(),
            config::Command::Debug(a) => a.filter.expression.is_some(),
            _ => false,
        };
        let has_failed_filter = matches!(self.cfg.filter.failed, Some(config::FailedMode::Only));
        let has_affected = self.cfg.filter.affected.is_some();
        let is_filtered = has_node_ids || has_expression || has_failed_filter || has_affected;

        if !is_filtered {
            let all_modules: Vec<camino::Utf8PathBuf> = self
                .state
                .prescan_data
                .iter()
                .map(|m| m.path.clone())
                .collect();
            let (shared, _) = self.into_parts();
            return Ok(shared.into_pipeline(MetadataFiltered {
                modules_to_import: all_modules,
                is_filtered: false,
            }));
        }

        // Prepare filter inputs once before the loop.
        let expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };
        let failed_ids = if has_failed_filter {
            self.cache.last_failed_ids()
        } else {
            std::collections::HashSet::new()
        };
        let node_ids: Vec<String> = self
            .cfg
            .filter
            .node_ids
            .iter()
            .map(|n| n.to_string())
            .collect();
        let source_files = self.cfg.filter.source_files();

        let preds = FilterPredicates {
            node_ids: &node_ids,
            expression: expression.as_deref(),
            failed_ids: &failed_ids,
            node_id_source_files: &source_files,
            has_node_ids,
            has_failed_filter,
        };

        use rayon::prelude::*;

        let modules_to_import: Vec<camino::Utf8PathBuf> = self
            .state
            .prescan_data
            .par_iter()
            .filter_map(|m| {
                if m.has_dynamic_collection
                    || self.file_passes_all_filters(&m.path, &m.items, &preds)
                {
                    Some(m.path.clone())
                } else {
                    None
                }
            })
            .collect();

        let filtered_conftests =
            collector::conftests_for_modules(&self.shared.conftest_files, &modules_to_import);

        tracing::info!(
            total_files = self.state.prescan_data.len(),
            matched_files = modules_to_import.len(),
            conftests = filtered_conftests.len(),
            "lazy collection: filtered by prescan metadata"
        );

        let (mut shared, _) = self.into_parts();
        shared.conftest_files = filtered_conftests;
        Ok(shared.into_pipeline(MetadataFiltered {
            modules_to_import,
            is_filtered: true,
        }))
    }

    /// Returns true if the file should be imported given the active filters.
    ///
    /// Uses early-return false for each failing filter (AND semantics, short-circuit).
    fn file_passes_all_filters(
        &self,
        path: &camino::Utf8Path,
        items: &[crate::prescan::PrescanItem],
        preds: &FilterPredicates<'_>,
    ) -> bool {
        if preds.has_node_ids {
            // Only apply node ID filtering to files that came from node ID args,
            // not bare path args. Files from bare paths pass through unconditionally.
            let is_node_id_source =
                preds.node_id_source_files.is_empty() || preds.node_id_source_files.contains(path);
            if is_node_id_source
                && !filter::file_matches_node_ids(items, path.as_str(), preds.node_ids)
            {
                return false;
            }
        }

        if let Some(expr) = preds.expression {
            let module_marks = self.state.module_markers.get(path).map(|v| v.as_slice());
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
}
