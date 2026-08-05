//! Transitions: session (FilesCollected/MetadataFiltered -> SessionReady),
//! collect (SessionReady -> Collected), query (SessionReady -> terminal)

use pyo3::prelude::*;

use super::super::collection;
use super::super::helpers;
use super::super::{Pipeline, PipelinePhase};
use crate::types::ExitCode;
use crate::{bridge, collector, config, query};

impl Pipeline {
    // 5a. session from FilesCollected: FilesCollected -> SessionReady
    // 5b. session from MetadataFiltered: MetadataFiltered -> SessionReady
    pub(crate) fn session(self, py: Python<'_>) -> Result<Self, ExitCode> {
        // The phase is consumed *before* the session is built, not after. The
        // previous shape called `init_session` once per arm and then asked
        // `into_parts()` for a variant it had already matched, which needed an
        // `unreachable!()` that had nothing to do with the pipeline typestate
        // this module is excepted for (ADR-0011, E1).
        let (mut shared, phase) = self.into_parts();
        let modules_to_import = match phase {
            PipelinePhase::FilesCollected => None,
            PipelinePhase::MetadataFiltered { modules_to_import } => Some(modules_to_import),
            _ => unreachable!("session called outside FilesCollected or MetadataFiltered phase"),
        };

        let (session, fixture_violations) =
            helpers::init_session(py, &shared.conftest_files, &shared.cfg, || {
                shared.make_error_reporter()
            })?;

        // Replace test_files with the filtered modules to import.
        if let Some(modules) = modules_to_import {
            shared.test_files = modules;
        }

        Ok(Self {
            shared,
            phase: PipelinePhase::SessionReady {
                session,
                session_violations: fixture_violations,
            },
        })
    }

    // 6. collect: SessionReady -> Collected
    pub(crate) fn collect(self, py: Python<'_>) -> Result<Self, ExitCode> {
        let PipelinePhase::SessionReady {
            session,
            session_violations,
        } = self.phase
        else {
            unreachable!("collect called outside SessionReady phase")
        };
        let mut shared = self.shared;
        shared.cache.invalidate_modules();
        let collection::CollectionOutput {
            mut items,
            mut errors,
            raw_violations,
            profile,
            fixture_modules,
        } = collection::collect_items(
            py,
            &shared.test_files,
            &shared.cfg,
            &session,
            &mut shared.cache,
        );
        shared.fixture_modules = fixture_modules;

        // Collect doctest items when doctest collection is opted in — either
        // via `--doctest-modules` on the CLI or a `[tool.oxitest.doctest]`
        // table in pyproject.toml.
        if shared.cfg.doctest_enabled() {
            // Reported, not swallowed: an empty file list is indistinguishable
            // from "this project has no doctests", so degrading here would
            // silently drop the whole doctest suite under a green gate.
            let doctest_files = match collector::collect_doctest_files(&shared.cfg) {
                Ok(files) => files,
                Err(err) => {
                    errors.push(crate::types::CollectError::ImportError {
                        path: shared.cfg.rootdir.clone(),
                        message: format!(
                            "doctest collection could not compile its `*.py` glob: {err}"
                        ),
                    });
                    Vec::new()
                }
            };
            let doctest_items = collection::collect_doctest_items(&doctest_files);
            tracing::debug!(
                doctest_files = doctest_files.len(),
                doctest_items = doctest_items.len(),
                "collected doctest items"
            );
            items.extend(doctest_items);

            // Run the doctest coverage rule. Error-severity diagnostics are
            // promoted to collection errors so `strict = "abort"` exits
            // non-zero via the existing early-exit path. Warning/Notice pass
            // through as pending diagnostics for the reporter.
            let coverage_diags =
                collection::collect_coverage_diagnostics(&doctest_files, &shared.cfg);
            if !coverage_diags.is_empty() {
                tracing::debug!(
                    coverage_diagnostics = coverage_diags.len(),
                    "doctest coverage diagnostics emitted"
                );
                let (coverage_errors, coverage_pending) =
                    collection::split_coverage_diagnostics(coverage_diags);
                shared.pending_diagnostics.extend(coverage_pending);
                errors.extend(coverage_errors);
            }
        }

        if !errors.is_empty() {
            return Err(helpers::early_exit_with_error(&errors, &|| {
                shared.make_error_reporter()
            }));
        }

        // Merge session-phase violations with collection violations.
        let mut merged_violations = session_violations;
        merged_violations.extend(raw_violations);

        // Detect unused fixtures when strict mode is enabled.
        if shared.cfg.markers.strict.is_some()
            && !shared.cfg.filter.has_explicit_paths
            && let Ok(unused) = bridge::find_unused_fixtures(py, &session, &items)
        {
            merged_violations.extend(unused);
        }

        // Apply node ID filter early.
        let source_files = shared.cfg.filter.source_files();
        let items =
            crate::filter::filter_by_node_ids(items, &shared.cfg.filter.node_ids, &source_files);

        if let Some(ref prof) = profile {
            eprintln!("{}", collection::format_collection_profile(prof));
        }

        Ok(Self {
            shared,
            phase: PipelinePhase::Collected {
                session,
                items,
                raw_violations: merged_violations,
            },
        })
    }

    // 8. query: SessionReady -> terminal
    pub(crate) fn query(
        self,
        py: Python<'_>,
        args: &config::QueryArgs,
    ) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::SessionReady { ref session, .. } = self.phase else {
            unreachable!("query called outside SessionReady phase")
        };

        if args.tree {
            if args.resource != query::resource::ResourceKind::Fixtures {
                eprintln!("error: --tree is only valid for the 'fixtures' resource");
                return Ok(ExitCode::UsageError);
            }
            let verbosity = self.cfg.output.verbosity as i32;
            match query::bridge::tree_fixtures(session, py, verbosity, None, self.use_color) {
                Ok(output) => {
                    if output.starts_with("error:") {
                        eprintln!("{output}");
                        return Ok(ExitCode::Failure);
                    }
                    if !output.is_empty() {
                        println!("{output}");
                    }
                }
                Err(e) => {
                    eprintln!("Error rendering fixture tree: {e}");
                    return Ok(ExitCode::Failure);
                }
            }
            return Ok(ExitCode::Success);
        }

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.shared.test_files,
                &self.shared.conftest_files,
                Some(session),
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
            Some(session),
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
