//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.
//!
//! Phase ordering is enforced at compile time via the typestate pattern:
//! each pipeline stage is a distinct type, and transitions consume one type
//! to produce the next. Illegal ordering is a compile error.

mod arrange;
mod collection;
pub(crate) mod execution;
mod helpers;
pub(crate) mod phases;
pub(crate) mod traits;

use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::types::ExitCode;
use crate::{bridge, cache, config, query, reporter, strict, types};
use helpers::env_string;
use pyo3::prelude::*;
use std::io::IsTerminal;

// ─── State types ─────────────────────────────────────────────────────────────

pub(crate) struct Empty;

pub(crate) struct FilesCollected {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
}

pub(crate) struct SessionReady {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) session_violations: Vec<bridge::RawViolation>,
}

pub(crate) struct Collected {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) items: Vec<Arc<types::TestItem>>,
    pub(crate) raw_violations: Vec<bridge::RawViolation>,
    #[allow(dead_code)]
    pub(crate) collection_profile: Option<collection::CollectionProfile>,
}

pub(crate) struct PreFilter {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) clean_items: Vec<Arc<types::TestItem>>,
    pub(crate) violated_items: Vec<Arc<types::TestItem>>,
    pub(crate) all_violations: Vec<strict::StrictViolation>,
    pub(crate) suite_lines: Vec<String>,
}

pub(crate) struct Ready {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) clean_items: Vec<Arc<types::TestItem>>,
    pub(crate) violated_items: Vec<Arc<types::TestItem>>,
    pub(crate) all_violations: Vec<strict::StrictViolation>,
    pub(crate) suite_lines: Vec<String>,
}

pub(crate) struct Executed {
    #[allow(dead_code)]
    pub(crate) test_files: Vec<Utf8PathBuf>,
    #[allow(dead_code)]
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) items: Vec<Arc<types::TestItem>>,
    pub(crate) execution_results: ExecutionResults,
}

// ─── ExecutionResults ────────────────────────────────────────────────────────

pub(crate) struct ExecutionResults {
    pub(crate) timings: Vec<types::TestTiming>,
    pub(crate) interrupted: bool,
    pub(crate) reporter: Box<dyn reporter::Reporter>,
}

// ─── Pipeline<S> ─────────────────────────────────────────────────────────────

pub(crate) struct Pipeline<S> {
    pub(crate) cfg: config::Config,
    pub(crate) command: config::Command,
    pub(crate) rootdir: Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) python_bin: String,
    pub(crate) state: S,
}

impl<S> std::fmt::Debug for Pipeline<S> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Pipeline")
            .field("rootdir", &self.rootdir)
            .finish_non_exhaustive()
    }
}

impl<S> Pipeline<S> {
    fn make_error_reporter(&self) -> Box<dyn reporter::Reporter> {
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

    fn into_parts(self) -> (PipelineShared, S) {
        (
            PipelineShared {
                cfg: self.cfg,
                command: self.command,
                rootdir: self.rootdir,
                is_tty: self.is_tty,
                use_color: self.use_color,
                base: self.base,
                cache: self.cache,
                python_bin: self.python_bin,
            },
            self.state,
        )
    }
}

pub(crate) struct PipelineShared {
    pub(crate) cfg: config::Config,
    pub(crate) command: config::Command,
    pub(crate) rootdir: Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) python_bin: String,
}

impl PipelineShared {
    fn into_pipeline<T>(self, state: T) -> Pipeline<T> {
        Pipeline {
            cfg: self.cfg,
            command: self.command,
            rootdir: self.rootdir,
            is_tty: self.is_tty,
            use_color: self.use_color,
            base: self.base,
            cache: self.cache,
            python_bin: self.python_bin,
            state,
        }
    }

    fn make_error_reporter(&self) -> Box<dyn reporter::Reporter> {
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

// ─── SetupContext ────────────────────────────────────────────────────────────

#[derive(Debug)]
pub(crate) struct SetupContext {
    pub(crate) cfg: config::Config,
    pub(crate) cache: cache::TestCache,
    pub(crate) command: config::Command,
    pub(crate) rootdir: Utf8PathBuf,
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
            // --version and --help are display requests, not errors.
            if e.kind() == clap::error::ErrorKind::DisplayVersion
                || e.kind() == clap::error::ErrorKind::DisplayHelp
                || e.kind() == clap::error::ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
            {
                print!("{}", e);
                return Ok(Err(ExitCode::Success));
            }
            eprintln!("{}", e);
            return Ok(Err(ExitCode::UsageError));
        }
    };

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

    if matches!(command, config::Command::Env) {
        println!("{}", env_string(py));
        return Ok(Err(ExitCode::Success));
    }

    let first_path = match &command {
        config::Command::Run(a) => a.paths.first().map(|p| p.as_path()),
        config::Command::Debug(a) => a.paths.first().map(|p| p.as_path()),
        config::Command::Query(a) => a.paths.first().map(|p| p.as_path()),
        _ => None,
    };
    let rootdir = config::find_rootdir(first_path);

    let cfg = match &command {
        config::Command::Run(args) => config::Config::load(&rootdir).merge_run_args(args),
        config::Command::Debug(args) => config::Config::load(&rootdir).merge_debug_args(args),
        config::Command::Query(args) => config::Config::load(&rootdir).merge_query_args(args),
        config::Command::Env => unreachable!("handled above"),
    };

    let mut cfg = cfg;
    if !use_gitignore {
        cfg.use_gitignore = false;
    }

    let cache = cache::TestCache::load(&rootdir);

    let is_tty = std::io::stdout().is_terminal() && cfg.debug.is_none();
    let use_color = cfg.color.resolve(is_tty || cfg.debug.is_some());

    let python_bin = py
        .import("sys")?
        .getattr("executable")?
        .extract::<String>()?;

    let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color).tb(cfg.tb.clone());

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

// ─── Command entry points ────────────────────────────────────────────────────

fn run_command(py: Python<'_>, pipeline: Pipeline<Empty>) -> Result<ExitCode, ExitCode> {
    // Extract coverage config before pipeline consumes it
    let cov_enabled = pipeline.cfg.cov;
    let cov_report_format = pipeline.cfg.cov_report.clone();

    // Start coverage before any test execution
    let cov_provider = if cov_enabled {
        match bridge::start_coverage(py) {
            Ok(provider) => Some(provider),
            Err(e) => {
                eprintln!("error: {e}");
                return Err(ExitCode::UsageError);
            }
        }
    } else {
        None
    };

    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.strict_or_skip(py)?;
    let p = p.filter(py)?;
    let p = p.execute(py)?;
    let p = p.retry(py)?;
    let result = p.finalize(py);

    // Stop coverage and generate report after all tests
    if let Some(ref provider) = cov_provider {
        let format = cov_report_format
            .as_ref()
            .map(|f| f.as_str())
            .unwrap_or("term");
        if let Err(e) = bridge::stop_and_report_coverage(py, provider, format) {
            eprintln!("warning: coverage report failed: {e}");
        }
    }

    result
}

fn debug_command(py: Python<'_>, pipeline: Pipeline<Empty>) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    // Debug mode skips strict, goes straight to filter
    let p = p.strict_or_skip(py)?;
    let p = p.filter(py)?;
    let p = p.execute(py)?;
    // No retry in debug mode
    p.finalize(py)
}

fn query_command(
    py: Python<'_>,
    pipeline: Pipeline<Empty>,
    needs_session: bool,
) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    if needs_session {
        let p = p.session(py)?;
        p.query(py)
    } else {
        p.query_without_session(py)
    }
}

// ─── run() ───────────────────────────────────────────────────────────────────

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let setup_ctx = match setup(py, &args)? {
        Err(code) => return Ok(code.as_i32()),
        Ok(ctx) => *ctx,
    };

    let pipeline = Pipeline {
        cfg: setup_ctx.cfg,
        command: setup_ctx.command,
        rootdir: setup_ctx.rootdir,
        is_tty: setup_ctx.is_tty,
        use_color: setup_ctx.use_color,
        base: setup_ctx.base,
        cache: setup_ctx.cache,
        python_bin: setup_ctx.python_bin,
        state: Empty,
    };

    let result = match &pipeline.command {
        config::Command::Run(_) => run_command(py, pipeline),
        config::Command::Debug(_) => debug_command(py, pipeline),
        config::Command::Query(ref args) => {
            let needs_session =
                query::needs_python(args.resource, args.expression.as_deref()) || args.tree;
            query_command(py, pipeline, needs_session)
        }
        config::Command::Env => unreachable!("handled in setup"),
    };

    match result {
        Ok(code) | Err(code) => Ok(code.as_i32()),
    }
}

// ─── format_fixture_errors (kept public for tests) ──────────────────────────

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

#[cfg(test)]
#[path = "../pipeline_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "../pipeline_phase_tests.rs"]
mod phase_tests;

#[cfg(test)]
#[path = "../pipeline_contract_tests.rs"]
mod contract_tests;
