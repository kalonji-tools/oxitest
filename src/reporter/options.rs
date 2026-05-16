// ─── Options ─────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct ReporterOpts {
    pub(crate) total: usize,
    pub(crate) use_color: bool,
    pub(crate) tb: crate::config::TbStyle,
    pub(crate) show_tips: bool,
    pub(crate) show_warnings: bool,
    pub(crate) verbose: bool,
    pub(crate) show_durations: Option<usize>,
    pub(crate) strict_suite_lines: Vec<String>,
}

// ─── Builder ──────────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct ReporterOptsBuilder {
    total: usize,
    use_color: bool,
    tb: crate::config::TbStyle,
    show_tips: bool,
    show_warnings: bool,
    verbose: bool,
    show_durations: Option<usize>,
    strict_suite_lines: Vec<String>,
}

impl ReporterOptsBuilder {
    /// Sensible defaults for tests: total=0, use_color=false, tb=Short,
    /// show_tips=false, show_warnings=false, verbose=false.
    pub fn new() -> Self {
        Self {
            total: 0,
            use_color: false,
            tb: crate::config::TbStyle::Short,
            show_tips: false,
            show_warnings: false,
            verbose: false,
            show_durations: None,
            strict_suite_lines: vec![],
        }
    }

    /// Derive all fields from CLI. Sets total=0 — call `.total(n)` before `.build()`.
    pub fn from_cli(cli: &crate::config::Cli, use_color: bool) -> Self {
        Self {
            total: 0,
            use_color,
            tb: cli.tb.clone().unwrap_or(crate::config::TbStyle::Short),
            show_tips: cli.tips || cli.verbose,
            show_warnings: cli.warnings || cli.verbose,
            verbose: cli.verbose,
            show_durations: cli.durations,
            strict_suite_lines: vec![],
        }
    }

    pub fn total(self, n: usize) -> Self {
        Self { total: n, ..self }
    }

    pub fn verbose(self, v: bool) -> Self {
        Self { verbose: v, ..self }
    }

    pub fn tb(self, tb: crate::config::TbStyle) -> Self {
        Self { tb, ..self }
    }

    pub fn strict_suite_lines(self, lines: Vec<String>) -> Self {
        Self {
            strict_suite_lines: lines,
            ..self
        }
    }

    pub fn build(self) -> ReporterOpts {
        ReporterOpts {
            total: self.total,
            use_color: self.use_color,
            tb: self.tb,
            show_tips: self.show_tips,
            show_warnings: self.show_warnings,
            verbose: self.verbose,
            show_durations: self.show_durations,
            strict_suite_lines: self.strict_suite_lines,
        }
    }
}

impl Default for ReporterOptsBuilder {
    fn default() -> Self {
        Self::new()
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    fn test_cli(verbose: bool, tips: bool, warnings: bool) -> crate::config::Cli {
        use clap::Parser;
        let base = crate::config::Cli::try_parse_from(["oxitest"])
            .expect("default CLI parse must succeed");
        crate::config::Cli {
            verbose,
            tips,
            warnings,
            ..base
        }
    }

    #[test]
    fn test_builder_new_defaults() {
        let opts = ReporterOptsBuilder::new().build();
        assert_eq!(opts.total, 0);
        assert!(!opts.use_color);
        assert!(!opts.show_tips);
        assert!(!opts.show_warnings);
        assert!(!opts.verbose);
        assert_eq!(opts.tb, crate::config::TbStyle::Short);
    }

    #[test]
    fn test_builder_total_override() {
        let opts = ReporterOptsBuilder::new().total(42).build();
        assert_eq!(opts.total, 42);
    }

    #[test]
    fn test_builder_verbose_override() {
        let opts = ReporterOptsBuilder::new().verbose(true).build();
        assert!(opts.verbose);
    }

    #[test]
    fn test_builder_tb_override() {
        let opts = ReporterOptsBuilder::new()
            .tb(crate::config::TbStyle::No)
            .build();
        assert_eq!(opts.tb, crate::config::TbStyle::No);
    }

    #[test]
    fn test_builder_from_cli_verbose_implies_show_tips_and_warnings() {
        let cli = test_cli(true, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_tips);
        assert!(opts.show_warnings);
        assert!(opts.verbose);
    }

    #[test]
    fn test_builder_from_cli_tips_flag_without_verbose() {
        let cli = test_cli(false, true, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_tips);
        assert!(!opts.show_warnings);
        assert!(!opts.verbose);
    }

    #[test]
    fn test_builder_from_cli_warnings_flag_without_verbose() {
        let cli = test_cli(false, false, true);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(!opts.show_tips);
        assert!(opts.show_warnings);
    }

    #[test]
    fn test_builder_from_cli_use_color_passed_through() {
        let cli = test_cli(false, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, true).build();
        assert!(opts.use_color);
    }

    #[test]
    fn test_builder_from_cli_total_default_is_zero() {
        let cli = test_cli(false, false, false);
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert_eq!(opts.total, 0);
    }

    #[test]
    fn test_builder_durations_from_cli() {
        use clap::Parser;
        let cli = crate::config::Cli::try_parse_from(["oxitest", "--durations", "5"]).unwrap();
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert_eq!(opts.show_durations, Some(5));
    }

    #[test]
    fn test_builder_durations_absent_is_none() {
        use clap::Parser;
        let cli = crate::config::Cli::try_parse_from(["oxitest"]).unwrap();
        let opts = ReporterOptsBuilder::from_cli(&cli, false).build();
        assert!(opts.show_durations.is_none());
    }

    #[test]
    fn test_builder_clone_allows_two_builds_from_same_base() {
        let base = ReporterOptsBuilder::new().total(5);
        let a = base.clone().verbose(false).build();
        let b = base.verbose(true).build();
        assert_eq!(a.total, 5);
        assert!(!a.verbose);
        assert_eq!(b.total, 5);
        assert!(b.verbose);
    }
}
