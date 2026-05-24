//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.

mod helpers;
pub(crate) mod phases;
pub(crate) mod traits;

use std::sync::Arc;

use crate::{affected, bridge, cache, collector, config, parallel, reporter, retry, strict, types};
use clap::Parser;
use helpers::*;
use pyo3::prelude::*;
use std::io::IsTerminal;

/// The outcome of a single pipeline phase execution.
#[derive(Debug)]
#[allow(dead_code)] // Scaffolding for #450 — phases not yet wired into run().
pub(crate) enum PhaseOutcome {
    /// The phase completed normally; continue to the next phase.
    Continue,
    /// The phase requests an early exit with the given exit code.
    EarlyExit(i32),
}

/// A discrete stage of the test pipeline.
///
/// Implementers should be unit-testable: each phase reads from and writes to
/// [`PipelineContext`], with side effects limited to the phase's own scope.
#[allow(dead_code)] // Scaffolding for #450 — phases not yet wired into run().
pub(crate) trait PipelinePhase {
    /// Human-readable name used in diagnostics and tracing spans.
    fn name(&self) -> &'static str;

    /// Whether this phase should run given the current context.
    ///
    /// Return `false` to skip the phase entirely (e.g. `ListPhase` when
    /// `--list` was not passed).
    fn should_run(&self, ctx: &PipelineContext) -> bool;

    /// Execute the phase, mutating `ctx` as needed.
    ///
    /// Returns [`PhaseOutcome::Continue`] to proceed, or
    /// [`PhaseOutcome::EarlyExit(code)`] to stop the pipeline and return
    /// `code` as the process exit code.
    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, i32>;
}

/// Shared mutable state that flows through every pipeline phase.
///
/// Populated incrementally: early phases (file collection, session setup)
/// fill the inputs; later phases (collection, execution, reporting) consume
/// and augment the results.
#[allow(dead_code)] // Scaffolding for #450 — not yet fully wired into run().
pub(crate) struct PipelineContext {
    pub(crate) cfg: config::Config,
    pub(crate) cli: config::Cli,
    pub(crate) rootdir: camino::Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) test_files: Vec<camino::Utf8PathBuf>,
    pub(crate) conftest_files: Vec<camino::Utf8PathBuf>,
    pub(crate) session: Option<bridge::FixtureSession>,
    pub(crate) items: Vec<Arc<types::TestItem>>,
    pub(crate) raw_violations: Vec<bridge::RawViolation>,
    pub(crate) violated_items: Vec<Arc<types::TestItem>>,
    pub(crate) all_violations: Vec<strict::StrictViolation>,
    pub(crate) suite_lines: Vec<String>,
    pub(crate) timings: Vec<types::TestTiming>,
    pub(crate) interrupted: bool,
    pub(crate) reporter: Option<Box<dyn reporter::Reporter>>,
}

impl PipelineContext {
    /// Construct a context from the result of the setup phase.
    ///
    /// All incremental fields (files, items, timings, etc.) start empty or
    /// `None`; they are populated by subsequent phases.
    #[allow(dead_code)] // Scaffolding for #450 — not yet called outside tests.
    pub(crate) fn from_setup(s: SetupContext) -> Self {
        Self {
            cfg: s.cfg,
            cli: s.cli,
            rootdir: s.rootdir,
            is_tty: s.is_tty,
            use_color: s.use_color,
            base: s.base,
            cache: s.cache,
            test_files: Vec::new(),
            conftest_files: Vec::new(),
            session: None,
            items: Vec::new(),
            raw_violations: Vec::new(),
            violated_items: Vec::new(),
            all_violations: Vec::new(),
            suite_lines: Vec::new(),
            timings: Vec::new(),
            interrupted: false,
            reporter: None,
        }
    }

    /// Build a minimal error reporter suitable for pre-execution failures.
    ///
    /// Uses the base options with verbose disabled, no JSON/JUnit output, and
    /// no plugin reporters.
    #[allow(dead_code)] // Scaffolding for #450 — not yet called outside tests.
    pub(crate) fn make_error_reporter(&self) -> Box<dyn reporter::Reporter> {
        reporter::make_reporter(
            self.base.clone().verbose(false).build(),
            self.is_tty,
            None,
            None,
            vec![],
        )
    }
}

#[derive(Debug)]
pub(crate) struct SetupContext {
    pub(crate) cfg: config::Config,
    pub(crate) cache: cache::TestCache,
    pub(crate) cli: config::Cli,
    pub(crate) rootdir: camino::Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
}

fn setup(py: Python<'_>, args: &[String]) -> PyResult<Result<Box<SetupContext>, i32>> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string())
        .chain(args.iter().cloned())
        .collect();

    let cli = match config::Cli::try_parse_from(&argv) {
        Ok(c) => c,
        Err(e) => {
            // Clap formats this for the user; subscriber may not be initialised yet.
            eprintln!("{}", e);
            return Ok(Err(4));
        }
    };

    // Early-exit flags: handled before any filesystem setup.
    if cli.capture_environment {
        println!("{}", env_string(py));
        return Ok(Err(0));
    }

    let rootdir = config::find_rootdir(cli.paths.first().map(|p| p.as_path()));
    let cfg = config::Config::load(&rootdir).merge_cli(&cli);
    let cache = cache::TestCache::load(&rootdir);

    let is_tty = std::io::stdout().is_terminal();
    let use_color = resolve_color(cfg.color, is_tty);
    let resolved_tb = cli.tb.clone().unwrap_or(cfg.tb.clone());
    let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color)
        .tb(resolved_tb)
        .show_tips(cli.tips)
        .show_warnings(cli.warnings);

    Ok(Ok(Box::new(SetupContext {
        cfg,
        cache,
        cli,
        rootdir,
        is_tty,
        use_color,
        base,
    })))
}

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let SetupContext {
        cfg,
        mut cache,
        cli,
        rootdir,
        is_tty,
        use_color,
        base,
    } = match setup(py, &args)? {
        Err(code) => return Ok(code),
        Ok(ctx) => *ctx,
    };

    let make_error_rep = || {
        reporter::make_reporter(
            base.clone().verbose(false).build(),
            is_tty,
            None,
            None,
            vec![],
        )
    };

    let (test_files, conftest_files) = collector::collect_files(&cfg);

    // --affected: filter test files to only those affected by git changes.
    let test_files = if let Some(base_ref) = &cfg.affected {
        match affected::filter_affected_test_files(py, &test_files, &cfg.rootdir, base_ref) {
            Ok(Some(files)) => {
                if files.is_empty() {
                    println!("no changes detected — nothing to test");
                    return Ok(0);
                }
                let total = test_files.len();
                tracing::info!(
                    affected = files.len(),
                    total,
                    base = base_ref.as_str(),
                    "running affected tests only"
                );
                files
            }
            Ok(None) => {
                tracing::info!("pyproject.toml changed — running all tests");
                test_files
            }
            Err(e) => {
                let err = types::CollectError::PyError(e.to_string());
                return Ok(early_exit_with_error(&[err], &make_error_rep));
            }
        }
    } else {
        test_files
    };

    // Load conftest before importing test files — conftest_loader registers
    // sys.modules["conftest"] so test files can do `from conftest import my_fixture`.
    let session = match bridge::FixtureSession::new(py, &conftest_files) {
        Ok(s) => s,
        Err(e) => {
            let err =
                types::CollectError::PyError(format!("Failed to load conftest fixtures: {}", e));
            return Ok(early_exit_with_error(&[err], &make_error_rep));
        }
    };

    // Load plugins declared in [tool.oxitest] plugins = [...]
    if !cfg.plugins.is_empty() {
        if let Err(e) = session.load_plugins(py, &cfg.plugins, &cfg.plugin_settings) {
            let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
            return Ok(early_exit_with_error(&[err], &make_error_rep));
        }
    }

    // Resolve and set the async backend
    if let Err(e) = session.init_async_backend(py, &cfg.async_backend) {
        let err = types::CollectError::PyError(format!("Async backend init failed: {}", e));
        return Ok(early_exit_with_error(&[err], &make_error_rep));
    }

    // --fixtures: list registered fixtures and exit.
    if cli.fixtures {
        let verbosity = if cli.quiet {
            0
        } else if cli.verbose {
            2
        } else {
            1
        };
        match session.list_fixtures(py, verbosity, cli.keyword.as_deref(), use_color) {
            Ok(output) => {
                if !output.is_empty() {
                    println!("{output}");
                }
            }
            Err(e) => {
                eprintln!("Error listing fixtures: {e}");
                return Ok(1);
            }
        }
        return Ok(0);
    }

    let collector_impl = traits::BridgeCollector;
    let runner_impl = traits::BridgeRunner;
    let parallel_impl = traits::DefaultParallelRunner;

    cache.invalidate_modules();
    let (items, errors, raw_violations) =
        collect_items(py, &test_files, &cfg, &session, &collector_impl, &mut cache);

    if !errors.is_empty() {
        return Ok(early_exit_with_error(&errors, &make_error_rep));
    }

    let StrictResult {
        clean_items,
        violated_items,
        all_violations,
        suite_lines,
    } = match apply_strict(&cfg, items, raw_violations, use_color) {
        Ok(r) => r,
        Err(code) => return Ok(code),
    };

    let items = match apply_filters(clean_items, &cli, &cfg, &cache, &make_error_rep) {
        Ok(items) => items,
        Err(code) => return Ok(code),
    };

    // --list: print collected tests and exit without execution.
    if cli.list {
        println!("{}", format_test_list(&items, cli.verbose));
        return Ok(0);
    }

    let total = violated_items.len() + items.len();
    let async_count = items.iter().filter(|i| i.is_async).count();
    let max_name_width = items
        .iter()
        .chain(violated_items.iter())
        .map(|i| i.fn_name.len())
        .max()
        .unwrap_or(30);
    cache.invalidate(&items);

    // Fetch plugin reporters from Python registry.
    let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !cfg.plugins.is_empty() {
        bridge::get_plugin_reporters(py, &session)
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

    let mut rep = reporter::make_reporter(
        base.total(total)
            .async_count(async_count)
            .name_width(max_name_width)
            .strict_suite_lines(suite_lines)
            .build(),
        is_tty,
        cli.json.clone(),
        cli.junit_xml.clone(),
        plugin_reporters,
    );

    let ctx = ExecutionContext {
        cfg: &cfg,
        cache: &cache,
        session: &session,
        conftest_files: &conftest_files,
        runner: &runner_impl,
        parallel: &parallel_impl,
    };

    let all_items = items.clone(); // Keep for retry phase

    let parallel::PhaseResult {
        interrupted,
        timings,
    } = execute(
        py,
        items,
        violated_items,
        all_violations,
        &ctx,
        rep.as_mut(),
    );

    // ── Retry phase: re-run failed tests if --retries > 0 ────────────
    let (timings, interrupted) = if cfg.retries > 0 && !interrupted {
        let failed_node_ids: std::collections::HashSet<&str> = timings
            .iter()
            .filter(|t| t.outcome.is_failure())
            .map(|t| t.node_id.as_ref())
            .collect();

        if failed_node_ids.is_empty() {
            (timings, interrupted)
        } else {
            let failed_items: Vec<std::sync::Arc<types::TestItem>> = all_items
                .iter()
                .filter(|i| failed_node_ids.contains(i.node_id.as_ref()))
                .cloned()
                .collect();

            let retry_ctx = retry::RetryContext {
                py,
                max_retries: cfg.retries,
                delay_secs: cfg.retries_delay_secs,
                session: &session,
                runner: &runner_impl,
                timeout_secs: cfg.timeout_secs,
            };
            let retry::RetryResult {
                flaky_ids,
                retry_timings,
            } = retry::run_retries(&retry_ctx, &failed_items, rep.as_mut());

            // Replace timings for flaky tests with their retry timings.
            let flaky_set: std::collections::HashSet<&str> =
                flaky_ids.iter().map(|id| id.as_ref()).collect();

            let mut merged_timings: Vec<types::TestTiming> = timings
                .into_iter()
                .filter(|t| !flaky_set.contains(t.node_id.as_ref()))
                .collect();
            merged_timings.extend(retry_timings);

            (merged_timings, interrupted)
        }
    } else {
        (timings, interrupted)
    };

    finalize(&mut cache, &timings, cfg.cache_max_age, &rootdir);

    Ok(rep.finish(&[], interrupted).code())
}

#[cfg(test)]
#[path = "../pipeline_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "../pipeline_phase_tests.rs"]
mod phase_tests;
