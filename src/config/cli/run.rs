use camino::Utf8PathBuf;

use super::super::{ColorMode, KeepTmpMode, ScheduleStrategy, StrictMode, TbStyle, WorkerCount};
use super::{CovReportFormat, FailedFilterArgs, FilteringArgs, VerbosityArgs};

/// Arguments for `oxitest run` (the default subcommand).
#[derive(clap::Args, Debug, Clone)]
pub struct RunArgs {
    /// Paths to test files/directories, or node IDs (path::test_name)
    pub paths: Vec<Utf8PathBuf>,

    /// Node IDs extracted from positional args (populated by resolve, not by clap)
    #[arg(skip)]
    pub node_ids: Vec<crate::types::NodeId>,

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

    /// Collect and run doctests from all Python source modules
    #[arg(long, help_heading = "Execution")]
    pub doctest_modules: bool,

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

    /// Enable coverage collection via coverage.py
    #[arg(long, help_heading = "Reports")]
    pub cov: bool,

    /// Coverage report format (requires --cov)
    #[arg(long, value_enum, value_name = "FORMAT", help_heading = "Reports")]
    pub cov_report: Option<CovReportFormat>,
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
        if self.show_locals
            && let Some(ref tb) = self.tb
            && !matches!(tb, TbStyle::Detail)
        {
            return Err("--show-locals requires --tb=detail.".to_string());
        }
        if self.show_internals
            && let Some(ref tb) = self.tb
            && !matches!(tb, TbStyle::Detail)
        {
            return Err("--show-internals requires --tb=detail.".to_string());
        }

        if self.cov_report.is_some() && !self.cov {
            return Err("--cov-report requires --cov.".to_string());
        }

        self.verbosity.validate()
    }
}

#[cfg(test)]
impl RunArgs {
    pub fn default_for_test() -> Self {
        use clap::Parser;
        super::OxitestCli::try_parse_from(["oxitest", "run"])
            .expect("default RunArgs must parse")
            .command
            .map(|cmd| match cmd {
                super::Command::Run(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::super::{Command, CovReportFormat, OxitestCli};

    /// Convert a fixed-size array of `&str` into `Vec<String>`.
    fn s<const N: usize>(args: [&str; N]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
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

    // ── Coverage flags ───────────────────────────────────────────────────────

    #[test]
    fn cov_flag_accepted() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--cov"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.cov);
    }

    #[test]
    fn cov_report_accepted() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "run", "--cov", "--cov-report", "html"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.cov);
        assert_eq!(args.cov_report, Some(CovReportFormat::Html));
    }

    #[test]
    fn cov_report_without_cov_rejected() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run", "--cov-report", "html"])).unwrap();
        let Command::Run(args) = cmd else { panic!() };
        assert!(args.validate().is_err());
    }

    #[test]
    fn cov_report_invalid_value_rejected() {
        let result = OxitestCli::resolve(&s(["oxitest", "run", "--cov-report", "pdf"]));
        assert!(result.is_err());
    }

    #[test]
    fn cov_report_as_str() {
        assert_eq!(CovReportFormat::Term.as_str(), "term");
        assert_eq!(CovReportFormat::Html.as_str(), "html");
        assert_eq!(CovReportFormat::None.as_str(), "none");
    }
}
