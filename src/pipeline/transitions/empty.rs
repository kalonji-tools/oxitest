//! Transition: `Empty` -> `FilesCollected`

use super::super::{Pipeline, PipelinePhase};
use crate::collector;
use crate::types::ExitCode;

impl Pipeline {
    // 1. collect_files: Empty -> FilesCollected
    pub(crate) fn collect_files(self) -> Result<Self, ExitCode> {
        let PipelinePhase::Empty = self.phase else {
            unreachable!("collect_files called outside Empty phase")
        };
        let (test_files, conftest_files) = collector::collect_files(&self.cfg).map_err(|e| {
            eprintln!("error: invalid glob pattern in python_files: {e}");
            ExitCode::UsageError
        })?;
        let (mut shared, _) = self.into_parts();
        shared.test_files = test_files;
        shared.conftest_files = conftest_files;
        Ok(Self {
            shared,
            phase: PipelinePhase::FilesCollected,
        })
    }
}
