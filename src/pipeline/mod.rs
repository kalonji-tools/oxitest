//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.

mod arrange;
pub(crate) mod collection;
pub(crate) mod execution;
mod helpers;
mod transitions;

use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::types::ExitCode;
use crate::{bridge, cache, config, query, reporter, strict, types};
use helpers::env_string;
use pyo3::prelude::*;
use std::io::IsTerminal;

// ─── PipelinePhase enum ─────────────────────────────────────────────────────

/// Runtime pipeline phase — each variant carries the data that was previously
/// held by a separate typestate struct.
pub(crate) enum PipelinePhase {
    /// Initial pipeline state before any work has been done.
    Empty,
    /// Files discovered on disk; test file and conftest paths live in `PipelineShared`.
    FilesCollected,
    /// AST prescan complete; holds per-file `PrescanItem` metadata and module-level markers.
    Prescanned {
        prescan_data: Vec<crate::prescan::PrescanModule>,
        module_markers: std::collections::HashMap<Utf8PathBuf, Vec<String>>,
    },
    /// Prescan metadata filtered; holds only modules that matched filters plus dynamic fallbacks.
    MetadataFiltered { modules_to_import: Vec<Utf8PathBuf> },
    /// Fixture session initialized; conftest files loaded and `FixtureSession` ready.
    SessionReady {
        session: bridge::FixtureSession,
        session_violations: Vec<bridge::RawViolation>,
    },
    /// Test items collected via Python import; holds the full `TestItem` list and any violations.
    Collected {
        session: bridge::FixtureSession,
        items: Vec<Arc<types::TestItem>>,
        raw_violations: Vec<bridge::RawViolation>,
    },
    /// Strict-mode + item-level filtering applied; items are ready for execution.
    Ready {
        session: bridge::FixtureSession,
        clean_items: Vec<Arc<types::TestItem>>,
        violated_items: Vec<Arc<types::TestItem>>,
        all_violations: Vec<strict::StrictViolation>,
        suite_lines: Vec<String>,
    },
    /// All tests executed; holds final items, timings, and the reporter.
    Executed {
        session: bridge::FixtureSession,
        items: Vec<Arc<types::TestItem>>,
        execution_results: ExecutionResults,
    },
}

// ─── ExecutionResults ────────────────────────────────────────────────────────

pub(crate) struct ExecutionResults {
    pub(crate) timings: Vec<types::TestTiming>,
    pub(crate) interrupted: bool,
    pub(crate) reporter: Box<dyn reporter::Reporter>,
}

// ─── Pipeline ────────────────────────────────────────────────────────────────

pub(crate) struct Pipeline {
    pub(crate) shared: PipelineShared,
    pub(crate) phase: PipelinePhase,
}

impl std::fmt::Debug for Pipeline {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Pipeline")
            .field("rootdir", &self.shared.rootdir)
            .finish_non_exhaustive()
    }
}

impl std::ops::Deref for Pipeline {
    type Target = PipelineShared;
    fn deref(&self) -> &PipelineShared {
        &self.shared
    }
}

impl std::ops::DerefMut for Pipeline {
    fn deref_mut(&mut self) -> &mut PipelineShared {
        &mut self.shared
    }
}

impl Pipeline {
    fn into_parts(self) -> (PipelineShared, PipelinePhase) {
        (self.shared, self.phase)
    }
}

/// Data shared across all pipeline phases, accessible via `Deref`/`DerefMut` on `Pipeline`.
pub(crate) struct PipelineShared {
    pub(crate) cfg: config::Config,
    pub(crate) command: config::Command,
    pub(crate) rootdir: Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) python_bin: String,
    /// Sum of AST-derived body weights across all prescan items; `None` if prescan produced no items.
    pub(crate) ast_weight: Option<types::DurationMs>,
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    /// `__fixtures__.py` files registered during collection, sent to every
    /// worker so parallel sessions see what the serial session sees (#1732).
    pub(crate) fixture_modules: Vec<types::FixtureModule>,
    /// Rust-side diagnostics gathered pre-execution (e.g. doctest coverage in
    /// `collect`) that need to reach the reporter once it exists in `execute`.
    pub(crate) pending_diagnostics: Vec<reporter::stats::DiagnosticEntry>,
}

impl PipelineShared {
    /// The `--json` CTRF path this invocation asked for, if any.
    ///
    /// Every early exit needs it, not just the happy path in
    /// [`execute`](Pipeline::execute): `--json PATH` promises that `PATH` exists
    /// once the process is gone, whatever the exit code (#1682).
    pub(in crate::pipeline) fn json_path(&self) -> Option<Utf8PathBuf> {
        match &self.command {
            config::Command::Run(args) => args.json.clone(),
            _ => None,
        }
    }

    /// The `--junit-xml` path this invocation asked for, if any.
    ///
    /// Same promise as [`json_path`](Self::json_path), and needed on the same
    /// early exits: `--junit-xml PATH` means `PATH` exists once the process is
    /// gone, whatever the exit code (#1858).
    pub(in crate::pipeline) fn junit_xml_path(&self) -> Option<Utf8PathBuf> {
        match &self.command {
            config::Command::Run(args) => args.junit_xml.clone(),
            _ => None,
        }
    }

    fn make_error_reporter(&self) -> Box<dyn reporter::Reporter> {
        reporter::make_reporter(
            self.base
                .clone()
                .verbosity(config::Verbosity::Normal)
                .build(),
            self.is_tty,
            self.json_path(),
            self.junit_xml_path(),
            vec![],
        )
    }
}

/// The subset of [`config::Command`] that builds a test pipeline.
///
/// `Inspect`, `Env` and `Completions` are dispatched and returned by `setup`
/// before any pipeline config is loaded, so they have no variant here. That
/// absence is the point: the two callers below used to end their config-loading
/// `match` with `unreachable!("handled above")`, restating in a macro what this
/// type now says (ADR-0011). [`from_command`](Self::from_command) is the single
/// place that decides, and it answers with `None` rather than aborting.
enum PipelineCommand<'a> {
    Run(&'a config::RunArgs),
    Debug(&'a config::DebugArgs),
    Query(&'a config::QueryArgs),
    Fixtures(&'a config::cli::FixturesArgs),
}

impl<'a> PipelineCommand<'a> {
    fn from_command(command: &'a config::Command) -> Option<Self> {
        match command {
            config::Command::Run(args) => Some(Self::Run(args)),
            config::Command::Debug(args) => Some(Self::Debug(args)),
            config::Command::Query(args) => Some(Self::Query(args)),
            config::Command::Fixtures(args) => Some(Self::Fixtures(args)),
            config::Command::Inspect(_)
            | config::Command::Env
            | config::Command::Completions { .. } => None,
        }
    }

    /// The first positional path, which seeds rootdir discovery.
    fn first_path(&self) -> Option<&camino::Utf8Path> {
        let paths = match self {
            Self::Run(args) => &args.paths,
            Self::Debug(args) => &args.paths,
            Self::Query(args) => &args.paths,
            Self::Fixtures(args) => &args.paths,
        };
        paths.first().map(camino::Utf8PathBuf::as_path)
    }

    /// Load the config under `rootdir` and merge this command's flags into it.
    fn load_config(&self, rootdir: &camino::Utf8Path) -> config::Config {
        let cfg = config::Config::load(rootdir);
        match self {
            Self::Run(args) => cfg.merge_run_args(args),
            Self::Debug(args) => cfg.merge_debug_args(args),
            Self::Query(args) => cfg.merge_query_args(args),
            // The deprecated `oxitest fixtures` is `oxitest query fixtures`
            // with every query flag at its default.
            Self::Fixtures(args) => cfg.merge_query_args(&config::QueryArgs {
                resource: query::resource::ResourceKind::Fixtures,
                expression: None,
                fzf: false,
                detail: None,
                jsonl: false,
                count: false,
                tree: false,
                color: None,
                paths: args.paths.clone(),
            }),
        }
    }
}

/// Report a command that reached pipeline code after `setup` should have
/// dispatched it, and give the caller an exit code to return.
///
/// The replacement for three `unreachable!("handled above")` arms: a runner
/// that mis-dispatches must still exit with a code its caller understands, not
/// abort with a backtrace (ADR-0011).
fn report_non_pipeline_command(command: &config::Command) -> ExitCode {
    eprintln!(
        "error: internal dispatch error — `{command:?}` is handled before the test pipeline \
         and must not reach it"
    );
    ExitCode::UsageError
}

/// Validate that no two plugins claim the same CLI prefix.
///
/// Returns `Err` with a human-readable message if duplicate prefixes are found.
fn validate_prefix_uniqueness(extensions: &config::PluginCliExtensions) -> Result<(), String> {
    let mut seen: std::collections::HashMap<&str, &str> = std::collections::HashMap::new();
    for (module, (prefix, _)) in &extensions.plugins {
        if let Some(existing) = seen.get(prefix.as_str()) {
            return Err(format!(
                "plugin CLI prefix conflict: plugins '{existing}' and '{module}' both use prefix '{prefix}'"
            ));
        }
        seen.insert(prefix.as_str(), module.as_str());
    }
    Ok(())
}

/// Extract plugin CLI values directly from argv without using clap.
///
/// This is needed for the plugin-recovery path where the extended clap
/// parser cannot be used (it adds plugin args to the top-level command
/// but subcommand args like `--color` are on the `run` subcommand).
fn extract_plugin_values_from_argv(
    argv: &[String],
    extensions: &config::PluginCliExtensions,
) -> std::collections::HashMap<String, std::collections::HashMap<String, toml::Value>> {
    let mut result: std::collections::HashMap<
        String,
        std::collections::HashMap<String, toml::Value>,
    > = std::collections::HashMap::new();

    let mut i = 0;
    while i < argv.len() {
        let arg = &argv[i];

        for (module, (_, opts)) in &extensions.plugins {
            for opt in opts {
                // Check long flag: --infra-host value or --infra-host=value
                let (base, inline_val) = if let Some(pos) = arg.find('=') {
                    (&arg[..pos], Some(&arg[pos + 1..]))
                } else {
                    (arg.as_str(), None)
                };

                if base == opt.long_flag {
                    let values = result.entry(module.clone()).or_default();
                    match opt.value_type {
                        config::PluginValueType::Bool => {
                            values.insert(opt.field_name.clone(), toml::Value::Boolean(true));
                        }
                        _ => {
                            let val_str = if let Some(v) = inline_val {
                                v.to_string()
                            } else if i + 1 < argv.len() {
                                i += 1;
                                argv[i].clone()
                            } else {
                                continue;
                            };

                            let val = match opt.value_type {
                                config::PluginValueType::Int => val_str
                                    .parse::<i64>()
                                    .map(toml::Value::Integer)
                                    .unwrap_or(toml::Value::String(val_str)),
                                config::PluginValueType::Float => val_str
                                    .parse::<f64>()
                                    .map(toml::Value::Float)
                                    .unwrap_or(toml::Value::String(val_str)),
                                _ => toml::Value::String(val_str),
                            };
                            values.insert(opt.field_name.clone(), val);
                        }
                    }
                }

                // Check short flag: -H value
                if let Some(short) = opt.short_flag
                    && *arg == format!("-{short}")
                {
                    let values = result.entry(module.clone()).or_default();
                    match opt.value_type {
                        config::PluginValueType::Bool => {
                            values.insert(opt.field_name.clone(), toml::Value::Boolean(true));
                        }
                        _ => {
                            if i + 1 < argv.len() {
                                i += 1;
                                let val_str = argv[i].clone();
                                let val = match opt.value_type {
                                    config::PluginValueType::Int => val_str
                                        .parse::<i64>()
                                        .map(toml::Value::Integer)
                                        .unwrap_or(toml::Value::String(val_str)),
                                    config::PluginValueType::Float => val_str
                                        .parse::<f64>()
                                        .map(toml::Value::Float)
                                        .unwrap_or(toml::Value::String(val_str)),
                                    _ => toml::Value::String(val_str),
                                };
                                values.insert(opt.field_name.clone(), val);
                            }
                        }
                    }
                }
            }
        }

        i += 1;
    }

    result
}

/// Strip known plugin CLI flags from argv so that the core parser can succeed.
///
/// Plugin flags (`--infra-host myhost`) are not known to clap Phase 1.
/// This helper removes them (and their values for non-bool types) so that
/// `OxitestCli::resolve` can parse the remaining core args.
fn strip_plugin_flags(argv: &[String], extensions: &config::PluginCliExtensions) -> Vec<String> {
    let known_long: Vec<&str> = extensions
        .plugins
        .values()
        .flat_map(|(_, opts)| opts.iter().map(|o| o.long_flag.as_str()))
        .collect();

    let mut filtered: Vec<String> = Vec::with_capacity(argv.len());
    let mut skip_next = false;

    for (i, arg) in argv.iter().enumerate() {
        if skip_next {
            skip_next = false;
            continue;
        }

        let base_flag = arg.split('=').next().unwrap_or(arg);

        // Long plugin flags
        if known_long.contains(&base_flag) {
            if !arg.contains('=') {
                let is_bool = extensions.plugins.values().any(|(_, opts)| {
                    opts.iter().any(|o| {
                        o.long_flag == base_flag
                            && matches!(o.value_type, config::PluginValueType::Bool)
                    })
                });
                if !is_bool && i + 1 < argv.len() {
                    skip_next = true;
                }
            }
            continue;
        }

        // Short plugin flags (-H, -o, etc.)
        let is_short = arg.starts_with('-')
            && !arg.starts_with("--")
            && arg.len() == 2
            && extensions.plugins.values().any(|(_, opts)| {
                opts.iter()
                    .any(|o| o.short_flag.is_some_and(|s| format!("-{s}") == *arg))
            });
        if is_short {
            let is_bool = extensions.plugins.values().any(|(_, opts)| {
                opts.iter().any(|o| {
                    o.short_flag.is_some_and(|s| format!("-{s}") == *arg)
                        && matches!(o.value_type, config::PluginValueType::Bool)
                })
            });
            if !is_bool && i + 1 < argv.len() {
                skip_next = true;
            }
            continue;
        }

        filtered.push(arg.clone());
    }

    filtered
}

/// Merge plugin CLI values into the config's `plugin_settings` and `plugin_cli_values`.
fn merge_plugin_cli_values(
    cfg: &mut config::Config,
    plugin_values: std::collections::HashMap<
        String,
        std::collections::HashMap<String, toml::Value>,
    >,
) {
    if plugin_values.is_empty() {
        return;
    }
    for (module, values) in &plugin_values {
        let settings_entry = cfg
            .features
            .plugin_settings
            .entry(module.clone())
            .or_insert_with(|| toml::Value::Table(toml::map::Map::new()));
        if let Some(table) = settings_entry.as_table_mut() {
            for (key, value) in values {
                table.insert(key.clone(), value.clone());
            }
        } else {
            tracing::warn!(
                module = %module,
                "plugin_settings entry for module is not a TOML table, skipping CLI merge"
            );
        }
    }
    cfg.features.plugin_cli_values = plugin_values;
}

/// Build a `PipelineShared` from resolved config, command, and rootdir.
fn build_shared(
    py: Python<'_>,
    cfg: config::Config,
    command: config::Command,
    rootdir: Utf8PathBuf,
) -> PyResult<PipelineShared> {
    let cache = cache::TestCache::load(&rootdir);
    let is_tty = std::io::stdout().is_terminal() && cfg.exec.mode.debug_mode().is_none();
    let use_color = cfg
        .output
        .color
        .resolve(is_tty || cfg.exec.mode.debug_mode().is_some());

    crate::prescan::init_stdlib_names(py);

    let python_bin = py
        .import("sys")?
        .getattr("executable")?
        .extract::<String>()?;

    let base =
        reporter::ReporterOptsBuilder::from_config(&cfg, use_color).tb(cfg.output.tb.clone());

    let base = if let config::Command::Run(args) = &command {
        base.show_tips(args.tips).show_warnings(args.warnings)
    } else {
        base
    };

    Ok(PipelineShared {
        cfg,
        cache,
        command,
        rootdir,
        is_tty,
        use_color,
        python_bin,
        base,
        ast_weight: None,
        test_files: vec![],
        conftest_files: vec![],
        fixture_modules: vec![],
        pending_diagnostics: vec![],
    })
}

/// Recovery path: Phase 1 failed (unknown flag or --help) but plugins are
/// configured in pyproject.toml.  Discover plugin CLI extensions, build an
/// extended parser, and re-parse.
fn setup_with_plugin_recovery(
    py: Python<'_>,
    argv: &[String],
    phase1_err: clap::Error,
    rootdir: &Utf8PathBuf,
) -> PyResult<Result<PipelineShared, ExitCode>> {
    let cfg = config::Config::load(rootdir);
    let extensions =
        bridge::discover_plugin_cli(py, &cfg.features.plugins, &cfg.features.plugin_settings)?;

    if extensions.plugins.is_empty() {
        // No plugin CLI extensions — the deferred error is genuine.
        use clap::error::ErrorKind;
        if phase1_err.kind() == ErrorKind::DisplayHelp
            || phase1_err.kind() == ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
        {
            print!("{phase1_err}");
            return Ok(Err(ExitCode::Success));
        }
        eprintln!("{phase1_err}");
        return Ok(Err(ExitCode::UsageError));
    }

    // Validate prefix uniqueness
    if let Err(msg) = validate_prefix_uniqueness(&extensions) {
        eprintln!("error: {msg}");
        return Ok(Err(ExitCode::UsageError));
    }

    // Build extended parser with plugin args
    use clap::CommandFactory;
    let base_cmd = config::OxitestCli::command();
    let mut extended_cmd = config::cli::add_plugin_args(base_cmd, &extensions);

    use clap::error::ErrorKind;
    let is_help = phase1_err.kind() == ErrorKind::DisplayHelp
        || phase1_err.kind() == ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand;

    if is_help {
        // Show help with plugin flags included, then exit.
        let _ = extended_cmd.print_help();
        println!();
        return Ok(Err(ExitCode::Success));
    }

    // Extract plugin CLI values directly from argv (without clap).
    // We can't use the extended parser because plugin args are added to the
    // top-level command while subcommand args (--color, etc.) live on `run`.
    let plugin_values = extract_plugin_values_from_argv(argv, &extensions);

    // Strip plugin flags and re-parse with core parser to get the real Command.
    let filtered_argv = strip_plugin_flags(argv, &extensions);
    let (command, use_gitignore) = match config::OxitestCli::resolve(&filtered_argv) {
        Ok(pair) => pair,
        Err(e) => {
            // Core args are invalid even without plugin flags.
            eprintln!("{e}");
            return Ok(Err(ExitCode::UsageError));
        }
    };

    // Validate command
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

    // Build the real config from the re-parsed command.
    let Some(pipeline_command) = PipelineCommand::from_command(&command) else {
        return Ok(Err(report_non_pipeline_command(&command)));
    };
    let rootdir = config::find_rootdir(pipeline_command.first_path());
    let mut cfg = pipeline_command.load_config(&rootdir);

    if !use_gitignore {
        cfg.paths.use_gitignore = false;
    }

    merge_plugin_cli_values(&mut cfg, plugin_values);

    Ok(Ok(build_shared(py, cfg, command, rootdir)?))
}

fn setup(py: Python<'_>, args: &[String]) -> PyResult<Result<PipelineShared, ExitCode>> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string())
        .chain(args.iter().cloned())
        .collect();

    // ── Phase 1: Core CLI parse ────────────────────────────────────
    //
    // When plugins declare CLI extensions (e.g. `--infra-host`), Phase 1
    // will reject them as unknown flags.  Similarly, `--help` would exit
    // before Phase 2 adds plugin flags to the help text.
    //
    // Strategy: if Phase 1 fails with UnknownArgument or DisplayHelp *and*
    // pyproject.toml has plugins configured, defer the error and proceed to
    // Phase 2 where the extended parser may accept those flags.
    let (command, use_gitignore) = match config::OxitestCli::resolve(&argv) {
        Ok(result) => result,
        Err(e) => {
            use clap::error::ErrorKind;
            let is_version = e.kind() == ErrorKind::DisplayVersion;
            let is_help = e.kind() == ErrorKind::DisplayHelp
                || e.kind() == ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand;
            let is_unknown = e.kind() == ErrorKind::UnknownArgument;

            if is_version {
                print!("{e}");
                return Ok(Err(ExitCode::Success));
            }

            if is_help || is_unknown {
                // Check if plugins are configured — if so, try Phase 2 recovery.
                let first_path = argv.get(1).and_then(|a| {
                    if !a.starts_with('-') {
                        Some(camino::Utf8Path::new(a))
                    } else {
                        None
                    }
                });
                let rootdir = config::find_rootdir(first_path);
                if config::has_plugins_configured(&rootdir) {
                    return setup_with_plugin_recovery(py, &argv, e, &rootdir);
                }
            }

            // No plugins or non-recoverable error — exit normally.
            if is_help {
                print!("{e}");
                return Ok(Err(ExitCode::Success));
            }
            eprintln!("{e}");
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

    if let config::Command::Completions { shell } = &command {
        use clap::CommandFactory;
        let mut cmd = config::OxitestCli::command();
        clap_complete::generate(*shell, &mut cmd, "oxitest", &mut std::io::stdout());
        return Ok(Err(ExitCode::Success));
    }

    if let config::Command::Inspect(args) = command {
        let rootdir = config::find_rootdir(None);
        let cfg = config::Config::load(&rootdir);
        return py.detach(|| match crate::inspect::run(&args, &cfg) {
            Ok(()) => Ok(Err(ExitCode::Success)),
            Err(e) => {
                eprintln!("error: {e}");
                Ok(Err(ExitCode::Failure))
            }
        });
    }

    let Some(pipeline_command) = PipelineCommand::from_command(&command) else {
        return Ok(Err(report_non_pipeline_command(&command)));
    };
    let rootdir = config::find_rootdir(pipeline_command.first_path());
    let mut cfg = pipeline_command.load_config(&rootdir);
    if !use_gitignore {
        cfg.paths.use_gitignore = false;
    }

    // ── Phase 2: Plugin CLI extension discovery ──────────────────────
    if !cfg.features.plugins.is_empty() {
        let extensions =
            bridge::discover_plugin_cli(py, &cfg.features.plugins, &cfg.features.plugin_settings)?;

        if !extensions.plugins.is_empty() {
            // Validate prefix uniqueness across plugins
            if let Err(msg) = validate_prefix_uniqueness(&extensions) {
                eprintln!("error: {msg}");
                return Ok(Err(ExitCode::UsageError));
            }

            // Build extended clap Command with plugin args
            use clap::CommandFactory;
            let base_cmd = config::OxitestCli::command();
            let extended_cmd = config::cli::add_plugin_args(base_cmd, &extensions);

            // Re-parse the original argv with the extended parser
            let matches = match extended_cmd.try_get_matches_from(&argv) {
                Ok(m) => Some(m),
                Err(e) => {
                    use clap::error::ErrorKind;
                    match e.kind() {
                        // Benign: unknown args may belong to core clap parse
                        // which already succeeded in Phase 1.
                        ErrorKind::UnknownArgument
                        | ErrorKind::InvalidSubcommand
                        | ErrorKind::DisplayHelp
                        | ErrorKind::DisplayVersion => {
                            tracing::debug!(
                                error = %e,
                                "Phase 2 plugin CLI parse encountered non-fatal error, skipping"
                            );
                            None
                        }
                        // User-facing errors: missing value, wrong type, etc.
                        _ => {
                            eprintln!("{e}");
                            return Ok(Err(ExitCode::UsageError));
                        }
                    }
                }
            };

            // Extract plugin values from the extended matches
            if let Some(matches) = matches {
                let plugin_values = config::cli::extract_plugin_values(&matches, &extensions);
                merge_plugin_cli_values(&mut cfg, plugin_values);
            }
        }
    }

    Ok(Ok(build_shared(py, cfg, command, rootdir)?))
}

// ─── Command entry points ────────────────────────────────────────────────────

fn run_command(py: Python<'_>, pipeline: Pipeline) -> Result<ExitCode, ExitCode> {
    // Extract coverage config before pipeline consumes it
    let cov_enabled = pipeline.cfg.features.cov;
    let cov_report_format = pipeline.cfg.features.cov_report.clone();

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
    let p = p.prescan()?;
    let p = p.filter_metadata()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.strict_or_skip(py)?;
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

fn debug_command(py: Python<'_>, pipeline: Pipeline) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    let p = p.prescan()?;
    let p = p.filter_metadata()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    // Debug mode skips strict, goes straight to ready
    let p = p.strict_or_skip(py)?;
    let p = p.execute(py)?;
    // No retry in debug mode
    p.finalize(py)
}

fn query_command(
    py: Python<'_>,
    pipeline: Pipeline,
    args: &config::QueryArgs,
    needs_session: bool,
) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    if needs_session {
        let p = p.session(py)?;
        p.query(py, args)
    } else {
        p.query_without_session(py, args)
    }
}

// ─── run() ───────────────────────────────────────────────────────────────────

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let shared = match setup(py, &args)? {
        Err(code) => return Ok(code.as_i32()),
        Ok(shared) => shared,
    };

    let pipeline = Pipeline {
        shared,
        phase: PipelinePhase::Empty,
    };

    let result = match &pipeline.command {
        config::Command::Run(_) => run_command(py, pipeline),
        config::Command::Debug(_) => debug_command(py, pipeline),
        config::Command::Query(args) => {
            let needs_session =
                query::needs_python(args.resource, args.expression.as_deref()) || args.tree;
            // Cloned: `pipeline` is moved below, ending the borrow.
            let args = args.clone();
            query_command(py, pipeline, &args, needs_session)
        }
        // Bound by the arm, not re-matched inside it — the inner
        // `match pipeline.command { … _ => unreachable!() }` it replaced asked
        // the same question twice.
        config::Command::Fixtures(fixtures_args) => {
            eprintln!(
                "Warning: 'oxitest fixtures' is deprecated and will be removed in a future release. \
                Use 'oxitest query fixtures' instead."
            );
            // Rewrite the command as `query fixtures` so the downstream pipeline
            // (which pattern-matches on Command::Query) works without modification.
            let query_args = config::QueryArgs {
                resource: query::resource::ResourceKind::Fixtures,
                expression: None,
                fzf: false,
                detail: None,
                jsonl: false,
                count: false,
                tree: false,
                color: None,
                paths: fixtures_args.paths.clone(),
            };
            let mut pipeline = pipeline;
            pipeline.shared.command = config::Command::Query(query_args.clone());
            query_command(py, pipeline, &query_args, true)
        }
        command @ (config::Command::Inspect(_)
        | config::Command::Env
        | config::Command::Completions { .. }) => Err(report_non_pipeline_command(command)),
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
            Some(s) => {
                format!("  {node_id} - fixture '{bad_name}' not found (did you mean '{s}'?)")
            }
            None => format!("  {node_id} - fixture '{bad_name}' not found"),
        };
        messages.push(msg);
    }
    format!("ERROR collecting tests\n{}", messages.join("\n"))
}

#[cfg(test)]
mod prefix_uniqueness_tests {
    use super::*;

    fn make_extensions(entries: Vec<(&str, &str)>) -> config::PluginCliExtensions {
        let mut plugins = std::collections::HashMap::new();
        for (module, prefix) in entries {
            plugins.insert(module.to_string(), (prefix.to_string(), vec![]));
        }
        config::PluginCliExtensions { plugins }
    }

    #[test]
    fn unique_prefixes_pass_validation() {
        let ext = make_extensions(vec![("plugin_a", "alpha"), ("plugin_b", "beta")]);
        assert!(
            validate_prefix_uniqueness(&ext).is_ok(),
            "distinct prefixes should not conflict"
        );
    }

    #[test]
    fn duplicate_prefixes_return_error_with_both_modules() {
        let ext = make_extensions(vec![("plugin_a", "shared"), ("plugin_b", "shared")]);
        let err =
            validate_prefix_uniqueness(&ext).expect_err("duplicate prefix should be rejected");
        assert!(
            err.contains("plugin_a") && err.contains("plugin_b"),
            "error message should name both conflicting modules, got: {err}"
        );
        assert!(
            err.contains("shared"),
            "error message should name the conflicting prefix, got: {err}"
        );
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
