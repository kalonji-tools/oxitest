use pyo3::prelude::*;

use super::super::{helpers, Collected, Pipeline, PreFilter};
use crate::types::ExitCode;
use crate::{bridge, config, reporter, strict, types};

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

        if shared.cfg.strict.is_none() {
            return Ok(shared.into_pipeline(PreFilter {
                test_files,
                conftest_files,
                session,
                clean_items: items,
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            }));
        }

        // Build the full violation list.
        let mut all_violations = strict::check_config(&shared.cfg);
        all_violations.extend(strict::check_collected(raw_violations));

        // Abort mode: print and signal early exit.
        if shared.cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
            let abort_lines: Vec<String> = all_violations
                .iter()
                .map(strict::format_violation_line)
                .collect();
            reporter::print_strict_abort(&abort_lines, shared.use_color);
            return Err(ExitCode::CollectError);
        }

        // Enforce mode: build suite-level violation lines.
        let suite_lines: Vec<String> = if shared.cfg.strict == Some(config::StrictMode::Enforce) {
            strict::suite_level(&all_violations)
                .iter()
                .map(|v| v.to_string())
                .collect()
        } else {
            vec![]
        };

        // Partition items into violated vs. clean.
        let (violated_items, clean_items): (Vec<_>, Vec<_>) =
            if shared.cfg.strict == Some(config::StrictMode::Enforce) {
                let violated_ids: std::collections::HashSet<&str> = all_violations
                    .iter()
                    .filter_map(|v| v.node_id())
                    .map(|id| id.as_ref())
                    .collect();
                items
                    .into_iter()
                    .partition(|i| violated_ids.contains(i.node_id.as_ref()))
            } else {
                (vec![], items)
            };

        Ok(shared.into_pipeline(PreFilter {
            test_files,
            conftest_files,
            session,
            clean_items,
            violated_items,
            all_violations,
            suite_lines,
        }))
    }
}
