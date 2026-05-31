use camino::Utf8PathBuf;
use clap::Parser;

use super::{
    ColorMode, FailedMode, KeepTmpMode, ScheduleStrategy, StrictMode, TbStyle, WorkerCount,
};

#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum DebugMode {
    /// Drop into pdb on first test failure
    PostMortem,
    /// Drop into pdb before every test
    Always,
}

#[derive(Parser, Debug)]
#[command(name = "oxitest", about = "A fast Python test runner")]
pub struct Cli {
    /// Paths to test files or directories (default: current directory)
    pub paths: Vec<Utf8PathBuf>,

    /// Only run tests matching the keyword expression
    #[arg(short = 'k', value_name = "EXPR")]
    pub keyword: Option<String>,

    /// Short-form verbosity (-v = detailed, -vv = full)
    #[arg(short = 'v', action = clap::ArgAction::Count)]
    pub verbose_count: u8,

    /// Explicit verbosity level (--verbose, --verbose=detailed, --verbose=full)
    #[arg(
        long = "verbose",
        value_enum,
        value_name = "LEVEL",
        default_missing_value = "detailed",
        num_args = 0..=1,
        require_equals = true,
    )]
    pub verbose: Option<crate::config::Verbosity>,

    /// Exit immediately after the first failure
    #[arg(short = 'x')]
    pub exitfirst: bool,

    /// Exit after N failures (0 = no limit)
    #[arg(long, value_name = "NUM")]
    pub maxfail: Option<usize>,

    /// Traceback style: detail (default), line, no
    #[arg(long, value_enum)]
    pub tb: Option<TbStyle>,

    /// Expand assertions-without-messages tip list
    #[arg(long)]
    pub tips: bool,

    /// Expand captured Python warnings list
    #[arg(long)]
    pub warnings: bool,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum)]
    pub color: Option<ColorMode>,

    /// Write CTRF JSON results to PATH
    #[arg(long, value_name = "PATH")]
    pub json: Option<Utf8PathBuf>,

    /// Write JUnit XML results to PATH
    #[arg(long, value_name = "PATH")]
    pub junit_xml: Option<Utf8PathBuf>,

    /// Only run tests matching the marker expression
    #[arg(short = 'm', long = "marker", value_name = "EXPR")]
    pub marker: Option<String>,

    /// Run tests serially (single process, no workers)
    #[arg(long)]
    pub serial: bool,

    /// Number of parallel worker processes ("auto" or a positive integer)
    #[arg(short = 'n', long, value_name = "N", conflicts_with = "serial")]
    pub workers: Option<WorkerCount>,

    /// Group scheduling strategy for parallel runs
    #[arg(long, value_enum)]
    pub schedule: Option<ScheduleStrategy>,

    /// Per-test timeout in seconds (overrides pyproject.toml timeout)
    #[arg(long, value_name = "SECS")]
    pub timeout: Option<u64>,

    /// Show the N slowest tests at end of run
    #[arg(long, value_name = "N")]
    pub durations: Option<usize>,

    /// Failed-test mode: "only" runs just failures, "first" runs failures before the rest
    #[arg(long, value_enum, value_name = "MODE")]
    pub failed: Option<FailedMode>,

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
    )]
    pub strict: Option<StrictMode>,

    /// Print environment information (oxitest version, Python, rustc, OS) and exit
    #[arg(long)]
    pub capture_environment: bool,

    /// List all registered fixtures and exit
    #[arg(long, visible_alias = "fx")]
    pub fixtures: bool,

    /// Show fixture dependency tree and exit
    #[arg(long)]
    pub tree: bool,

    /// Quiet output (minimal detail for --fixtures)
    #[arg(short = 'q', long)]
    pub quiet: bool,

    /// List collected tests and exit (no execution)
    #[arg(long)]
    pub list: bool,

    /// Run only tests affected by git changes (default ref from affected_base config, or HEAD)
    #[arg(
        long,
        value_name = "REF",
        default_missing_value = "",
        num_args = 0..=1,
        require_equals = true,
    )]
    pub affected: Option<String>,

    /// Retry failed tests up to N times
    #[arg(long, value_name = "N")]
    pub retries: Option<usize>,

    /// Seconds to wait between retries
    #[arg(long, value_name = "SECS")]
    pub retries_delay: Option<u64>,

    /// Drop into an interactive debugger on test failure.
    /// Implies --serial and --maxfail 1. Use `--debug=MODE` with `=`.
    /// Bare `--debug` defaults to post-mortem mode.
    #[arg(
        long,
        value_enum,
        value_name = "MODE",
        default_missing_value = "post-mortem",
        num_args = 0..=1,
        require_equals = true,
    )]
    pub debug: Option<DebugMode>,

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
    )]
    pub keep_tmp: Option<KeepTmpMode>,

    /// Auto-arrange tests by shared fixture dependencies onto the same worker.
    /// Bare `--auto-arrange` uses the default threshold (70%).
    /// Use `--auto-arrange=N` to set a custom threshold percentage.
    #[arg(
        long,
        value_name = "THRESHOLD",
        default_missing_value = "70",
        num_args = 0..=1,
        require_equals = true,
    )]
    pub auto_arrange: Option<u8>,

    /// Disable auto-arrangement of tests by shared fixtures.
    #[arg(long, conflicts_with = "auto_arrange")]
    pub no_auto_arrange: bool,

    /// Show local variables in diagnostic frames (requires --tb=detail)
    #[arg(long)]
    pub show_locals: bool,

    /// Show oxitest internal frames in trace (requires --tb=detail)
    #[arg(long)]
    pub show_internals: bool,
}

impl Cli {
    /// Check for conflicting flag combinations.
    ///
    /// Returns `Err` with a human-readable message if flags contradict each other.
    /// Called after `Cli::parse()` and before any filesystem or config work.
    pub fn validate(&self) -> Result<(), String> {
        if self.exitfirst && self.maxfail.is_some() {
            return Err(
                "-x and --maxfail both control when to stop after failures. Use one or the other."
                    .to_string(),
            );
        }

        if self.verbose_count > 0 && self.verbose.is_some() {
            return Err("use -v/-vv or --verbose=LEVEL, not both.".to_string());
        }

        if self.schedule.is_some() && self.serial {
            return Err(
                "--schedule controls parallel worker ordering, which has no effect with --serial."
                    .to_string(),
            );
        }

        if self.retries_delay.is_some() && self.retries.is_none() {
            return Err("--retries-delay has no effect without --retries.".to_string());
        }

        if let Some(ref mode) = self.debug {
            if self.workers.is_some() {
                return Err(
                    "--debug implies serial mode. It cannot be combined with --workers."
                        .to_string(),
                );
            }
            if self.serial {
                return Err(
                    "--debug already implies serial mode. Passing --serial is redundant."
                        .to_string(),
                );
            }
            if matches!(mode, DebugMode::PostMortem) {
                if self.exitfirst {
                    return Err(
                        "--debug already implies --maxfail 1. Passing -x is redundant.".to_string(),
                    );
                }
                if self.maxfail.is_some() {
                    return Err(
                        "--debug already implies --maxfail 1. It cannot be combined with --maxfail."
                            .to_string(),
                    );
                }
            }
            if self.retries.is_some() {
                return Err(
                    "--debug is for interactive debugging. It cannot be combined with --retries."
                        .to_string(),
                );
            }
            if self.retries_delay.is_some() {
                return Err(
                    "--debug is for interactive debugging. It cannot be combined with --retries-delay."
                        .to_string(),
                );
            }
            if self.schedule.is_some() {
                return Err(
                    "--debug implies serial mode. --schedule has no effect without parallel workers."
                        .to_string(),
                );
            }
            if self.timeout.is_some() {
                return Err(
                    "--debug is for interactive debugging. --timeout would kill the debugger session."
                        .to_string(),
                );
            }
        }

        // ── Mutually exclusive action modes ──────────────────────────
        let action_modes = [
            (self.list, "--list"),
            (self.fixtures, "--fixtures"),
            (self.tree, "--tree"),
            (self.capture_environment, "--capture-environment"),
        ];
        let active: Vec<&str> = action_modes
            .iter()
            .filter(|(flag, _)| *flag)
            .map(|(_, name)| *name)
            .collect();
        if active.len() > 1 {
            return Err(format!(
                "{} and {} are mutually exclusive action modes.",
                active[0], active[1],
            ));
        }

        // ── Quiet conflicts with output-requesting actions ───────────
        if self.quiet {
            if self.list {
                return Err("--quiet suppresses output, but --list requests it.".to_string());
            }
            if self.fixtures {
                return Err("--quiet suppresses output, but --fixtures requests it.".to_string());
            }
            if self.tree {
                return Err("--quiet suppresses output, but --tree requests it.".to_string());
            }
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

        Ok(())
    }
}

#[cfg(test)]
impl Cli {
    /// Construct a `Cli` with all defaults, equivalent to running `oxitest` with no arguments.
    pub fn default_for_test() -> Self {
        Self::try_parse_from(["oxitest"]).expect("default CLI args must parse")
    }
}

#[cfg(test)]
mod validate_tests {
    use super::*;
    use clap::Parser;

    #[test]
    fn test_exitfirst_conflicts_with_maxfail() {
        let cli = Cli::try_parse_from(["oxitest", "-x", "--maxfail", "5"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("-x"), "error should mention -x: {err}");
        assert!(
            err.contains("--maxfail"),
            "error should mention --maxfail: {err}"
        );
    }

    #[test]
    fn test_exitfirst_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "-x"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_maxfail_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--maxfail", "3"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_no_flags_is_valid() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_short_v_parses() {
        let cli = Cli::try_parse_from(["oxitest", "-v"]).unwrap();
        assert_eq!(cli.verbose_count, 1);
        assert!(cli.verbose.is_none());
    }

    #[test]
    fn test_short_vv_parses() {
        let cli = Cli::try_parse_from(["oxitest", "-vv"]).unwrap();
        assert_eq!(cli.verbose_count, 2);
    }

    #[test]
    fn test_long_verbose_bare() {
        let cli = Cli::try_parse_from(["oxitest", "--verbose"]).unwrap();
        assert_eq!(cli.verbose, Some(crate::config::Verbosity::Detailed));
        assert_eq!(cli.verbose_count, 0);
    }

    #[test]
    fn test_long_verbose_full() {
        let cli = Cli::try_parse_from(["oxitest", "--verbose=full"]).unwrap();
        assert_eq!(cli.verbose, Some(crate::config::Verbosity::Full));
    }

    #[test]
    fn test_short_and_long_verbose_conflict() {
        let cli = Cli::try_parse_from(["oxitest", "-v", "--verbose=full"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("-v/-vv"), "error: {err}");
        assert!(err.contains("--verbose"), "error: {err}");
    }

    #[test]
    fn test_v_with_quiet_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "-v", "-q"]).unwrap();
        assert!(
            cli.validate().is_ok(),
            "-v -q should be valid (quiet trumps)"
        );
    }

    #[test]
    fn test_short_v_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "-v"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_quiet_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "-q"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_schedule_conflicts_with_serial() {
        let cli = Cli::try_parse_from(["oxitest", "--schedule", "random", "--serial"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--schedule"),
            "error should mention --schedule: {err}"
        );
        assert!(
            err.contains("--serial"),
            "error should mention --serial: {err}"
        );
    }

    #[test]
    fn test_schedule_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--schedule", "random"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_serial_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--serial"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_retries_delay_requires_retries() {
        let cli = Cli::try_parse_from(["oxitest", "--retries-delay", "5"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--retries-delay"),
            "error should mention --retries-delay: {err}"
        );
        assert!(
            err.contains("--retries"),
            "error should mention --retries: {err}"
        );
    }

    #[test]
    fn test_retries_with_delay_is_valid() {
        let cli =
            Cli::try_parse_from(["oxitest", "--retries", "3", "--retries-delay", "5"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_retries_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--retries", "3"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_debug_bare_defaults_to_post_mortem() {
        let cli = Cli::try_parse_from(["oxitest", "--debug"]).unwrap();
        assert_eq!(cli.debug, Some(DebugMode::PostMortem));
    }

    #[test]
    fn test_debug_explicit_post_mortem() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=post-mortem"]).unwrap();
        assert_eq!(cli.debug, Some(DebugMode::PostMortem));
    }

    #[test]
    fn test_debug_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.debug, None);
    }

    #[test]
    fn test_debug_conflicts_with_workers() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--workers", "4"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--workers"),
            "error should mention --workers: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_serial() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--serial"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--serial"),
            "error should mention --serial: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_exitfirst() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "-x"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(err.contains("-x"), "error should mention -x: {err}");
    }

    #[test]
    fn test_debug_conflicts_with_maxfail() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--maxfail", "5"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--maxfail"),
            "error should mention --maxfail: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_retries() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--retries", "3"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--retries"),
            "error should mention --retries: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_retries_delay() {
        let cli = Cli::try_parse_from([
            "oxitest",
            "--debug",
            "--retries",
            "3",
            "--retries-delay",
            "5",
        ])
        .unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--retries"),
            "error should mention --retries: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_schedule() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--schedule", "random"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--schedule"),
            "error should mention --schedule: {err}"
        );
    }

    #[test]
    fn test_debug_conflicts_with_timeout() {
        let cli = Cli::try_parse_from(["oxitest", "--debug", "--timeout", "30"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--timeout"),
            "error should mention --timeout: {err}"
        );
    }

    #[test]
    fn test_debug_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--debug"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_debug_always_parses() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always"]).unwrap();
        assert_eq!(cli.debug, Some(DebugMode::Always));
    }

    #[test]
    fn test_debug_always_allows_exitfirst() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always", "-x"]).unwrap();
        assert!(cli.validate().is_ok(), "always mode should allow -x");
    }

    #[test]
    fn test_debug_always_allows_maxfail() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always", "--maxfail", "3"]).unwrap();
        assert!(cli.validate().is_ok(), "always mode should allow --maxfail");
    }

    #[test]
    fn test_debug_always_still_conflicts_with_workers() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always", "--workers", "4"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--workers"),
            "error should mention --workers: {err}"
        );
    }

    #[test]
    fn test_debug_always_still_conflicts_with_timeout() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always", "--timeout", "30"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(
            err.contains("--debug"),
            "error should mention --debug: {err}"
        );
        assert!(
            err.contains("--timeout"),
            "error should mention --timeout: {err}"
        );
    }

    #[test]
    fn test_debug_always_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--debug=always"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_keep_tmp_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.keep_tmp.is_none());
    }

    #[test]
    fn test_keep_tmp_bare_defaults_to_failed() {
        let cli = Cli::try_parse_from(["oxitest", "--keep-tmp"]).unwrap();
        assert_eq!(cli.keep_tmp, Some(KeepTmpMode::Failed));
    }

    #[test]
    fn test_keep_tmp_explicit_failed() {
        let cli = Cli::try_parse_from(["oxitest", "--keep-tmp=failed"]).unwrap();
        assert_eq!(cli.keep_tmp, Some(KeepTmpMode::Failed));
    }

    #[test]
    fn test_keep_tmp_explicit_always() {
        let cli = Cli::try_parse_from(["oxitest", "--keep-tmp=always"]).unwrap();
        assert_eq!(cli.keep_tmp, Some(KeepTmpMode::Always));
    }

    #[test]
    fn test_list_conflicts_with_fixtures() {
        let cli = Cli::try_parse_from(["oxitest", "--list", "--fixtures"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--list"), "error: {err}");
        assert!(err.contains("--fixtures"), "error: {err}");
    }

    #[test]
    fn test_list_conflicts_with_capture_environment() {
        let cli = Cli::try_parse_from(["oxitest", "--list", "--capture-environment"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--list"), "error: {err}");
        assert!(err.contains("--capture-environment"), "error: {err}");
    }

    #[test]
    fn test_fixtures_conflicts_with_capture_environment() {
        let cli = Cli::try_parse_from(["oxitest", "--fixtures", "--capture-environment"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--fixtures"), "error: {err}");
        assert!(err.contains("--capture-environment"), "error: {err}");
    }

    #[test]
    fn test_list_conflicts_with_quiet() {
        let cli = Cli::try_parse_from(["oxitest", "--list", "-q"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--list"), "error: {err}");
        assert!(err.contains("--quiet"), "error: {err}");
    }

    #[test]
    fn test_fixtures_conflicts_with_quiet() {
        let cli = Cli::try_parse_from(["oxitest", "--fixtures", "-q"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--fixtures"), "error: {err}");
        assert!(err.contains("--quiet"), "error: {err}");
    }

    #[test]
    fn test_list_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--list"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_list_with_v_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--list", "-v"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_show_locals_with_tb_no_conflicts() {
        let cli = Cli::try_parse_from(["oxitest", "--show-locals", "--tb", "no"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--show-locals"), "error: {err}");
        assert!(err.contains("--tb=detail"), "error: {err}");
    }

    #[test]
    fn test_show_locals_with_tb_line_conflicts() {
        let cli = Cli::try_parse_from(["oxitest", "--show-locals", "--tb", "line"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--show-locals"), "error: {err}");
    }

    #[test]
    fn test_show_internals_with_tb_no_conflicts() {
        let cli = Cli::try_parse_from(["oxitest", "--show-internals", "--tb", "no"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--show-internals"), "error: {err}");
    }

    #[test]
    fn test_show_internals_with_tb_line_conflicts() {
        let cli = Cli::try_parse_from(["oxitest", "--show-internals", "--tb", "line"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--show-internals"), "error: {err}");
    }

    #[test]
    fn test_show_locals_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--show-locals"]).unwrap();
        assert!(cli.validate().is_ok(), "default tb is detail, so valid");
    }

    #[test]
    fn test_show_internals_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--show-internals"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_show_locals_with_tb_detail_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--show-locals", "--tb", "detail"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_show_internals_with_tb_detail_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--show-internals", "--tb", "detail"]).unwrap();
        assert!(cli.validate().is_ok());
    }

    #[test]
    fn test_tree_conflicts_with_list() {
        let cli = Cli::try_parse_from(["oxitest", "--tree", "--list"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--tree"), "error: {err}");
        assert!(err.contains("--list"), "error: {err}");
    }

    #[test]
    fn test_tree_conflicts_with_fixtures() {
        let cli = Cli::try_parse_from(["oxitest", "--tree", "--fixtures"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--tree"), "error: {err}");
        assert!(err.contains("--fixtures"), "error: {err}");
    }

    #[test]
    fn test_tree_conflicts_with_quiet() {
        let cli = Cli::try_parse_from(["oxitest", "--tree", "-q"]).unwrap();
        let err = cli.validate().unwrap_err();
        assert!(err.contains("--tree"), "error: {err}");
        assert!(err.contains("--quiet"), "error: {err}");
    }

    #[test]
    fn test_tree_alone_is_valid() {
        let cli = Cli::try_parse_from(["oxitest", "--tree"]).unwrap();
        assert!(cli.validate().is_ok());
    }
}
