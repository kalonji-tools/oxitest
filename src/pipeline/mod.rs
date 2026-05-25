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

use crate::{bridge, cache, config, reporter, strict, types};
use clap::Parser;
use helpers::{env_string, resolve_color};
use pyo3::prelude::*;
use std::io::IsTerminal;

/// The outcome of a single pipeline phase execution.
#[derive(Debug)]
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
pub(crate) trait PipelinePhase {
    /// Human-readable name used in diagnostics and tracing spans.
    #[allow(dead_code)] // Reserved for future tracing/diagnostic output.
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

/// Execute a sequence of pipeline phases against a context.
///
/// Returns the exit code: 0 if all phases complete, or the code from the
/// first phase that returns [`PhaseOutcome::EarlyExit`].
pub(crate) fn run_pipeline(
    py: Python<'_>,
    pipeline: &[&dyn PipelinePhase],
    ctx: &mut PipelineContext,
) -> Result<i32, i32> {
    for phase in pipeline {
        if phase.should_run(ctx) {
            match phase.execute(py, ctx)? {
                PhaseOutcome::Continue => {}
                PhaseOutcome::EarlyExit(code) => return Ok(code),
            }
        }
    }
    Ok(0)
}

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let setup_ctx = match setup(py, &args)? {
        Err(code) => return Ok(code),
        Ok(ctx) => *ctx,
    };

    let mut ctx = PipelineContext::from_setup(setup_ctx);

    let collector_impl = traits::BridgeCollector;
    let runner_impl = traits::BridgeRunner;
    let parallel_impl = traits::DefaultParallelRunner;

    let pipeline: &[&dyn PipelinePhase] = &[
        &phases::FileCollectionPhase,
        &phases::AffectedPhase,
        &phases::SessionPhase,
        &phases::FixturesPhase,
        &phases::CollectionPhase {
            collector: &collector_impl,
        },
        &phases::StrictPhase,
        &phases::FilterPhase,
        &phases::ListPhase,
        &phases::ExecutionPhase {
            runner: &runner_impl,
            parallel: &parallel_impl,
        },
        &phases::RetryPhase {
            runner: &runner_impl,
        },
        &phases::FinalizePhase,
    ];

    match run_pipeline(py, pipeline, &mut ctx) {
        Ok(code) | Err(code) => Ok(code),
    }
}

#[cfg(test)]
#[path = "../pipeline_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "../pipeline_phase_tests.rs"]
mod phase_tests;

#[cfg(test)]
#[path = "../pipeline_contract_tests.rs"]
mod contract_tests;
