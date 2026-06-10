use pyo3::prelude::*;

use super::super::helpers;
use super::super::{FilesCollected, Pipeline, Prescanned, SessionReady};
use crate::types::ExitCode;
use crate::{affected, config, query};

impl Pipeline<FilesCollected> {
    pub(crate) fn affected(mut self) -> Result<Pipeline<FilesCollected>, ExitCode> {
        if let Some(base_ref) = self.cfg.affected.as_ref() {
            match affected::filter_affected_test_files(
                &self.state.test_files,
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
                        total = self.state.test_files.len(),
                        base = base_ref.as_str(),
                        "running affected tests only"
                    );
                    self.state.test_files = files;
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

    pub(crate) fn prescan(self) -> Result<Pipeline<Prescanned>, ExitCode> {
        let mut prescan_data = Vec::with_capacity(self.state.test_files.len());
        let mut module_markers = std::collections::HashMap::new();
        let mut ast_weight_sum = 0.0f64;

        for file in &self.state.test_files {
            let result = crate::prescan::prescan_with_ast(file, false);
            match result {
                crate::prescan::PrescanResult::HasTests {
                    items,
                    has_dynamic_collection,
                    module_markers: file_marks,
                    ..
                } => {
                    ast_weight_sum += items.iter().map(|i| i.body_weight_ms).sum::<f64>();
                    prescan_data.push((file.clone(), items, has_dynamic_collection));
                    if !file_marks.is_empty() {
                        module_markers.insert(file.clone(), file_marks);
                    }
                }
                crate::prescan::PrescanResult::NoTests => {
                    tracing::debug!(path = file.as_str(), "prescan: no tests, skipping");
                }
                crate::prescan::PrescanResult::Unavailable => {
                    prescan_data.push((file.clone(), vec![], true));
                }
            }
        }

        let (
            mut shared,
            FilesCollected {
                test_files,
                conftest_files,
            },
        ) = self.into_parts();

        shared.ast_weight_ms = if ast_weight_sum > 0.0 {
            Some(ast_weight_sum)
        } else {
            None
        };

        Ok(shared.into_pipeline(Prescanned {
            test_files,
            conftest_files,
            prescan_data,
            module_markers,
        }))
    }

    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline<SessionReady>, ExitCode> {
        let (session, fixture_violations) =
            helpers::init_session(py, &self.state.conftest_files, &self.cfg, || {
                self.make_error_reporter()
            })?;
        let (
            shared,
            FilesCollected {
                test_files,
                conftest_files,
            },
        ) = self.into_parts();
        Ok(shared.into_pipeline(SessionReady {
            test_files,
            conftest_files,
            session,
            session_violations: fixture_violations,
        }))
    }

    pub(crate) fn query_without_session(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query_without_session only called for Query command");
        };

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.state.test_files,
                &self.state.conftest_files,
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
            &self.state.test_files,
            &self.state.conftest_files,
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
