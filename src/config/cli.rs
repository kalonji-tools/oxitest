use camino::Utf8PathBuf;
use clap::Parser;

use super::{
    parse_workers, ColorMode, FailedMode, ScheduleStrategy, StrictMode, TbStyle, WorkerCount,
};

#[derive(Parser, Debug)]
#[command(name = "oxitest", about = "A fast Python test runner")]
pub struct Cli {
    /// Paths to test files or directories (default: current directory)
    pub paths: Vec<Utf8PathBuf>,

    /// Only run tests matching the keyword expression
    #[arg(short = 'k', value_name = "EXPR")]
    pub keyword: Option<String>,

    /// Verbose output: show each test name and result
    #[arg(short = 'v', long)]
    pub verbose: bool,

    /// Exit immediately after the first failure
    #[arg(short = 'x')]
    pub exitfirst: bool,

    /// Exit after N failures (0 = no limit)
    #[arg(long, value_name = "NUM")]
    pub maxfail: Option<usize>,

    /// Traceback style: long, short (default), line, no
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
    #[arg(short = 'n', long, value_name = "N", conflicts_with = "serial", value_parser = parse_workers)]
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

    /// Quiet output (minimal detail for --fixtures)
    #[arg(short = 'q', long)]
    pub quiet: bool,

    /// List collected tests and exit (no execution)
    #[arg(long)]
    pub list: bool,
}
