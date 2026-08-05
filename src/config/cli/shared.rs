use super::super::{FailedMode, Verbosity};

/// Query DSL and filtering flags, shared by run/debug.
#[derive(clap::Args, Debug, Clone, Default)]
pub struct FilteringArgs {
    /// Filter tests using the query DSL expression (e.g. "name(foo) & mark(slow)")
    #[arg(short = 'E', value_name = "EXPR", help_heading = "Filtering")]
    pub expression: Option<String>,

    /// Run only tests affected by git changes (default ref from `affected_base` config, or HEAD)
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
    pub const fn resolve(&self) -> Option<FailedMode> {
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
    pub const fn resolve(&self) -> Option<Verbosity> {
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

#[cfg(test)]
mod tests {
    use super::super::{Command, OxitestCli};
    use crate::config::{FailedMode, Verbosity};

    /// Convert a fixed-size array of `&str` into `Vec<String>`.
    fn s<const N: usize>(args: [&str; N]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
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
}
