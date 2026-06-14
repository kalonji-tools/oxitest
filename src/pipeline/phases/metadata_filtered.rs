use pyo3::prelude::*;

use super::super::{helpers, MetadataFiltered, Pipeline, SessionReady};
use crate::types::ExitCode;

impl Pipeline<MetadataFiltered> {
    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline<SessionReady>, ExitCode> {
        let (session, fixture_violations) =
            helpers::init_session(py, &self.shared.conftest_files, &self.cfg, || {
                self.make_error_reporter()
            })?;
        let (
            mut shared,
            MetadataFiltered {
                modules_to_import, ..
            },
        ) = self.into_parts();
        // Replace test_files with the filtered modules to import.
        shared.test_files = modules_to_import;
        Ok(shared.into_pipeline(SessionReady {
            session,
            session_violations: fixture_violations,
        }))
    }
}
