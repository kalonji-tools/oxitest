use super::super::{Empty, FilesCollected, Pipeline};
use crate::collector;
use crate::types::ExitCode;

impl Pipeline<Empty> {
    pub(crate) fn collect_files(self) -> Result<Pipeline<FilesCollected>, ExitCode> {
        let (test_files, conftest_files) = collector::collect_files(&self.cfg).map_err(|e| {
            eprintln!("error: invalid glob pattern in python_files: {e}");
            ExitCode::UsageError
        })?;
        let (mut shared, _) = self.into_parts();
        shared.test_files = test_files;
        shared.conftest_files = conftest_files;
        Ok(shared.into_pipeline(FilesCollected))
    }
}
