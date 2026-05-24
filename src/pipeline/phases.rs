//! Pipeline phase implementations.
//!
//! Each phase is a unit struct implementing [`PipelinePhase`]. Phases read
//! from and write to [`PipelineContext`], delegating to helper functions
//! in [`super::helpers`] for the actual work.

use pyo3::prelude::*;

use super::helpers;
use super::{PhaseOutcome, PipelineContext, PipelinePhase};
use crate::{affected, bridge, collector, types};

// ─── FileCollectionPhase ─────────────────────────────────────────────────────

pub(crate) struct FileCollectionPhase;

impl PipelinePhase for FileCollectionPhase {
    fn name(&self) -> &'static str {
        "file-collection"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, _py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, i32> {
        let (test_files, conftest_files) = collector::collect_files(&ctx.cfg);
        ctx.test_files = test_files;
        ctx.conftest_files = conftest_files;
        Ok(PhaseOutcome::Continue)
    }
}

// ─── SessionPhase ─────────────────────────────────────────────────────────────

pub(crate) struct SessionPhase;

impl PipelinePhase for SessionPhase {
    fn name(&self) -> &'static str {
        "session"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, i32> {
        let session = match bridge::FixtureSession::new(py, &ctx.conftest_files) {
            Ok(s) => s,
            Err(e) => {
                let err = types::CollectError::PyError(format!(
                    "Failed to load conftest fixtures: {}",
                    e
                ));
                return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                    &[err],
                    &|| ctx.make_error_reporter(),
                )));
            }
        };

        if !ctx.cfg.plugins.is_empty() {
            if let Err(e) = session.load_plugins(py, &ctx.cfg.plugins, &ctx.cfg.plugin_settings) {
                let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
                return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                    &[err],
                    &|| ctx.make_error_reporter(),
                )));
            }
        }

        if let Err(e) = session.init_async_backend(py, &ctx.cfg.async_backend) {
            let err = types::CollectError::PyError(format!("Async backend init failed: {}", e));
            return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                &[err],
                &|| ctx.make_error_reporter(),
            )));
        }

        ctx.session = Some(session);
        Ok(PhaseOutcome::Continue)
    }
}

// ─── FixturesPhase ────────────────────────────────────────────────────────────

pub(crate) struct FixturesPhase;

impl PipelinePhase for FixturesPhase {
    fn name(&self) -> &'static str {
        "fixtures"
    }

    fn should_run(&self, ctx: &PipelineContext) -> bool {
        ctx.cli.fixtures
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, i32> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");

        let verbosity = if ctx.cli.quiet {
            0
        } else if ctx.cli.verbose {
            2
        } else {
            1
        };

        match session.list_fixtures(py, verbosity, ctx.cli.keyword.as_deref(), ctx.use_color) {
            Ok(output) => {
                if !output.is_empty() {
                    println!("{output}");
                }
            }
            Err(e) => {
                eprintln!("Error listing fixtures: {e}");
                return Ok(PhaseOutcome::EarlyExit(1));
            }
        }
        Ok(PhaseOutcome::EarlyExit(0))
    }
}

// ─── AffectedPhase ───────────────────────────────────────────────────────────

pub(crate) struct AffectedPhase;

impl PipelinePhase for AffectedPhase {
    fn name(&self) -> &'static str {
        "affected"
    }

    fn should_run(&self, ctx: &PipelineContext) -> bool {
        ctx.cfg.affected.is_some()
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, i32> {
        let base_ref = ctx.cfg.affected.as_ref().expect("checked in should_run");
        match affected::filter_affected_test_files(py, &ctx.test_files, &ctx.cfg.rootdir, base_ref)
        {
            Ok(Some(files)) => {
                if files.is_empty() {
                    println!("no changes detected — nothing to test");
                    return Ok(PhaseOutcome::EarlyExit(0));
                }
                tracing::info!(
                    affected = files.len(),
                    total = ctx.test_files.len(),
                    base = base_ref.as_str(),
                    "running affected tests only"
                );
                ctx.test_files = files;
            }
            Ok(None) => {
                tracing::info!("pyproject.toml changed — running all tests");
            }
            Err(e) => {
                let err = types::CollectError::PyError(e.to_string());
                return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                    &[err],
                    &|| ctx.make_error_reporter(),
                )));
            }
        }
        Ok(PhaseOutcome::Continue)
    }
}
