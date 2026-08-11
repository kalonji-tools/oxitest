//! Transitions from `Collected` phase: `validate`, `strict_or_skip`

use pyo3::prelude::*;

use super::super::helpers;
use super::super::{Pipeline, PipelinePhase};
use super::apply_query_dsl_filter;
use crate::types::ExitCode;
use crate::{bridge, config, filter, types};

impl Pipeline {
    // 9. validate: Collected -> Collected
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Self, ExitCode> {
        let PipelinePhase::Collected {
            ref session,
            ref items,
            ..
        } = self.phase
        else {
            unreachable!("validate called outside Collected phase")
        };

        // B1 first, and it exits differently on purpose — see below.
        //
        // Scoped to the modules that were actually collected. `fx_usages` is
        // filled during prescan, which runs *before* `filter_metadata` narrows
        // the import set for a node ID, `-E`, `--failed=only` or `--affected`.
        // The registry is filled by the imports that survive that narrowing, so
        // an unscoped check reads a deselected module's accesses against a
        // catalog missing its declaring package and refuses legal code.
        let collected: std::collections::HashSet<&str> =
            items.iter().map(|item| item.module_path()).collect();
        let boundary =
            bridge::validate_fx_boundaries(py, session, &self.shared.fx_usages, &collected)
                .map_err(|_| ExitCode::CollectError)?;
        if !boundary.is_empty() {
            return Err(self.refuse_fx_boundaries(&boundary));
        }

        let errors = bridge::validate_fixture_names(py, session, items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(self);
        }

        let registered = bridge::registered_fixture_names(py, session).unwrap_or_default();
        let full_message = super::super::format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Err(helpers::early_exit_with_error(&[err], &|| {
            self.make_error_reporter()
        }))
    }

    /// Refuse the run over statically-visible `fx.` accesses that cannot resolve.
    ///
    /// Exits `UsageError`, **not** the `CollectError` its sibling above uses.
    /// `exit-codes.md` defines exit 4 by the *class* of the error and not by when
    /// oxitest detects it, and it defines exit 3 narrowly as a test file that
    /// could not be imported or a `--strict=abort` violation. A fixture wiring
    /// error is neither, so it keeps the class it has at access time — where the
    /// same access already exits 4 — rather than inheriting the exit code of the
    /// transition it happens to be caught in.
    ///
    /// Every violation is reported, not the first: a run refused over one line
    /// would need re-running to find the next, which is the loop #1797 removed
    /// for absent targets.
    fn refuse_fx_boundaries(&self, violations: &[(String, usize, String)]) -> ExitCode {
        let mut out = String::from("fixture boundary violations found during collection:\n");
        for (module_path, lineno, message) in violations {
            out.push_str(&format!("\n  {module_path}:{lineno}\n    {message}\n"));
        }
        let err = types::CollectError::PyError(out);
        helpers::early_exit_with_error(&[err], &|| self.make_error_reporter());
        ExitCode::UsageError
    }

    // 10. strict_or_skip: Collected -> Ready
    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Self, ExitCode> {
        let PipelinePhase::Collected {
            session,
            items,
            raw_violations,
        } = self.phase
        else {
            unreachable!("strict_or_skip called outside Collected phase")
        };
        let shared = self.shared;

        let result = helpers::apply_strict_mode(&shared, items, raw_violations)?;

        // ── Item-level filtering (formerly PreFilter::filter) ────────────

        // Node ID filter (positional node IDs).
        //
        // No #1797 Target check here, deliberately. Transition 6 already refused
        // a Target that matched nothing, against the complete collected set.
        // `apply_strict_mode` has run by this point, so repeating the check on
        // its output could report a Target as unmatched when strict mode dropped
        // the item that matched it.
        let source_files = shared.cfg.filter.source_files();
        let items = filter::filter_by_node_ids(
            result.clean_items,
            &shared.cfg.filter.node_ids,
            &source_files,
        );

        let expression = match &shared.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };

        // Query DSL filter (-E).
        let items = if let Some(expr_str) = expression.as_deref() {
            apply_query_dsl_filter(items, expr_str, &shared)?
        } else {
            items
        };

        // Last-failed filter (--failed=only / --failed=first).
        let total_before_failed_filter = items.len();
        let items = match shared.cfg.filter.failed {
            Some(config::FailedMode::Only) => {
                let failed_ids = shared.cache.last_failed_ids();
                if failed_ids.is_empty() {
                    tracing::info!(
                        count = items.len(),
                        "no recorded failures — running all tests"
                    );
                    items
                } else {
                    let filtered = filter::filter_last_failed(items, &failed_ids);
                    tracing::info!(
                        running = filtered.len(),
                        total = total_before_failed_filter,
                        "running tests in --failed=only mode"
                    );
                    filtered
                }
            }
            Some(config::FailedMode::First) => {
                let failed_ids = shared.cache.last_failed_ids();
                filter::sort_failed_first(items, &failed_ids)
            }
            None => items,
        };

        Ok(Self {
            shared,
            phase: PipelinePhase::Ready {
                session,
                clean_items: items,
                violated_items: result.violated_items,
                all_violations: result.all_violations,
                suite_lines: result.suite_lines,
            },
        })
    }
}
