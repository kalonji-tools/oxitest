use camino::Utf8PathBuf;
use clap::Parser;

use super::{
    ColorMode, FailedMode, KeepTmpMode, ScheduleStrategy, StrictMode, TbStyle, Verbosity,
    WorkerCount,
};

// ── DebugMode ────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum DebugMode {
    /// Drop into pdb on first test failure
    PostMortem,
    /// Drop into pdb before every test
    Always,
}

// ── Shared flag groups ───────────────────────────────────────────────────────

/// Keyword and marker filtering flags, shared by run/debug/list.
#[derive(clap::Args, Debug, Clone, Default)]
pub struct FilteringArgs {
    /// Only run tests matching the keyword expression
    #[arg(short = 'k', value_name = "EXPR", help_heading = "Filtering")]
    pub keyword: Option<String>,

    /// Only run tests matching the marker expression
    #[arg(
        short = 'm',
        long = "marker",
        value_name = "EXPR",
        help_heading = "Filtering"
    )]
    pub marker: Option<String>,

    /// Run only tests affected by git changes (default ref from affected_base config, or HEAD)
    #[arg(
        long,
        value_name = "REF",
        default_missing_value = "",
        num_args = 0..=1,
        require_equals = true,
        help_heading = "Filtering",
    )]
    pub affected: Option<String>,
}

/// Failed-test filtering flags, shared by run/debug.
#[derive(clap::Args, Debug, Clone, Default)]
pub struct FailedFilterArgs {
    /// Failed-test mode: "only" runs just failures, "first" runs failures before the rest
    #[arg(long, value_enum, value_name = "MODE", help_heading = "Filtering")]
    pub failed: Option<FailedMode>,

    /// Shorthand for --failed=only (run only previously-failed tests)
    #[arg(long, conflicts_with = "failed", help_heading = "Filtering")]
    pub lf: bool,

    /// Shorthand for --failed=first (run failed tests first, then the rest)
    #[arg(long, conflicts_with_all = ["failed", "lf"], help_heading = "Filtering")]
    pub ff: bool,
}

impl FailedFilterArgs {
    /// Resolve the three flags into a single `Option<FailedMode>`.
    pub fn resolve(&self) -> Option<FailedMode> {
        if self.lf {
            Some(FailedMode::Only)
        } else if self.ff {
            Some(FailedMode::First)
        } else {
            self.failed
        }
    }
}

/// Verbosity flags, shared across subcommands.
#[derive(clap::Args, Debug, Clone, Default)]
pub struct VerbosityArgs {
    /// Short-form verbosity (-v = detailed, -vv = full)
    #[arg(short = 'v', action = clap::ArgAction::Count, help_heading = "Output")]
    pub verbose_count: u8,

    /// Explicit verbosity level (--verbose, --verbose=detailed, --verbose=full)
    #[arg(
        long = "verbose",
        value_enum,
        value_name = "LEVEL",
        default_missing_value = "detailed",
        num_args = 0..=1,
        require_equals = true,
        help_heading = "Output",
    )]
    pub verbose: Option<Verbosity>,
}

impl VerbosityArgs {
    /// Check that short and long verbosity flags are not mixed.
    pub fn validate(&self) -> Result<(), String> {
        if self.verbose_count > 0 && self.verbose.is_some() {
            return Err("use -v/-vv or --verbose=LEVEL, not both.".to_string());
        }
        Ok(())
    }

    /// Resolve the two flags into a single `Option<Verbosity>`.
    pub fn resolve(&self) -> Option<Verbosity> {
        if let Some(level) = self.verbose {
            Some(level)
        } else if self.verbose_count >= 2 {
            Some(Verbosity::Full)
        } else if self.verbose_count == 1 {
            Some(Verbosity::Detailed)
        } else {
            None
        }
    }
}

// ── Subcommand argument structs ──────────────────────────────────────────────

/// Arguments for `oxitest run` (the default subcommand).
#[derive(clap::Args, Debug, Clone)]
pub struct RunArgs {
    /// Paths to test files or directories (default: current directory)
    pub paths: Vec<Utf8PathBuf>,

    #[command(flatten)]
    pub filter: FilteringArgs,

    #[command(flatten)]
    pub failed_filter: FailedFilterArgs,

    #[command(flatten)]
    pub verbosity: VerbosityArgs,

    // ── Execution ────────────────────────────────────────────────────
    /// Exit immediately after the first failure
    #[arg(short = 'x', help_heading = "Execution")]
    pub exitfirst: bool,

    /// Exit after N failures (0 = no limit)
    #[arg(long, value_name = "NUM", help_heading = "Execution")]
    pub maxfail: Option<usize>,

    /// Run tests serially (single process, no workers)
    #[arg(long, help_heading = "Execution")]
    pub serial: bool,

    /// Number of parallel worker processes ("auto" or a positive integer)
    #[arg(
        short = 'n',
        long,
        value_name = "N",
        conflicts_with = "serial",
        help_heading = "Execution"
    )]
    pub workers: Option<WorkerCount>,

    /// Group scheduling strategy for parallel runs
    #[arg(long, value_enum, help_heading = "Execution")]
    pub schedule: Option<ScheduleStrategy>,

    /// Per-test timeout in seconds (overrides pyproject.toml timeout)
    #[arg(long, value_name = "SECS", help_heading = "Execution")]
    pub timeout: Option<u64>,

    /// Retry failed tests up to N times
    #[arg(long, value_name = "N", help_heading = "Execution")]
    pub retries: Option<usize>,

    /// Enforce strict conventions (bare-assert, dict-parametrize, missing mark reason,
    /// marker-without-description). Use `--strict=MODE` with `=` (e.g. `--strict=enforce`).
    /// Bare `--strict` defaults to abort mode.
    #[arg(
        long,
        value_enum,
        value_name = "MODE",
        default_missing_value = "abort",
        num_args = 0..=1,
        require_equals = true,
        help_heading = "Execution",
    )]
    pub strict: Option<StrictMode>,

    // ── Output ───────────────────────────────────────────────────────
    /// Quiet output (minimal detail)
    #[arg(short = 'q', long, help_heading = "Output")]
    pub quiet: bool,

    /// Traceback style: detail (default), line, no
    #[arg(long, value_enum, help_heading = "Output")]
    pub tb: Option<TbStyle>,

    /// Show local variables in diagnostic frames (requires --tb=detail)
    #[arg(long, help_heading = "Output")]
    pub show_locals: bool,

    /// Show oxitest internal frames in trace (requires --tb=detail)
    #[arg(long, help_heading = "Output")]
    pub show_internals: bool,

    /// Expand assertions-without-messages tip list
    #[arg(long, help_heading = "Output")]
    pub tips: bool,

    /// Expand captured Python warnings list
    #[arg(long, help_heading = "Output")]
    pub warnings: bool,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,

    /// Show the N slowest tests at end of run
    #[arg(long, value_name = "N", help_heading = "Output")]
    pub durations: Option<usize>,

    /// Preserve TempDir contents instead of cleaning up.
    /// Bare `--keep-tmp` defaults to failed mode (preserve on test failure only).
    /// Use `--keep-tmp=MODE` with `=` (e.g. `--keep-tmp=always`).
    #[arg(
        long,
        value_enum,
        value_name = "MODE",
        default_missing_value = "failed",
        num_args = 0..=1,
        require_equals = true,
        help_heading = "Output",
    )]
    pub keep_tmp: Option<KeepTmpMode>,

    // ── Reports ──────────────────────────────────────────────────────
    /// Write CTRF JSON results to PATH
    #[arg(long, value_name = "PATH", help_heading = "Reports")]
    pub json: Option<Utf8PathBuf>,

    /// Write JUnit XML results to PATH
    #[arg(long, value_name = "PATH", help_heading = "Reports")]
    pub junit_xml: Option<Utf8PathBuf>,
}

impl RunArgs {
    /// Check for conflicting flag combinations.
    ///
    /// Returns `Err` with a human-readable message if flags contradict each other.
    pub fn validate(&self) -> Result<(), String> {
        if self.exitfirst && self.maxfail.is_some() {
            return Err(
                "-x and --maxfail both control when to stop after failures. Use one or the other."
                    .to_string(),
            );
        }

        if self.schedule.is_some() && self.serial {
            return Err(
                "--schedule controls parallel worker ordering, which has no effect with --serial."
                    .to_string(),
            );
        }

        // ── --show-locals / --show-internals require --tb=detail ──
        if self.show_locals {
            if let Some(ref tb) = self.tb {
                if !matches!(tb, TbStyle::Detail) {
                    return Err("--show-locals requires --tb=detail.".to_string());
                }
            }
        }
        if self.show_internals {
            if let Some(ref tb) = self.tb {
                if !matches!(tb, TbStyle::Detail) {
                    return Err("--show-internals requires --tb=detail.".to_string());
                }
            }
        }

        self.verbosity.validate()
    }
}

/// Arguments for `oxitest debug`.
#[derive(clap::Args, Debug, Clone)]
pub struct DebugArgs {
    /// Paths to test files or directories (default: current directory)
    pub paths: Vec<Utf8PathBuf>,

    /// Start the debugger before every test (not just on failure)
    #[arg(long)]
    pub always: bool,

    #[command(flatten)]
    pub filter: FilteringArgs,

    #[command(flatten)]
    pub failed_filter: FailedFilterArgs,

    #[command(flatten)]
    pub verbosity: VerbosityArgs,

    // ── Output ───────────────────────────────────────────────────────
    /// Quiet output (minimal detail)
    #[arg(short = 'q', long, help_heading = "Output")]
    pub quiet: bool,

    /// Traceback style: detail (default), line, no
    #[arg(long, value_enum, help_heading = "Output")]
    pub tb: Option<TbStyle>,

    /// Show local variables in diagnostic frames (requires --tb=detail)
    #[arg(long, help_heading = "Output")]
    pub show_locals: bool,

    /// Preserve TempDir contents instead of cleaning up.
    /// Bare `--keep-tmp` defaults to failed mode (preserve on test failure only).
    /// Use `--keep-tmp=MODE` with `=` (e.g. `--keep-tmp=always`).
    #[arg(
        long,
        value_enum,
        value_name = "MODE",
        default_missing_value = "failed",
        num_args = 0..=1,
        require_equals = true,
        help_heading = "Output",
    )]
    pub keep_tmp: Option<KeepTmpMode>,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,
}

impl DebugArgs {
    /// Return the debug mode based on the `--always` flag.
    pub fn mode(&self) -> DebugMode {
        if self.always {
            DebugMode::Always
        } else {
            DebugMode::PostMortem
        }
    }

    /// Check for conflicting flag combinations.
    pub fn validate(&self) -> Result<(), String> {
        self.verbosity.validate()
    }
}

/// Arguments for `oxitest list`.
#[derive(clap::Args, Debug, Clone)]
pub struct ListArgs {
    /// Paths to test files or directories (default: current directory)
    pub paths: Vec<Utf8PathBuf>,

    #[command(flatten)]
    pub filter: FilteringArgs,

    #[command(flatten)]
    pub verbosity: VerbosityArgs,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,
}

/// Arguments for `oxitest fixtures`.
#[derive(clap::Args, Debug, Clone)]
pub struct FixturesArgs {
    /// Show fixture dependency tree
    #[arg(long)]
    pub tree: bool,

    #[command(flatten)]
    pub verbosity: VerbosityArgs,

    /// Quiet output (minimal detail)
    #[arg(short = 'q', long, help_heading = "Output")]
    pub quiet: bool,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,
}

/// Arguments for `oxitest plugins`.
#[derive(clap::Args, Debug, Clone)]
pub struct PluginsArgs {
    #[command(flatten)]
    pub verbosity: VerbosityArgs,

    /// Quiet output (minimal detail)
    #[arg(short = 'q', long, help_heading = "Output")]
    pub quiet: bool,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,
}

// ── Command enum ─────────────────────────────────────────────────────────────

/// Available subcommands.
#[derive(clap::Subcommand, Debug, Clone)]
pub enum Command {
    /// Run tests (default when no subcommand is given)
    Run(RunArgs),
    /// Interactive debugger session
    Debug(DebugArgs),
    /// List collected tests without executing
    List(ListArgs),
    /// Inspect registered fixtures
    Fixtures(FixturesArgs),
    /// List registered plugins and their protocols
    Plugins(PluginsArgs),
    /// Print environment information (version, Python, rustc, OS)
    Env,
}

// ── Top-level parser ─────────────────────────────────────────────────────────

/// oxitest — a fast Python test runner.
#[derive(Parser, Debug)]
#[command(name = "oxitest", about = "A fast Python test runner")]
pub struct OxitestCli {
    #[command(subcommand)]
    pub command: Option<Command>,
}

impl OxitestCli {
    /// Parse arguments, falling back to implicit `run` when no subcommand is given.
    ///
    /// Tries parsing with subcommands first. On failure (e.g. `oxitest tests/ -k foo`
    /// without an explicit `run`), falls back to parsing all args as `RunArgs`.
    pub fn resolve(args: &[String]) -> Result<Command, clap::Error> {
        // First, try normal parsing with subcommands.
        match OxitestCli::try_parse_from(args) {
            Ok(cli) => {
                if let Some(cmd) = cli.command {
                    return Ok(cmd);
                }
                // No subcommand given (bare `oxitest`) — treat as `run` with no args.
                let run_args = vec![args[0].clone(), "run".to_string()];
                // (no extra args to forward)
                let cli = OxitestCli::try_parse_from(&run_args)?;
                Ok(cli.command.unwrap())
            }
            Err(e) => {
                // If the initial parse failed, try inserting "run" after the binary name
                // to handle implicit default subcommand (e.g. `oxitest tests/ -k foo`).
                if args.len() > 1 {
                    let mut run_args = Vec::with_capacity(args.len() + 1);
                    run_args.push(args[0].clone());
                    run_args.push("run".to_string());
                    run_args.extend_from_slice(&args[1..]);
                    match OxitestCli::try_parse_from(&run_args) {
                        Ok(cli) => Ok(cli.command.unwrap()),
                        Err(_) => Err(e), // Return the original error for better UX
                    }
                } else {
                    Err(e)
                }
            }
        }
    }
}

// ── Test helpers ─────────────────────────────────────────────────────────────

#[cfg(test)]
impl RunArgs {
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "run"])
            .expect("default RunArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::Run(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

#[cfg(test)]
impl DebugArgs {
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "debug"])
            .expect("default DebugArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::Debug(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

#[cfg(test)]
impl ListArgs {
    #[allow(dead_code)]
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "list"])
            .expect("default ListArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::List(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

#[cfg(test)]
impl FixturesArgs {
    #[allow(dead_code)]
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "fixtures"])
            .expect("default FixturesArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::Fixtures(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

#[cfg(test)]
impl PluginsArgs {
    #[allow(dead_code)]
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "plugins"])
            .expect("default PluginsArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::Plugins(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{FailedMode, Verbosity};

    /// Convert a fixed-size array of `&str` into `Vec<String>`.
    fn s<const N: usize>(args: [&str; N]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
    }

    // ── OxitestCli::resolve ───────────────────────────────────────────────────

    #[test]
    fn bare_oxitest_defaults_to_run() {
        let cmd = OxitestCli::resolve(&s(["oxitest"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn explicit_run_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn implicit_run_with_path() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "tests/"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
    }

    #[test]
    fn implicit_run_with_flags() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "-k", "foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.filter.keyword.as_deref(), Some("foo"));
    }

    #[test]
    fn implicit_run_with_path_and_flags() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "tests/", "-k", "foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
        assert_eq!(args.filter.keyword.as_deref(), Some("foo"));
    }

    #[test]
    fn debug_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        assert!(matches!(cmd, Command::Debug(_)));
    }

    #[test]
    fn debug_with_always() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "debug", "--always"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert!(args.always);
    }

    #[test]
    fn debug_default_is_post_mortem() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert_eq!(args.mode(), DebugMode::PostMortem);
    }

    #[test]
    fn list_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "list"])).unwrap();
        assert!(matches!(cmd, Command::List(_)));
    }

    #[test]
    fn list_with_keyword() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "list", "-k", "foo"])).unwrap();
        let Command::List(args) = cmd else {
            panic!("expected Command::List");
        };
        assert_eq!(args.filter.keyword.as_deref(), Some("foo"));
    }

    #[test]
    fn list_with_marker() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "list", "-m", "slow"])).unwrap();
        let Command::List(args) = cmd else {
            panic!("expected Command::List");
        };
        assert_eq!(args.filter.marker.as_deref(), Some("slow"));
    }

    #[test]
    fn fixtures_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "fixtures"])).unwrap();
        assert!(matches!(cmd, Command::Fixtures(_)));
    }

    #[test]
    fn fixtures_with_tree() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "fixtures", "--tree"])).unwrap();
        let Command::Fixtures(args) = cmd else {
            panic!("expected Command::Fixtures");
        };
        assert!(args.tree);
    }

    #[test]
    fn fixtures_with_quiet() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "fixtures", "-q"])).unwrap();
        let Command::Fixtures(args) = cmd else {
            panic!("expected Command::Fixtures");
        };
        assert!(args.quiet);
    }

    #[test]
    fn plugins_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "plugins"])).unwrap();
        assert!(matches!(cmd, Command::Plugins(_)));
    }

    #[test]
    fn plugins_with_verbose() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "plugins", "-v"])).unwrap();
        let Command::Plugins(args) = cmd else {
            panic!("expected Command::Plugins");
        };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Detailed));
    }

    #[test]
    fn plugins_with_quiet() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "plugins", "-q"])).unwrap();
        let Command::Plugins(args) = cmd else {
            panic!("expected Command::Plugins");
        };
        assert!(args.quiet);
    }

    #[test]
    fn env_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "env"])).unwrap();
        assert!(matches!(cmd, Command::Env));
    }

    // ── FailedFilterArgs::resolve ─────────────────────────────────────────────

    #[test]
    fn lf_resolves_to_failed_only() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--lf"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    #[test]
    fn ff_resolves_to_failed_first() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--ff"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::First));
    }

    #[test]
    fn failed_only_canonical() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--failed", "only"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    #[test]
    fn failed_first_canonical() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--failed", "first"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::First));
    }

    #[test]
    fn lf_conflicts_with_failed() {
        let result = OxitestCli::resolve(&s(["oxitest", "run", "--lf", "--failed", "only"]));
        assert!(result.is_err());
    }

    #[test]
    fn ff_conflicts_with_lf() {
        let result = OxitestCli::resolve(&s(["oxitest", "run", "--ff", "--lf"]));
        assert!(result.is_err());
    }

    #[test]
    fn no_failed_flags_resolves_none() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), None);
    }

    #[test]
    fn lf_in_debug_subcommand() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "debug", "--lf"])).unwrap();
        let Command::Debug(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    // ── RunArgs::validate ─────────────────────────────────────────────────────

    #[test]
    fn exitfirst_conflicts_with_maxfail() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "-x", "--maxfail", "5"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn schedule_conflicts_with_serial() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--serial", "--schedule", "random"]))
            .unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_locals_with_tb_no_conflicts() {
        let cmd =
            OxitestCli::resolve(&s(["oxitest", "run", "--show-locals", "--tb", "no"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_internals_with_tb_line_conflicts() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--show-internals", "--tb", "line"]))
            .unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_locals_alone_is_valid() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--show-locals"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        // default tb is None (→ detail), so no conflict
        assert!(args.validate().is_ok());
    }

    #[test]
    fn no_flags_is_valid() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_ok());
    }

    #[test]
    fn v_and_verbose_conflict() {
        // clap accepts -v and --verbose=full at parse time; validate() catches the mix.
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "-v", "--verbose=full"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    // ── VerbosityArgs ─────────────────────────────────────────────────────────

    #[test]
    fn short_v_resolves_detailed() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "-v"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Detailed));
    }

    #[test]
    fn short_vv_resolves_full() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "-vv"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Full));
    }

    #[test]
    fn long_verbose_bare_resolves_detailed() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--verbose"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Detailed));
    }

    #[test]
    fn long_verbose_full() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run", "--verbose=full"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Full));
    }

    #[test]
    fn no_verbose_resolves_none() {
        let cmd = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), None);
    }
}
