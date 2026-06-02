use camino::Utf8PathBuf;
use clap::Parser;

use super::{
    ColorMode, FailedMode, KeepTmpMode, ScheduleStrategy, StrictMode, TbStyle, Verbosity,
    WorkerCount,
};
use crate::query::resource::ResourceKind;

// ── DebugMode ────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum DebugMode {
    /// Drop into pdb on first test failure
    PostMortem,
    /// Drop into pdb before every test
    Always,
}

// ── QueryFormat ──────────────────────────────────────────────────────────────

/// Output format for the query subcommand.
#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum QueryFormat {
    /// JSON lines (one object per entry)
    Jsonl,
}

// ── Shared flag groups ───────────────────────────────────────────────────────

/// Keyword and marker filtering flags, shared by run/debug.
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

    /// Show collection timing breakdown (prescan, Python import, fixture resolution)
    #[arg(long, help_heading = "Output")]
    pub collection_profile: bool,

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

/// Arguments for `oxitest query`.
#[derive(clap::Args, Debug, Clone)]
#[command(after_help = "\
RESOURCES:
  tests      Test functions (instant)
  fixtures   Registered fixtures (requires Python)
  marks      Mark decorators (instant)
  helpers    Conftest helper functions (instant)
  plugins    Registered plugins (requires Python)

PREDICATES:
  name()     Primary identifier match         [all resources]
  source()   Source file path match           [all resources]
  mark()     Has matching mark                [tests]
  shared()   Is shared fixture                [fixtures]
  autouse()  Is autouse fixture               [fixtures]
  async()    Is async                         [tests, fixtures]
  protocol() Implements protocol              [plugins]

MATCHERS:
  name(foo)       Contains 'foo' (default)
  name(=exact)    Exact match
  name(~partial)  Explicit contains
  name(/re.*/)    Regex match

EXAMPLES:
  oxitest query tests                        List all tests
  oxitest query tests -E 'mark(slow)'        Tests marked @slow
  oxitest query tests -E 'async() & !mark(skip)'
  oxitest query fixtures -E 'shared()'
  oxitest query tests --fzf                  Interactive fuzzy finder
  oxitest query tests --inspect test_foo     Show details for test_foo
  oxitest query tests --format jsonl         JSON lines output
  oxitest query tests --count                Just the count\
")]
pub struct QueryArgs {
    /// Resource type to query
    pub resource: ResourceKind,

    /// Filter expression (DSL)
    #[arg(short = 'E', value_name = "EXPR")]
    pub expression: Option<String>,

    /// Interactive fuzzy finder
    #[arg(long)]
    pub fzf: bool,

    /// Inspect a single item by identifier
    #[arg(long, value_name = "ID")]
    pub inspect: Option<String>,

    /// Output format
    #[arg(long, value_enum)]
    pub format: Option<QueryFormat>,

    /// Show only the count
    #[arg(long)]
    pub count: bool,

    /// Show fixture dependency tree (fixtures only)
    #[arg(long)]
    pub tree: bool,

    /// Paths to test files or directories
    pub paths: Vec<Utf8PathBuf>,
}

// ── Command enum ─────────────────────────────────────────────────────────────

/// Available subcommands.
#[derive(clap::Subcommand, Debug, Clone)]
pub enum Command {
    /// Run tests (default when no subcommand is given)
    Run(RunArgs),
    /// Interactive debugger session
    Debug(DebugArgs),
    /// Query tests, fixtures, marks, helpers, or plugins
    Query(QueryArgs),
    /// Print environment information (version, Python, rustc, OS)
    Env,
}

// ── Top-level parser ─────────────────────────────────────────────────────────

/// oxitest — a fast Python test runner.
#[derive(Parser, Debug)]
#[command(name = "oxitest", about = "A fast Python test runner")]
pub struct OxitestCli {
    /// Disable .gitignore-aware file filtering
    #[arg(long)]
    pub no_use_gitignore: bool,

    #[command(subcommand)]
    pub command: Option<Command>,
}

impl OxitestCli {
    /// Parse arguments, falling back to implicit `run` when no subcommand is given.
    ///
    /// Returns `(Command, use_gitignore)` where `use_gitignore` is `true` unless
    /// `--no-use-gitignore` was passed.
    ///
    /// Tries parsing with subcommands first. On failure (e.g. `oxitest tests/ -k foo`
    /// without an explicit `run`), falls back to parsing all args as `RunArgs`.
    pub fn resolve(args: &[String]) -> Result<(Command, bool), clap::Error> {
        // First, try normal parsing with subcommands.
        match OxitestCli::try_parse_from(args) {
            Ok(cli) => {
                let use_gitignore = !cli.no_use_gitignore;
                if let Some(cmd) = cli.command {
                    return Ok((cmd, use_gitignore));
                }
                // No subcommand given (bare `oxitest`) — treat as `run` with no args.
                let run_args = vec![args[0].clone(), "run".to_string()];
                // (no extra args to forward)
                let cli = OxitestCli::try_parse_from(&run_args)?;
                Ok((cli.command.unwrap(), use_gitignore))
            }
            Err(e) => {
                // If the initial parse failed, try inserting "run" to handle implicit default
                // subcommand (e.g. `oxitest tests/ -k foo` or
                // `oxitest --no-use-gitignore tests/`).
                //
                // Top-level (global) flags like `--no-use-gitignore` can appear anywhere in
                // the args. We extract them, place them before the inserted "run" subcommand,
                // and pass the rest as subcommand args.
                if args.len() > 1 {
                    let global_flags: &[&str] = &["--no-use-gitignore"];
                    let mut run_args = Vec::with_capacity(args.len() + 1);
                    run_args.push(args[0].clone());
                    // Extract global flags from anywhere in args[1..]
                    for arg in &args[1..] {
                        if global_flags.contains(&arg.as_str()) {
                            run_args.push(arg.clone());
                        }
                    }
                    run_args.push("run".to_string());
                    // Then the rest (non-global args) in original order
                    for arg in &args[1..] {
                        if !global_flags.contains(&arg.as_str()) {
                            run_args.push(arg.clone());
                        }
                    }
                    match OxitestCli::try_parse_from(&run_args) {
                        Ok(cli) => Ok((cli.command.unwrap(), !cli.no_use_gitignore)),
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
impl QueryArgs {
    #[allow(dead_code)]
    pub fn default_for_test() -> Self {
        OxitestCli::try_parse_from(["oxitest", "query", "tests"])
            .expect("default QueryArgs must parse")
            .command
            .map(|cmd| match cmd {
                Command::Query(args) => args,
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
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn explicit_run_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn implicit_run_with_path() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "tests/"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
    }

    #[test]
    fn implicit_run_with_flags() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "-k", "foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.filter.keyword.as_deref(), Some("foo"));
    }

    #[test]
    fn implicit_run_with_path_and_flags() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "tests/", "-k", "foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
        assert_eq!(args.filter.keyword.as_deref(), Some("foo"));
    }

    #[test]
    fn debug_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        assert!(matches!(cmd, Command::Debug(_)));
    }

    #[test]
    fn debug_with_always() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug", "--always"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert!(args.always);
    }

    #[test]
    fn debug_default_is_post_mortem() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert_eq!(args.mode(), DebugMode::PostMortem);
    }

    #[test]
    fn env_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "env"])).unwrap();
        assert!(matches!(cmd, Command::Env));
    }

    // ── Query subcommand tests ────────────────────────────────────────────────

    #[test]
    fn query_tests_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests"])).unwrap();
        assert!(matches!(cmd, Command::Query(_)));
    }

    #[test]
    fn query_fixtures_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "fixtures"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.resource, ResourceKind::Fixtures);
    }

    #[test]
    fn query_with_expression() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "query", "tests", "-E", "name(~foo)"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.expression.as_deref(), Some("name(~foo)"));
    }

    #[test]
    fn query_with_fzf() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests", "--fzf"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.fzf);
    }

    #[test]
    fn query_with_inspect() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "query", "tests", "--inspect", "test_foo"]))
                .unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.inspect.as_deref(), Some("test_foo"));
    }

    #[test]
    fn query_with_format_jsonl() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "query", "tests", "--format", "jsonl"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.format, Some(QueryFormat::Jsonl));
    }

    #[test]
    fn query_with_count() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests", "--count"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.count);
    }

    #[test]
    fn query_with_tree() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "fixtures", "--tree"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.tree);
    }

    // ── FailedFilterArgs::resolve ─────────────────────────────────────────────

    #[test]
    fn lf_resolves_to_failed_only() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--lf"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    #[test]
    fn ff_resolves_to_failed_first() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--ff"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::First));
    }

    #[test]
    fn failed_only_canonical() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--failed", "only"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    #[test]
    fn failed_first_canonical() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--failed", "first"])).unwrap();
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
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), None);
    }

    #[test]
    fn lf_in_debug_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug", "--lf"])).unwrap();
        let Command::Debug(args) = cmd else { panic!() };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    // ── RunArgs::validate ─────────────────────────────────────────────────────

    #[test]
    fn exitfirst_conflicts_with_maxfail() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "-x", "--maxfail", "5"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn schedule_conflicts_with_serial() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "run", "--serial", "--schedule", "random"]))
                .unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_locals_with_tb_no_conflicts() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "run", "--show-locals", "--tb", "no"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_internals_with_tb_line_conflicts() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "run", "--show-internals", "--tb", "line"]))
                .unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn show_locals_alone_is_valid() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--show-locals"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        // default tb is None (→ detail), so no conflict
        assert!(args.validate().is_ok());
    }

    #[test]
    fn no_flags_is_valid() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_ok());
    }

    #[test]
    fn run_with_collection_profile() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--collection-profile"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert!(args.collection_profile);
    }

    #[test]
    fn v_and_verbose_conflict() {
        // clap accepts -v and --verbose=full at parse time; validate() catches the mix.
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "-v", "--verbose=full"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    // ── VerbosityArgs ─────────────────────────────────────────────────────────

    #[test]
    fn short_v_resolves_detailed() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "-v"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Detailed));
    }

    #[test]
    fn short_vv_resolves_full() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "-vv"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Full));
    }

    #[test]
    fn long_verbose_bare_resolves_detailed() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--verbose"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Detailed));
    }

    #[test]
    fn long_verbose_full() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--verbose=full"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), Some(Verbosity::Full));
    }

    #[test]
    fn no_verbose_resolves_none() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert_eq!(args.verbosity.resolve(), None);
    }

    // ── --no-use-gitignore tests ──────────────────────────────────────────────

    #[test]
    fn no_use_gitignore_flag() {
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "--no-use-gitignore", "run"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }

    #[test]
    fn use_gitignore_default_is_true() {
        let (_, use_gi) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        assert!(use_gi);
    }

    #[test]
    fn no_use_gitignore_with_implicit_run() {
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "--no-use-gitignore", "tests/"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }

    #[test]
    fn no_use_gitignore_after_path_implicit_run() {
        // Simulates how run_oxitest helper calls it: path first, flag after
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "tests/", "--no-use-gitignore"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }
}
