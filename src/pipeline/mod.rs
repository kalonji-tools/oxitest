//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.

mod arrange;
mod collection;
mod execution;
mod helpers;
pub(crate) mod phases;
pub(crate) mod traits;

use std::sync::Arc;

use crate::types::ExitCode;
use crate::{bridge, cache, config, query, reporter, strict, types};
use helpers::env_string;
use pyo3::prelude::*;
use std::io::IsTerminal;

/// The outcome of a single pipeline phase execution.
#[derive(Debug)]
pub(crate) enum PhaseOutcome {
    /// The phase completed normally; continue to the next phase.
    Continue,
    /// The phase requests an early exit with the given exit code.
    EarlyExit(ExitCode),
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
    fn execute(&self, py: Python<'_>, ctx: &mut PipelineContext) -> Result<PhaseOutcome, ExitCode>;
}

/// Results from the execution phase: timings, interrupt status, and the
/// reporter instance.  Bundled into a single struct so downstream phases
/// (retry, finalize) can access them via one `Option` instead of three
/// independent fields.
pub(crate) struct ExecutionResults {
    pub(crate) timings: Vec<types::TestTiming>,
    pub(crate) interrupted: bool,
    pub(crate) reporter: Box<dyn reporter::Reporter>,
}

/// Tracks the lifecycle of collected test items through the pipeline.
///
/// Each variant represents a well-defined stage.  Phases `match` on the
/// current variant and transition to the next, making illegal states
/// unrepresentable.
#[derive(Debug)]
pub(crate) enum CollectionState {
    /// No items collected yet (initial state).
    Empty,
    /// Items have been collected from test files but not yet partitioned
    /// by strict-mode checks.
    Collected {
        items: Vec<Arc<types::TestItem>>,
        raw_violations: Vec<bridge::RawViolation>,
    },
    /// Strict-mode checks have partitioned items into clean vs violated.
    Partitioned {
        clean_items: Vec<Arc<types::TestItem>>,
        violated_items: Vec<Arc<types::TestItem>>,
        all_violations: Vec<strict::StrictViolation>,
        suite_lines: Vec<String>,
    },
    /// Filters (-k, -m, --failed) have been applied; items are ready to run.
    Ready {
        clean_items: Vec<Arc<types::TestItem>>,
        violated_items: Vec<Arc<types::TestItem>>,
        all_violations: Vec<strict::StrictViolation>,
        suite_lines: Vec<String>,
    },
    /// Execution has completed; only the original items are kept for retry.
    Executed { items: Vec<Arc<types::TestItem>> },
}

/// Shared mutable state that flows through every pipeline phase.
///
/// Populated incrementally: early phases (file collection, session setup)
/// fill the inputs; later phases (collection, execution, reporting) consume
/// and augment the results.
pub(crate) struct PipelineContext {
    pub(crate) cfg: config::Config,
    pub(crate) command: config::Command,
    pub(crate) rootdir: camino::Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) test_files: Vec<camino::Utf8PathBuf>,
    pub(crate) conftest_files: Vec<camino::Utf8PathBuf>,
    pub(crate) session: Option<bridge::FixtureSession>,
    pub(crate) collection: CollectionState,
    pub(crate) execution_results: Option<ExecutionResults>,
    pub(crate) python_bin: String,
    pub(crate) collection_profile: Option<collection::CollectionProfile>,
}

impl PipelineContext {
    /// Construct a context from the result of the setup phase.
    ///
    /// All incremental fields (files, items, timings, etc.) start empty or
    /// `None`; they are populated by subsequent phases.
    pub(crate) fn from_setup(s: SetupContext) -> Self {
        Self {
            cfg: s.cfg,
            command: s.command,
            rootdir: s.rootdir,
            is_tty: s.is_tty,
            use_color: s.use_color,
            base: s.base,
            python_bin: s.python_bin,
            cache: s.cache,
            test_files: Vec::new(),
            conftest_files: Vec::new(),
            session: None,
            collection: CollectionState::Empty,
            execution_results: None,
            collection_profile: None,
        }
    }

    /// Build a minimal error reporter suitable for pre-execution failures.
    ///
    /// Uses the base options with verbose disabled, no JSON/JUnit output, and
    /// no plugin reporters.
    pub(crate) fn make_error_reporter(&self) -> Box<dyn reporter::Reporter> {
        reporter::make_reporter(
            self.base
                .clone()
                .verbosity(config::Verbosity::Normal)
                .build(),
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
    pub(crate) command: config::Command,
    pub(crate) rootdir: camino::Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) python_bin: String,
    pub(crate) base: reporter::ReporterOptsBuilder,
}

fn setup(py: Python<'_>, args: &[String]) -> PyResult<Result<Box<SetupContext>, ExitCode>> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string())
        .chain(args.iter().cloned())
        .collect();

    let (command, use_gitignore) = match config::OxitestCli::resolve(&argv) {
        Ok(pair) => pair,
        Err(e) => {
            // Clap formats this for the user; subscriber may not be initialised yet.
            eprintln!("{}", e);
            return Ok(Err(ExitCode::UsageError));
        }
    };

    // Per-command validation.
    match &command {
        config::Command::Run(a) => {
            if let Err(msg) = a.validate() {
                eprintln!("error: {msg}");
                return Ok(Err(ExitCode::UsageError));
            }
        }
        config::Command::Debug(a) => {
            if let Err(msg) = a.validate() {
                eprintln!("error: {msg}");
                return Ok(Err(ExitCode::UsageError));
            }
        }
        _ => {}
    }

    // Early-exit: `oxitest env` prints environment info and exits.
    if matches!(command, config::Command::Env) {
        println!("{}", env_string(py));
        return Ok(Err(ExitCode::Success));
    }

    // Resolve rootdir from first path argument (varies per command).
    let first_path = match &command {
        config::Command::Run(a) => a.paths.first().map(|p| p.as_path()),
        config::Command::Debug(a) => a.paths.first().map(|p| p.as_path()),
        config::Command::Query(a) => a.paths.first().map(|p| p.as_path()),
        _ => None,
    };
    let rootdir = config::find_rootdir(first_path);

    // Build Config from pyproject.toml, then merge CLI-specific args.
    let cfg = match &command {
        config::Command::Run(args) => config::Config::load(&rootdir).merge_run_args(args),
        config::Command::Debug(args) => config::Config::load(&rootdir).merge_debug_args(args),
        config::Command::Query(args) => config::Config::load(&rootdir).merge_query_args(args),
        config::Command::Env => unreachable!("handled above"),
    };

    // Apply top-level flags that override config values regardless of subcommand.
    let mut cfg = cfg;
    if !use_gitignore {
        cfg.use_gitignore = false;
    }

    let cache = cache::TestCache::load(&rootdir);

    // Debug mode disables the TTY progress bar — pdb needs a clean terminal.
    let is_tty = std::io::stdout().is_terminal() && cfg.debug.is_none();
    let use_color = cfg.color.resolve(is_tty || cfg.debug.is_some());

    let python_bin = py
        .import("sys")?
        .getattr("executable")?
        .extract::<String>()?;

    let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color).tb(cfg.tb.clone());

    // Only set tips/warnings for `run` command.
    let base = if let config::Command::Run(args) = &command {
        base.show_tips(args.tips).show_warnings(args.warnings)
    } else {
        base
    };

    Ok(Ok(Box::new(SetupContext {
        cfg,
        cache,
        command,
        rootdir,
        is_tty,
        use_color,
        python_bin,
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
) -> Result<ExitCode, ExitCode> {
    for phase in pipeline {
        if phase.should_run(ctx) {
            match phase.execute(py, ctx)? {
                PhaseOutcome::Continue => {}
                PhaseOutcome::EarlyExit(code) => return Ok(code),
            }
        }
    }
    Ok(ExitCode::Success)
}

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let setup_ctx = match setup(py, &args)? {
        Err(code) => return Ok(code.as_i32()),
        Ok(ctx) => *ctx,
    };

    let mut ctx = PipelineContext::from_setup(setup_ctx);

    let collector_impl = traits::BridgeCollector;
    let runner_impl = traits::BridgeRunner;
    let parallel_impl = traits::DefaultParallelRunner;

    let pipeline: &[&dyn PipelinePhase] = match &ctx.command {
        config::Command::Run(_) => &[
            &phases::FileCollectionPhase,
            &phases::AffectedPhase,
            &phases::SessionPhase,
            &phases::CollectionPhase {
                collector: &collector_impl,
            },
            &phases::FixtureValidationPhase,
            &phases::StrictPhase,
            &phases::FilterPhase,
            &phases::ExecutionPhase {
                runner: &runner_impl,
                parallel: &parallel_impl,
            },
            &phases::RetryPhase {
                runner: &runner_impl,
            },
            &phases::FinalizePhase,
        ],
        config::Command::Debug(_) => &[
            &phases::FileCollectionPhase,
            &phases::AffectedPhase,
            &phases::SessionPhase,
            &phases::CollectionPhase {
                collector: &collector_impl,
            },
            &phases::FixtureValidationPhase,
            &phases::FilterPhase,
            &phases::ExecutionPhase {
                runner: &runner_impl,
                parallel: &parallel_impl,
            },
            // No RetryPhase in debug mode
            &phases::FinalizePhase,
        ],
        config::Command::Query(ref args) => {
            if query::needs_python(args.resource, args.expression.as_deref()) || args.tree {
                // Full tier: need SessionPhase
                &[
                    &phases::FileCollectionPhase,
                    &phases::AffectedPhase,
                    &phases::SessionPhase,
                    &phases::QueryPhase,
                ]
            } else {
                // Instant tier: no Python needed
                &[
                    &phases::FileCollectionPhase,
                    &phases::AffectedPhase,
                    &phases::QueryPhase,
                ]
            }
        }
        config::Command::Env => unreachable!("handled in setup"),
    };

    match run_pipeline(py, pipeline, &mut ctx) {
        Ok(code) | Err(code) => Ok(code.as_i32()),
    }
}

#[cfg(test)]
impl PipelineContext {
    /// Set the collection state to `Collected` with the given items and raw violations.
    pub(crate) fn with_collected(
        &mut self,
        items: Vec<std::sync::Arc<types::TestItem>>,
        raw_violations: Vec<bridge::RawViolation>,
    ) -> &mut Self {
        self.collection = CollectionState::Collected {
            items,
            raw_violations,
        };
        self
    }

    /// Set the keyword filter in the command variant.
    pub(crate) fn with_keyword(&mut self, keyword: &str) -> &mut Self {
        match &mut self.command {
            config::Command::Run(a) => a.filter.keyword = Some(keyword.to_string()),
            config::Command::Debug(a) => a.filter.keyword = Some(keyword.to_string()),
            _ => {}
        }
        self
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
