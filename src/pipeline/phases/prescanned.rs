use super::super::{MetadataFiltered, Pipeline, Prescanned};
use crate::cache::OutcomeCache;
use crate::collector;
use crate::types::ExitCode;
use crate::{config, filter};

impl Pipeline<Prescanned> {
    pub(crate) fn filter_metadata(self) -> Result<Pipeline<MetadataFiltered>, ExitCode> {
        let has_node_ids = !self.cfg.node_ids.is_empty();
        let has_expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.is_some(),
            config::Command::Debug(a) => a.filter.expression.is_some(),
            _ => false,
        };
        let has_failed_filter = matches!(self.cfg.failed, Some(config::FailedMode::Only));
        let has_affected = self.cfg.affected.is_some();
        let is_filtered = has_node_ids || has_expression || has_failed_filter || has_affected;

        if !is_filtered {
            let all_modules: Vec<camino::Utf8PathBuf> = self
                .state
                .prescan_data
                .iter()
                .map(|(path, _, _)| path.clone())
                .collect();
            let (
                shared,
                Prescanned {
                    test_files,
                    conftest_files,
                    ..
                },
            ) = self.into_parts();
            return Ok(shared.into_pipeline(MetadataFiltered {
                test_files,
                conftest_files,
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
        let node_ids: Vec<String> = self.cfg.node_ids.iter().map(|n| n.to_string()).collect();

        let mut modules_to_import = Vec::new();
        for (path, items, has_dynamic) in &self.state.prescan_data {
            if *has_dynamic {
                modules_to_import.push(path.clone());
                continue;
            }
            if self.file_passes_all_filters(
                path,
                items,
                has_node_ids,
                has_failed_filter,
                &node_ids,
                expression.as_deref(),
                &failed_ids,
            ) {
                modules_to_import.push(path.clone());
            }
        }

        let filtered_conftests =
            collector::conftests_for_modules(&self.state.conftest_files, &modules_to_import);

        tracing::info!(
            total_files = self.state.prescan_data.len(),
            matched_files = modules_to_import.len(),
            conftests = filtered_conftests.len(),
            "lazy collection: filtered by prescan metadata"
        );

        let (shared, Prescanned { test_files, .. }) = self.into_parts();
        Ok(shared.into_pipeline(MetadataFiltered {
            test_files,
            conftest_files: filtered_conftests,
            modules_to_import,
            is_filtered: true,
        }))
    }

    /// Returns true if the file should be imported given the active filters.
    ///
    /// Uses early-return false for each failing filter (AND semantics, short-circuit).
    #[allow(clippy::too_many_arguments)]
    fn file_passes_all_filters(
        &self,
        path: &camino::Utf8Path,
        items: &[crate::prescan::PrescanItem],
        has_node_ids: bool,
        has_failed_filter: bool,
        node_ids: &[String],
        expression: Option<&str>,
        failed_ids: &std::collections::HashSet<String>,
    ) -> bool {
        if has_node_ids {
            // Only apply node ID filtering to files that came from node ID args,
            // not bare path args. Files from bare paths pass through unconditionally.
            let is_node_id_source = self.cfg.node_id_source_files.is_empty()
                || self.cfg.node_id_source_files.contains(path);
            if is_node_id_source && !filter::file_matches_node_ids(items, path.as_str(), node_ids) {
                return false;
            }
        }

        if let Some(expr) = expression {
            let module_marks = self.state.module_markers.get(path).map(|v| v.as_slice());
            if !filter::file_matches_expression(items, path.as_str(), expr, module_marks) {
                return false;
            }
        }

        if has_failed_filter
            && !failed_ids.is_empty()
            && !filter::file_matches_last_failed(items, path.as_str(), failed_ids)
        {
            return false;
        }

        true
    }
}
