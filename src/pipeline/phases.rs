//! Pipeline phase implementations.
//!
//! Each phase is a unit struct implementing [`PipelinePhase`]. Phases read
//! from and write to [`PipelineContext`], delegating to submodule functions
//! for the actual work.

use pyo3::prelude::*;

use super::traits::{ModuleCollector, ParallelRunner, TestRunner};
use super::{
    collection, execution, helpers, CollectionState, ExecutionResults, PhaseOutcome,
    PipelineContext, PipelinePhase,
};
use crate::cache::{ModuleCache, OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{
    affected, bridge, collector, config, filter, parallel, query, reporter, retry, strict, types,
};

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
        let (session, fixture_violations) =
            match bridge::FixtureSession::new(py, &ctx.conftest_files) {
                Ok(pair) => pair,
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
        // Stash fixture violations in the Collected state for CollectionPhase to merge.
        ctx.collection = CollectionState::Collected {
            items: Vec::new(),
            raw_violations: fixture_violations,
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

// ─── QueryPhase ───────────────────────────────────────────────────────────────

pub(crate) struct QueryPhase;

impl PipelinePhase for QueryPhase {
    fn name(&self) -> &'static str {
        "query"
    }

    fn should_run(&self, _ctx: &PipelineContext) -> bool {
        true
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let crate::config::Command::Query(ref args) = ctx.command else {
            unreachable!("QueryPhase only runs for Command::Query");
        };

        // --tree is only valid for fixtures resource
        if args.tree {
            if args.resource != query::resource::ResourceKind::Fixtures {
                eprintln!("error: --tree is only valid for the 'fixtures' resource");
                return Ok(PhaseOutcome::EarlyExit(ExitCode::UsageError));
            }
            let session = ctx.session.as_ref().expect("SessionPhase must run first");
            let verbosity = ctx.cfg.verbosity as i32;
            match query::bridge::tree_fixtures(session, py, verbosity, None, ctx.use_color) {
                Ok(output) => {
                    if output.starts_with("error:") {
                        eprintln!("{output}");
                        return Ok(PhaseOutcome::EarlyExit(ExitCode::Failure));
                    }
                    if !output.is_empty() {
                        println!("{output}");
                    }
                }
                Err(e) => {
                    eprintln!("Error rendering fixture tree: {e}");
                    return Ok(PhaseOutcome::EarlyExit(ExitCode::Failure));
                }
            }
            return Ok(PhaseOutcome::EarlyExit(ExitCode::Success));
        }

        // Handle --fzf
        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &ctx.test_files,
                &ctx.conftest_files,
                ctx.session.as_ref(),
                &ctx.cfg,
                ctx.use_color,
            ) {
                Ok(()) => return Ok(PhaseOutcome::EarlyExit(ExitCode::Success)),
                Err(e) => {
                    eprintln!("error: {e}");
                    return Ok(PhaseOutcome::EarlyExit(ExitCode::Failure));
                }
            }
        }

        // Standard query path
        match query::run_query(
            py,
            args,
            &ctx.test_files,
            &ctx.conftest_files,
            ctx.session.as_ref(),
            &ctx.cfg,
            ctx.is_tty,
            ctx.use_color,
        ) {
            Ok(output) => {
                if !output.is_empty() {
                    print!("{output}");
                }
                Ok(PhaseOutcome::EarlyExit(ExitCode::Success))
            }
            Err(msg) => {
                eprintln!("error: {msg}");
                Ok(PhaseOutcome::EarlyExit(ExitCode::UsageError))
            }
        }
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
        let (items, errors, raw_violations, profile) = collection::collect_items(
            py,
            &ctx.test_files,
            &ctx.cfg,
            session,
            self.collector,
            &mut ctx.cache,
        );
        ctx.collection_profile = profile;
        if !errors.is_empty() {
            return Ok(PhaseOutcome::EarlyExit(helpers::early_exit_with_error(
                &errors,
                &|| ctx.make_error_reporter(),
            )));
        }

        // Merge session-phase violations (if any) with collection violations.
        let mut merged_violations =
            match std::mem::replace(&mut ctx.collection, CollectionState::Empty) {
                CollectionState::Collected {
                    raw_violations: existing,
                    ..
                } => existing,
                CollectionState::Empty => Vec::new(),
                other => {
                    panic!("expected Empty or Collected before CollectionPhase, got {other:?}")
                }
            };
        merged_violations.extend(raw_violations);

        // Detect unused fixtures when strict mode is enabled.
        // Skip when targeting a subset (explicit paths or node IDs) —
        // unused-fixture detection requires the full suite to be meaningful.
        if ctx.cfg.strict.is_some() && !ctx.cfg.has_explicit_paths {
            if let Ok(unused) = bridge::find_unused_fixtures(py, session, &items) {
                merged_violations.extend(unused);
            }
        }

        // Apply node ID filter early so StrictPhase only sees targeted
        // items — prevents false positives for violations in non-selected
        // tests (#738).
        let items =
            filter::filter_by_node_ids(items, &ctx.cfg.node_ids, &ctx.cfg.node_id_source_files);

        ctx.collection = CollectionState::Collected {
            items,
            raw_violations: merged_violations,
        };

        if let Some(ref profile) = ctx.collection_profile {
            eprintln!("{}", collection::format_collection_profile(profile));
        }

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
        let items = match &ctx.collection {
            CollectionState::Collected { items, .. } => items,
            other => panic!("expected Collected before FixtureValidationPhase, got {other:?}"),
        };
        let errors = bridge::validate_fixture_names(py, session, items)
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
        let (items, raw_violations) =
            match std::mem::replace(&mut ctx.collection, CollectionState::Empty) {
                CollectionState::Collected {
                    items,
                    raw_violations,
                } => (items, raw_violations),
                other => panic!("expected Collected before StrictPhase, got {other:?}"),
            };

        // Build the full violation list.
        let all_violations: Vec<strict::StrictViolation> = if ctx.cfg.strict.is_some() {
            let mut v = strict::check_config(&ctx.cfg);
            v.extend(strict::check_collected(raw_violations));
            v
        } else {
            vec![]
        };

        // Abort mode: print and signal early exit.
        if ctx.cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
            let abort_lines: Vec<String> = all_violations
                .iter()
                .map(strict::format_violation_line)
                .collect();
            reporter::print_strict_abort(&abort_lines, ctx.use_color);
            return Ok(PhaseOutcome::EarlyExit(ExitCode::CollectError));
        }

        // Enforce mode: build suite-level violation lines.
        let suite_lines: Vec<String> = if ctx.cfg.strict == Some(config::StrictMode::Enforce) {
            strict::suite_level(&all_violations)
                .iter()
                .map(|v| v.to_string())
                .collect()
        } else {
            vec![]
        };

        // Partition items into violated vs. clean.
        let (violated_items, clean_items): (Vec<_>, Vec<_>) =
            if ctx.cfg.strict == Some(config::StrictMode::Enforce) {
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

        ctx.collection = CollectionState::Partitioned {
            clean_items,
            violated_items,
            all_violations,
            suite_lines,
        };
        Ok(PhaseOutcome::Continue)
    }
}

// ─── FilterPhase ─────────────────────────────────────────────────────────────

/// Convert a [`TestItem`] into a [`QueryEntry`] for DSL evaluation.
fn item_to_query_entry(item: &types::TestItem) -> query::resource::QueryEntry {
    let mut fields = std::collections::HashMap::new();
    fields.insert("name".to_string(), item.node_id.to_string());
    fields.insert("source".to_string(), item.module_path.to_string());
    fields.insert("mark".to_string(), item.markers.join(","));
    fields.insert("async".to_string(), item.is_async.to_string());
    query::resource::QueryEntry { fields }
}

/// Applies query DSL (-E) and last-failed filters.
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
        // Accept either Collected (strict skipped) or Partitioned (strict ran).
        let (items, violated_items, all_violations, suite_lines) =
            match std::mem::replace(&mut ctx.collection, CollectionState::Empty) {
                CollectionState::Collected { items, .. } => {
                    // Strict was skipped — no violations to carry forward.
                    (items, vec![], vec![], vec![])
                }
                CollectionState::Partitioned {
                    clean_items,
                    violated_items,
                    all_violations,
                    suite_lines,
                } => (clean_items, violated_items, all_violations, suite_lines),
                other => {
                    panic!("expected Collected or Partitioned before FilterPhase, got {other:?}")
                }
            };

        // Node ID filter (positional node IDs) — applied first.
        let items =
            filter::filter_by_node_ids(items, &ctx.cfg.node_ids, &ctx.cfg.node_id_source_files);

        let expression = match &ctx.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };

        // Query DSL filter (-E).
        let early_exit =
            |ctx: &mut PipelineContext, msg: String| -> Result<PhaseOutcome, ExitCode> {
                let code = ctx
                    .make_error_reporter()
                    .finish(
                        &[types::CollectError::PyError(msg)],
                        false,
                        &reporter::ReporterSession::new(0),
                    )
                    .code();
                Ok(PhaseOutcome::EarlyExit(code))
            };
        let items = if let Some(expr_str) = expression.as_deref() {
            let tokens = match query::compile::lex(expr_str) {
                Ok(t) => t,
                Err(e) => return early_exit(ctx, format!("invalid -E expression: {e}")),
            };
            let parsed = match query::compile::parse(tokens) {
                Ok(p) => p,
                Err(e) => return early_exit(ctx, format!("invalid -E expression: {e}")),
            };
            if let Err(e) =
                query::eval::validate_predicates(&parsed, &query::resource::ResourceKind::Tests)
            {
                return early_exit(ctx, format!("invalid -E expression: {e}"));
            }
            items
                .into_iter()
                .filter(|item| {
                    let entry = item_to_query_entry(item);
                    query::eval::eval(&parsed, &entry)
                })
                .collect()
        } else {
            items
        };

        // Last-failed filter (--failed=only / --failed=first).
        let total_before_failed_filter = items.len();
        let items = match ctx.cfg.failed {
            Some(config::FailedMode::Only) => {
                let failed_ids = ctx.cache.last_failed_ids();
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
                let failed_ids = ctx.cache.last_failed_ids();
                filter::sort_failed_first(items, &failed_ids)
            }
            None => items,
        };

        ctx.collection = CollectionState::Ready {
            clean_items: items,
            violated_items,
            all_violations,
            suite_lines,
        };
        Ok(PhaseOutcome::Continue)
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

        let (clean_items, violated_items, all_violations, suite_lines) =
            match std::mem::replace(&mut ctx.collection, CollectionState::Empty) {
                CollectionState::Ready {
                    clean_items,
                    violated_items,
                    all_violations,
                    suite_lines,
                } => (clean_items, violated_items, all_violations, suite_lines),
                other => panic!("expected Ready before ExecutionPhase, got {other:?}"),
            };

        let total = violated_items.len() + clean_items.len();
        let fn_count = {
            let mut seen = std::collections::HashSet::new();
            for item in clean_items.iter().chain(violated_items.iter()) {
                seen.insert((&item.module_path, &item.fn_name));
            }
            seen.len()
        };
        let async_count = clean_items.iter().filter(|i| i.is_async).count();
        let max_name_width = clean_items
            .iter()
            .chain(violated_items.iter())
            .map(|i| i.fn_name.len())
            .max()
            .unwrap_or(30);
        ctx.cache.invalidate(&clean_items);

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

        let (json_path, junit_path) = match &ctx.command {
            crate::config::Command::Run(a) => (a.json.clone(), a.junit_xml.clone()),
            _ => (None, None),
        };

        let mut rep = reporter::make_reporter(
            ctx.base
                .clone()
                .total(total)
                .fn_count(fn_count)
                .async_count(async_count)
                .name_width(max_name_width)
                .strict_suite_lines(suite_lines)
                .build(),
            ctx.is_tty,
            json_path,
            junit_path,
            plugin_reporters,
        );

        let exec_ctx = execution::ExecutionContext {
            cfg: &ctx.cfg,
            cache: &ctx.cache,
            session,
            conftest_files: &ctx.conftest_files,
            python_bin: &ctx.python_bin,
            runner: self.runner,
            parallel: self.parallel,
        };

        let parallel::PhaseResult {
            interrupted,
            timings,
        } = execution::execute(
            py,
            &clean_items,
            violated_items,
            all_violations,
            &exec_ctx,
            rep.as_mut(),
        );

        ctx.collection = CollectionState::Executed { items: clean_items };
        ctx.execution_results = Some(ExecutionResults {
            timings,
            interrupted,
            reporter: rep,
        });
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
        let not_interrupted = ctx
            .execution_results
            .as_ref()
            .is_none_or(|r| !r.interrupted);
        ctx.cfg.retries > 0 && not_interrupted
    }

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let session = ctx.session.as_ref().expect("SessionPhase must run first");
        let res = ctx
            .execution_results
            .as_mut()
            .expect("ExecutionPhase must run first");

        let items = match &ctx.collection {
            CollectionState::Executed { items } => items,
            other => panic!("expected Executed before RetryPhase, got {other:?}"),
        };

        let failed_items = retry::identify_failed_items(items, &res.timings);
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
        } = retry::run_retries(&retry_ctx, &failed_items, res.reporter.as_mut());

        let original_timings = std::mem::take(&mut res.timings);
        res.timings = retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

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

    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode> {
        let ExecutionResults {
            timings,
            interrupted,
            mut reporter,
        } = ctx
            .execution_results
            .take()
            .expect("ExecutionPhase must run first");

        // Fetch fixture timings from the Python session before finalizing.
        if let Some(session) = ctx.session.as_ref() {
            if let Ok(ft) = reporter::bridge::get_fixture_timings(session, py) {
                if !ft.is_empty() {
                    reporter.set_fixture_timings(ft);
                }
            }
        }

        helpers::finalize(
            &mut ctx.cache,
            &timings,
            ctx.cfg.cache_max_age,
            &ctx.rootdir,
        );
        // Fetch fixture cache stats from Python session.
        if let Some(session) = ctx.session.as_ref() {
            if let Ok(stats) = reporter::bridge::get_cache_stats(session, py) {
                if stats.hits + stats.misses > 0 {
                    reporter.set_fixture_cache_stats(stats.hits, stats.misses, stats.breakdown);
                }
            }
        }
        let code = reporter
            .finish(&[], interrupted, &reporter::ReporterSession::new(0))
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
        let _ = py; // GIL no longer needed for import analysis
        match affected::filter_affected_test_files(&ctx.test_files, &ctx.cfg.rootdir, base_ref) {
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
