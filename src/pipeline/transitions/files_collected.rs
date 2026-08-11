//! Transitions from `FilesCollected` phase: `affected`, `prescan`, `query_without_session`

use super::super::{Pipeline, PipelinePhase};
use crate::types::ExitCode;
use crate::{affected, config, query};

use pyo3::prelude::*;

impl Pipeline {
    // 2. affected: FilesCollected -> FilesCollected
    pub(crate) fn affected(mut self) -> Result<Self, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("affected called outside FilesCollected phase")
        };
        if let Some(base_ref) = self.cfg.filter.affected.as_ref() {
            match affected::filter_affected_with_diagnostics(
                &self.shared.test_files,
                &self.cfg.rootdir,
                base_ref,
            ) {
                Ok((Some(files), diag)) => {
                    if files.is_empty() {
                        // Always show summary for zero-affected (even at Normal)
                        eprintln!(
                            "affected: 0 of {} test files selected [base: {}]",
                            diag.total_tests, diag.base_ref,
                        );
                        if diag.total_changed == 0 {
                            eprintln!("  (no files changed)");
                        } else {
                            eprintln!(
                                "  ({} files changed, {} non-Python ignored)",
                                diag.total_changed, diag.non_python_count,
                            );
                        }
                        return Err(ExitCode::Success);
                    }

                    // -v: summary line
                    if self.cfg.output.verbosity >= config::Verbosity::Detailed {
                        eprintln!(
                            "affected: {} of {} test files selected (direct: {}, declaration: {}, import: {}) [base: {}]",
                            diag.affected_count,
                            diag.total_tests,
                            diag.direct_matches.len(),
                            diag.declaration_matches.len(),
                            diag.import_analysis.iter().filter(|a| a.affected).count(),
                            diag.base_ref,
                        );
                    }

                    // -vv: stage breakdown
                    if self.cfg.output.verbosity >= config::Verbosity::Full {
                        render_full_diagnostics(&diag);
                    }

                    self.shared.test_files = files;
                }
                Ok((None, _diag)) => {
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
    pub(crate) fn prescan(self) -> Result<Self, ExitCode> {
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
                    let fixture_module = file
                        .parent()
                        .map(|dir| dir.join("__fixtures__.py"))
                        .filter(|p| p.exists())
                        .map(|p| crate::prescan::prescan_fixture_module(&p));
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: p.items,
                        has_dynamic_collection: p.has_dynamic_collection,
                        fixture_module,
                    });
                }
                crate::prescan::PrescanResult::NoTests => {
                    tracing::debug!(path = file.as_str(), "prescan: no tests, skipping");
                }
                crate::prescan::PrescanResult::Unavailable => {
                    let fixture_module = file
                        .parent()
                        .map(|dir| dir.join("__fixtures__.py"))
                        .filter(|p| p.exists())
                        .map(|p| crate::prescan::prescan_fixture_module(&p));
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: vec![],
                        has_dynamic_collection: true,
                        fixture_module,
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

        Ok(Self {
            shared,
            phase: PipelinePhase::Prescanned {
                prescan_data,
                module_markers,
            },
        })
    }

    // 7. query_without_session: FilesCollected -> terminal
    pub(crate) fn query_without_session(
        self,
        py: Python<'_>,
        args: &config::QueryArgs,
    ) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("query_without_session called outside FilesCollected phase")
        };

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.shared.test_files,
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

fn render_full_diagnostics(diag: &affected::AffectedDiagnostics) {
    eprintln!();
    eprintln!("Stage 1: Git Diff (base: {})", diag.base_ref);
    eprintln!("  Changed files: {}", diag.total_changed);
    eprintln!();

    eprintln!("Stage 2: Classification");
    eprintln!("  declaration files: {}", diag.declaration_files.len());
    eprintln!(
        "  Python source: {}{}",
        diag.source_files.len(),
        if diag.source_files.is_empty() {
            String::new()
        } else {
            format!(" ({})", diag.source_files.join(", "))
        },
    );
    eprintln!("  Non-Python (ignored): {}", diag.non_python_count);
    eprintln!();

    eprintln!("Stage 3: Direct Matches");
    if diag.direct_matches.is_empty() {
        eprintln!("  (none)");
    } else {
        for m in &diag.direct_matches {
            eprintln!("  \u{2713} {m} (file itself changed)");
        }
    }
    eprintln!();

    eprintln!("Stage 4: Declaration Impact");
    if diag.declaration_matches.is_empty() {
        eprintln!("  (no declaration files changed)");
    } else {
        for m in &diag.declaration_matches {
            eprintln!("  \u{2713} {m}");
        }
    }
    eprintln!();

    eprintln!("Stage 5: Import Graph");
    if diag.import_analysis.is_empty() {
        eprintln!("  (no source files to analyze)");
    } else {
        for entry in &diag.import_analysis {
            if entry.affected {
                eprintln!(
                    "  \u{2713} {} \u{2014} via: {}",
                    entry.test_file,
                    entry.matched_imports.join(", "),
                );
            }
        }
        let unaffected = diag.import_analysis.iter().filter(|a| !a.affected).count();
        if unaffected > 0 {
            eprintln!("  ... and {unaffected} more not affected");
        }
    }
    eprintln!();

    eprintln!(
        "Summary: {} of {} test files affected",
        diag.affected_count, diag.total_tests,
    );
    eprintln!(
        "  Direct: {}, Declaration: {}, Import: {}",
        diag.direct_matches.len(),
        diag.declaration_matches.len(),
        diag.import_analysis.iter().filter(|a| a.affected).count(),
    );
}
