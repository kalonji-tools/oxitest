//! Pipeline phase implementations.
//!
//! Each phase is a unit struct implementing [`PipelinePhase`]. Phases read
//! from and write to [`PipelineContext`], delegating to helper functions
//! in [`super::helpers`] for the actual work.

use pyo3::prelude::*;

use super::helpers;
use super::{PhaseOutcome, PipelineContext, PipelinePhase};
use crate::{affected, collector, types};

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
