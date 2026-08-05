use camino::Utf8PathBuf;

use super::super::{ColorMode, KeepTmpMode, TbStyle};
use super::{DebugMode, FailedFilterArgs, FilteringArgs, VerbosityArgs};

/// Arguments for `oxitest debug`.
#[derive(clap::Args, Debug, Clone)]
pub struct DebugArgs {
    /// Paths to test files/directories, or node IDs (`path::test_name`)
    pub paths: Vec<Utf8PathBuf>,

    /// Node IDs extracted from positional args (populated by resolve, not by clap)
    #[arg(skip)]
    pub node_ids: Vec<crate::types::NodeId>,

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

    /// Preserve `TempDir` contents instead of cleaning up.
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

#[cfg(test)]
impl DebugArgs {
    pub fn default_for_test() -> Self {
        use clap::Parser;
        let parsed = super::OxitestCli::try_parse_from(["oxitest", "debug"])
            .expect("default DebugArgs must parse");
        match parsed.command {
            Some(super::Command::Debug(args)) => args,
            other => panic!("`oxitest debug` must parse as Command::Debug, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::{Command, DebugMode, OxitestCli};

    /// Convert a fixed-size array of `&str` into `Vec<String>`.
    fn s<const N: usize>(args: [&str; N]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
    }

    #[test]
    fn debug_default_is_post_mortem() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert_eq!(args.mode(), DebugMode::PostMortem);
    }
}
