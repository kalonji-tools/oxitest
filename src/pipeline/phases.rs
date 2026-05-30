//! Pipeline phase implementations.
//!
//! Each phase is a unit struct implementing [`PipelinePhase`]. Phases read
//! from and write to [`PipelineContext`], delegating to helper functions
//! in [`super::helpers`] for the actual work.

use pyo3::prelude::*;

use super::helpers;
use super::traits::{ModuleCollector, ParallelRunner, TestRunner};
use super::{PhaseOutcome, PipelineContext, PipelinePhase};
use crate::cache::{ModuleCache, TimingCache};
use crate::types::ExitCode;
use crate::{affected, bridge, collector, parallel, reporter, retry, types};

// ─── FileCollectionPhase ─────────────────────────────────────────────────────

pub(crate) struct FileCollectionPhase;

impl PipelinePhase for FileCollectionPhase {
    fn name(&self) -> &'static str {
        "file-collection"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(
        &self,
        _py: Python<'_>,
        ctx: &mut PipelineContext,
    ) -> Result<PhaseOutcome, ExitCode> {
        let (test_files, conftest_files) = collector::collect_files(&ctx.cfg).map_err(|e| {
            eprintln!("error: invalid glob pattern in python_files: {e}");
            ExitCode::UsageError
        })?;
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

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
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

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");

        let verbosity = ctx.cfg.verbosity as i32;

        match session.list_fixtures(py, verbosity, ctx.cli.keyword.as_deref(), ctx.use_color) {
            Ok(output) => {
                if !output.is_empty() {
                    println!("{output}");
                }
            }
            Err(e) => {
                eprintln!("Error listing fixtures: {e}");
                return Ok(PhaseOutcome::EarlyExit(ExitCode::Failure));
            }
        }
        Ok(PhaseOutcome::EarlyExit(ExitCode::Success))
    }
}

// ─── CollectionPhase ─────────────────────────────────────────────────────────

/// Imports test modules and collects test items + violations.
pub(crate) struct CollectionPhase<'a> {
    pub collector: &'a dyn ModuleCollector,
}

impl PipelinePhase for CollectionPhase<'_> {
    fn name(&self) -> &'static str {
        "collection"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");
        ctx.cache.invalidate_modules();
        let (items, errors, raw_violations) = helpers::collect_items(
            py,
            &ctx.test_files,
            &ctx.cfg,
            session,
            self.collector,
            &mut ctx.cache,
        );
        if !errors.is_empty() {
            return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                &errors,
                &|| ctx.make_error_reporter(),
            )));
        }
        ctx.items = items;
        ctx.raw_violations = raw_violations;
        Ok(PhaseOutcome::Continue)
    }
}

// ─── FixtureValidationPhase ──────────────────────────────────────────────────

/// Validates that every `Fixture[T]` parameter name matches a registered
/// fixture. Runs after collection, before strict checks. Aborts with
/// `CollectError` exit code on mismatch.
pub(crate) struct FixtureValidationPhase;

/// Format fixture validation errors into a human-readable message.
///
/// Each error gets a line like:
///   `test.py::test_foo - fixture 'sotre' not found (did you mean 'store'?)`
///
/// The `registered` list provides candidates for edit-distance suggestions.
pub(crate) fn format_fixture_errors(
    errors: &[(types::NodeId, String)],
    registered: &[String],
) -> String {
    let mut messages = Vec::with_capacity(errors.len());
    for (node_id, bad_name) in errors {
        let suggestion =
            crate::edit_distance::closest_match(bad_name, registered.iter().map(|s| s.as_str()), 2);
        let msg = match suggestion {
            Some(s) => format!(
                "  {} - fixture '{}' not found (did you mean '{}'?)",
                node_id, bad_name, s
            ),
            None => format!("  {} - fixture '{}' not found", node_id, bad_name),
        };
        messages.push(msg);
    }
    format!("ERROR collecting tests\n{}", messages.join("\n"))
}

impl PipelinePhase for FixtureValidationPhase {
    fn name(&self) -> &'static str {
        "fixture-validation"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");
        let errors = bridge::validate_fixture_names(py, session, &ctx.items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(PhaseOutcome::Continue);
        }

        let registered = bridge::registered_fixture_names(py, session).unwrap_or_default();
        let full_message = format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
            &[err],
            &|| ctx.make_error_reporter(),
        )))
    }
}

// ─── StrictPhase ─────────────────────────────────────────────────────────────

/// Checks strict-mode violations. Abort mode exits early; enforce mode
/// partitions items into clean vs violated.
pub(crate) struct StrictPhase;

impl PipelinePhase for StrictPhase {
    fn name(&self) -> &'static str {
        "strict"
    }

    fn should_run(&self, ctx: &PipelineContext) -> bool {
        ctx.cfg.strict.is_some()
    }

    fn execute(
        &self,
        _py: Python<'_>,
        ctx: &mut PipelineContext,
    ) -> Result<PhaseOutcome, ExitCode> {
        let raw_violations = std::mem::take(&mut ctx.raw_violations);
        let items = std::mem::take(&mut ctx.items);
        match helpers::apply_strict(&ctx.cfg, items, raw_violations, ctx.use_color) {
            Ok(strict_result) => {
                ctx.items = strict_result.clean_items;
                ctx.violated_items = strict_result.violated_items;
                ctx.all_violations = strict_result.all_violations;
                ctx.suite_lines = strict_result.suite_lines;
                Ok(PhaseOutcome::Continue)
            }
            Err(code) => Ok(PhaseOutcome::EarlyExit(code)),
        }
    }
}

// ─── FilterPhase ─────────────────────────────────────────────────────────────

/// Applies keyword (-k), marker (-m), and last-failed filters.
pub(crate) struct FilterPhase;

impl PipelinePhase for FilterPhase {
    fn name(&self) -> &'static str {
        "filter"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(
        &self,
        _py: Python<'_>,
        ctx: &mut PipelineContext,
    ) -> Result<PhaseOutcome, ExitCode> {
        let items = std::mem::take(&mut ctx.items);
        let make_rep = || {
            reporter::make_reporter(
                ctx.base
                    .clone()
                    .verbosity(crate::config::Verbosity::Normal)
                    .build(),
                ctx.is_tty,
                None,
                None,
                vec![],
            )
        };
        match helpers::apply_filters(items, &ctx.cli, &ctx.cfg, &ctx.cache, &make_rep) {
            Ok(filtered) => {
                ctx.items = filtered;
                Ok(PhaseOutcome::Continue)
            }
            Err(code) => Ok(PhaseOutcome::EarlyExit(code)),
        }
    }
}

// ─── ListPhase ───────────────────────────────────────────────────────────────

/// Handles `--list`: prints collected tests and exits without execution.
pub(crate) struct ListPhase;

impl PipelinePhase for ListPhase {
    fn name(&self) -> &'static str {
        "list"
    }

    fn should_run(&self, ctx: &PipelineContext) -> bool {
        ctx.cli.list
    }

    fn execute(
        &self,
        _py: Python<'_>,
        ctx: &mut PipelineContext,
    ) -> Result<PhaseOutcome, ExitCode> {
        println!(
            "{}",
            helpers::format_test_list(&ctx.items, ctx.cfg.verbosity)
        );
        Ok(PhaseOutcome::EarlyExit(ExitCode::Success))
    }
}

// ─── ExecutionPhase ──────────────────────────────────────────────────────────

/// Creates the reporter, dispatches serial or parallel execution, and stores
/// timings + interrupted flag on the context.
pub(crate) struct ExecutionPhase<'a> {
    pub runner: &'a dyn TestRunner,
    pub parallel: &'a dyn ParallelRunner,
}

impl PipelinePhase for ExecutionPhase<'_> {
    fn name(&self) -> &'static str {
        "execution"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");

        let total = ctx.violated_items.len() + ctx.items.len();
        let async_count = ctx.items.iter().filter(|i| i.is_async).count();
        let max_name_width = ctx
            .items
            .iter()
            .chain(ctx.violated_items.iter())
            .map(|i| i.fn_name.len())
            .max()
            .unwrap_or(30);
        ctx.cache.invalidate(&ctx.items);

        // Fetch plugin reporters from Python registry.
        let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !ctx.cfg.plugins.is_empty() {
            bridge::get_plugin_reporters(py, session)
                .unwrap_or_default()
                .into_iter()
                .map(|obj| {
                    Box::new(reporter::plugin::PyPluginReporter::new(obj))
                        as Box<dyn reporter::Reporter>
                })
                .collect()
        } else {
            vec![]
        };

        let rep = reporter::make_reporter(
            ctx.base
                .clone()
                .total(total)
                .async_count(async_count)
                .name_width(max_name_width)
                .strict_suite_lines(std::mem::take(&mut ctx.suite_lines))
                .build(),
            ctx.is_tty,
            ctx.cli.json.clone(),
            ctx.cli.junit_xml.clone(),
            plugin_reporters,
        );
        ctx.reporter = Some(rep);

        let violated_items = std::mem::take(&mut ctx.violated_items);
        let all_violations = std::mem::take(&mut ctx.all_violations);
        let items = std::mem::take(&mut ctx.items);

        let exec_ctx = helpers::ExecutionContext {
            cfg: &ctx.cfg,
            cache: &ctx.cache,
            session,
            conftest_files: &ctx.conftest_files,
            runner: self.runner,
            parallel: self.parallel,
        };

        let parallel::PhaseResult {
            interrupted,
            timings,
        } = helpers::execute(
            py,
            items.clone(),
            violated_items,
            all_violations,
            &exec_ctx,
            ctx.reporter.as_deref_mut().expect("just created"),
        );

        ctx.items = items; // Restore for RetryPhase
        ctx.timings = timings;
        ctx.interrupted = interrupted;
        Ok(PhaseOutcome::Continue)
    }
}

// ─── RetryPhase ──────────────────────────────────────────────────────────────

/// Re-runs failed tests serially when `--retries > 0`.
pub(crate) struct RetryPhase<'a> {
    pub runner: &'a dyn TestRunner,
}

impl PipelinePhase for RetryPhase<'_> {
    fn name(&self) -> &'static str {
        "retry"
    }

    fn should_run(&self, ctx: &PipelineContext) -> bool {
        ctx.cfg.retries > 0 && !ctx.interrupted
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");
        let rep = ctx
            .reporter
            .as_deref_mut()
            .expect("ExecutionPhase must run first");

        let failed_items = retry::identify_failed_items(&ctx.items, &ctx.timings);
        if failed_items.is_empty() {
            return Ok(PhaseOutcome::Continue);
        }

        let retry_ctx = retry::RetryContext {
            py,
            max_retries: ctx.cfg.retries,
            delay_secs: ctx.cfg.retries_delay_secs,
            session,
            runner: self.runner,
            timeout_secs: ctx.cfg.timeout_secs,
            keep_tmp: ctx.cfg.keep_tmp.as_ref().map(|m| m.as_str()),
            show_locals: ctx.cfg.show_locals,
            show_internals: ctx.cfg.show_internals,
        };
        let retry::RetryResult {
            flaky_ids,
            retry_timings,
        } = retry::run_retries(&retry_ctx, &failed_items, rep);

        let original_timings = std::mem::take(&mut ctx.timings);
        ctx.timings = retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

        Ok(PhaseOutcome::Continue)
    }
}

// ─── FinalizePhase ───────────────────────────────────────────────────────────

/// Persists cache and finishes the reporter. Always returns `EarlyExit` with
/// the final exit code.
pub(crate) struct FinalizePhase;

impl PipelinePhase for FinalizePhase {
    fn name(&self) -> &'static str {
        "finalize"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(
        &self,
        _py: Python<'_>,
        ctx: &mut PipelineContext,
    ) -> Result<PhaseOutcome, ExitCode> {
        helpers::finalize(
            &mut ctx.cache,
            &ctx.timings,
            ctx.cfg.cache_max_age,
            &ctx.rootdir,
        );
        let mut rep = ctx.reporter.take().expect("ExecutionPhase must run first");
        let code = rep
            .finish(&[], ctx.interrupted, &reporter::RunStats::new())
            .code();
        Ok(PhaseOutcome::EarlyExit(code))
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

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let base_ref = ctx.cfg.affected.as_ref().expect("checked in should_run");
        match affected::filter_affected_test_files(py, &ctx.test_files, &ctx.cfg.rootdir, base_ref)
        {
            Ok(Some(files)) => {
                if files.is_empty() {
                    println!("no changes detected — nothing to test");
                    return Ok(PhaseOutcome::EarlyExit(ExitCode::Success));
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
                return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                    &[e.into()],
                    &|| ctx.make_error_reporter(),
                )));
            }
        }
        Ok(PhaseOutcome::Continue)
    }
}
