//! Transitions from FilesCollected phase: affected, prescan, query_without_session

use super::super::{Pipeline, PipelinePhase};
use crate::types::ExitCode;
use crate::{affected, config, query};

use pyo3::prelude::*;

impl Pipeline {
    // 2. affected: FilesCollected -> FilesCollected
    pub(crate) fn affected(mut self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("affected called outside FilesCollected phase")
        };
        if let Some(base_ref) = self.cfg.filter.affected.as_ref() {
            match affected::filter_affected_test_files(
                &self.shared.test_files,
                &self.cfg.rootdir,
                base_ref,
            ) {
                Ok(Some(files)) => {
                    if files.is_empty() {
                        println!("no changes detected — nothing to test");
                        return Err(ExitCode::Success);
                    }
                    tracing::info!(
                        affected = files.len(),
                        total = self.shared.test_files.len(),
                        base = base_ref.as_str(),
                        "running affected tests only"
                    );
                    self.shared.test_files = files;
                }
                Ok(None) => {
                    tracing::info!("pyproject.toml changed — running all tests");
                }
                Err(e) => {
                    tracing::warn!("--affected filtering failed ({e}), running all tests");
                }
            }
        }
        Ok(self)
    }

    // 3. prescan: FilesCollected -> Prescanned
    pub(crate) fn prescan(self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("prescan called outside FilesCollected phase")
        };
        use rayon::prelude::*;

        // Phase 1: parallel AST parse — CPU-bound, no shared state.
        let file_results: Vec<_> = self
            .shared
            .test_files
            .par_iter()
            .map(|file| (file.clone(), crate::prescan::prescan_with_ast(file, false)))
            .collect();

        // Phase 2: sequential accumulation — cheap, preserves deterministic order.
        let mut prescan_data = Vec::with_capacity(file_results.len());
        let mut module_markers = std::collections::HashMap::new();
        let mut ast_weight_sum = crate::types::DurationMs::ZERO;

        for (file, result) in file_results {
            match result {
                crate::prescan::PrescanResult::HasTests(p) => {
                    for i in &p.items {
                        ast_weight_sum += i.body_weight;
                    }
                    if !p.module_markers.is_empty() {
                        module_markers.insert(file.clone(), p.module_markers);
                    }
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: p.items,
                        has_dynamic_collection: p.has_dynamic_collection,
                    });
                }
                crate::prescan::PrescanResult::NoTests => {
                    tracing::debug!(path = file.as_str(), "prescan: no tests, skipping");
                }
                crate::prescan::PrescanResult::Unavailable => {
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: vec![],
                        has_dynamic_collection: true,
                    });
                }
            }
        }

        let (mut shared, _) = self.into_parts();

        shared.ast_weight = if ast_weight_sum > crate::types::DurationMs::ZERO {
            Some(ast_weight_sum)
        } else {
            None
        };

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Prescanned {
                prescan_data,
                module_markers,
            },
        })
    }

    // 7. query_without_session: FilesCollected -> terminal
    pub(crate) fn query_without_session(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("query_without_session called outside FilesCollected phase")
        };
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query_without_session only called for Query command");
        };

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.shared.test_files,
                &self.shared.conftest_files,
                None,
                &self.cfg,
                self.use_color,
            ) {
                Ok(()) => return Ok(ExitCode::Success),
                Err(e) => {
                    eprintln!("error: {e}");
                    return Ok(ExitCode::Failure);
                }
            }
        }

        match query::run_query(
            py,
            args,
            &self.shared.test_files,
            &self.shared.conftest_files,
            None,
            &self.cfg,
            self.is_tty,
            self.use_color,
        ) {
            Ok(output) => {
                if !output.is_empty() {
                    print!("{output}");
                }
                Ok(ExitCode::Success)
            }
            Err(msg) => {
                eprintln!("error: {msg}");
                Ok(ExitCode::UsageError)
            }
        }
    }
}
