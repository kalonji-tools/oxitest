//! Transition: `Prescanned` -> `MetadataFiltered`

use super::super::{Pipeline, PipelinePhase};
use super::FilterPredicates;
use super::file_passes_all_filters;
use crate::config;
use crate::types::ExitCode;

impl Pipeline {
    // 4. filter_metadata: Prescanned -> MetadataFiltered
    pub(crate) fn filter_metadata(self) -> Result<Self, ExitCode> {
        let PipelinePhase::Prescanned {
            ref prescan_data,
            ref module_markers,
        } = self.phase
        else {
            unreachable!("filter_metadata called outside Prescanned phase")
        };

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
            let all_modules: Vec<camino::Utf8PathBuf> =
                prescan_data.iter().map(|m| m.path.clone()).collect();
            let (shared, _) = self.into_parts();
            return Ok(Self {
                shared,
                phase: PipelinePhase::MetadataFiltered {
                    modules_to_import: all_modules,
                },
            });
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

        let modules_to_import: Vec<camino::Utf8PathBuf> = prescan_data
            .par_iter()
            .filter_map(|m| {
                if m.has_dynamic_collection
                    || file_passes_all_filters(&m.path, &m.items, &preds, module_markers)
                {
                    Some(m.path.clone())
                } else {
                    None
                }
            })
            .collect();

        tracing::info!(
            total_files = prescan_data.len(),
            matched_files = modules_to_import.len(),
            "lazy collection: filtered by prescan metadata"
        );

        let (shared, _) = self.into_parts();
        Ok(Self {
            shared,
            phase: PipelinePhase::MetadataFiltered { modules_to_import },
        })
    }
}
