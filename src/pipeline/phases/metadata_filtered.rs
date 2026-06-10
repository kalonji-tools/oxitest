use pyo3::prelude::*;

use super::super::{helpers, MetadataFiltered, Pipeline, SessionReady};
use crate::types::ExitCode;

impl Pipeline<MetadataFiltered> {
    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline<SessionReady>, ExitCode> {
        let (session, fixture_violations) =
            helpers::init_session(py, &self.state.conftest_files, &self.cfg, || {
                self.make_error_reporter()
            })?;
        let (
            shared,
            MetadataFiltered {
                test_files: _,
                conftest_files,
                modules_to_import,
                is_filtered: _,
            },
        ) = self.into_parts();
        Ok(shared.into_pipeline(SessionReady {
            test_files: modules_to_import,
            conftest_files,
            session,
            session_violations: fixture_violations,
        }))
    }
}
