use pyo3::prelude::*;

use super::super::{helpers, Collected, Pipeline, PreFilter};
use crate::types::ExitCode;
use crate::{bridge, types};

impl Pipeline<Collected> {
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        let errors = bridge::validate_fixture_names(py, &self.state.session, &self.state.items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(self);
        }

        let registered =
            bridge::registered_fixture_names(py, &self.state.session).unwrap_or_default();
        let full_message = super::super::format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Err(helpers::early_exit_with_error(&[err], &|| {
            self.make_error_reporter()
        }))
    }

    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Pipeline<PreFilter>, ExitCode> {
        let (
            shared,
            Collected {
                test_files,
                conftest_files,
                session,
                items,
                raw_violations,
                collection_profile: _,
            },
        ) = self.into_parts();

        let result =
            helpers::apply_strict_mode(&shared.cfg, items, raw_violations, shared.use_color)?;

        Ok(shared.into_pipeline(PreFilter {
            test_files,
            conftest_files,
            session,
            clean_items: result.clean_items,
            violated_items: result.violated_items,
            all_violations: result.all_violations,
            suite_lines: result.suite_lines,
        }))
    }
}
