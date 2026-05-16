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

    /// Derive fields from resolved Config. Sets total=0 — call `.total(n)` before `.build()`.
    /// Use `.show_tips(true)` / `.show_warnings(true)` to apply CLI-only flags after.
    pub fn from_config(cfg: &crate::config::Config, use_color: bool) -> Self {
        Self {
            total: 0,
            use_color,
            tb: cfg.tb.clone(),
            show_tips: cfg.verbose,
            show_warnings: cfg.verbose,
            verbose: cfg.verbose,
            show_durations: cfg.durations,
            strict_suite_lines: vec![],
        }
    }

    pub fn show_tips(self, v: bool) -> Self {
        Self {
            show_tips: self.show_tips || v,
            ..self
        }
    }

    pub fn show_warnings(self, v: bool) -> Self {
        Self {
            show_warnings: self.show_warnings || v,
            ..self
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
    fn test_from_config_verbose_implies_show_tips_and_warnings() {
        let mut cfg = crate::config::Config::default();
        cfg.verbose = true;
        let opts = ReporterOptsBuilder::from_config(&cfg, false).build();
        assert!(opts.show_tips);
        assert!(opts.show_warnings);
        assert!(opts.verbose);
    }

    #[test]
    fn test_show_tips_setter() {
        let opts = ReporterOptsBuilder::new().show_tips(true).build();
        assert!(opts.show_tips);
        assert!(!opts.show_warnings);
        assert!(!opts.verbose);
    }

    #[test]
    fn test_show_warnings_setter() {
        let opts = ReporterOptsBuilder::new().show_warnings(true).build();
        assert!(!opts.show_tips);
        assert!(opts.show_warnings);
    }

    #[test]
    fn test_from_config_use_color_passed_through() {
        let cfg = crate::config::Config::default();
        let opts = ReporterOptsBuilder::from_config(&cfg, true).build();
        assert!(opts.use_color);
    }

    #[test]
    fn test_from_config_total_default_is_zero() {
        let cfg = crate::config::Config::default();
        let opts = ReporterOptsBuilder::from_config(&cfg, false).build();
        assert_eq!(opts.total, 0);
    }

    #[test]
    fn test_from_config_durations() {
        let mut cfg = crate::config::Config::default();
        cfg.durations = Some(5);
        let opts = ReporterOptsBuilder::from_config(&cfg, false).build();
        assert_eq!(opts.show_durations, Some(5));
    }

    #[test]
    fn test_from_config_durations_none() {
        let cfg = crate::config::Config::default();
        let opts = ReporterOptsBuilder::from_config(&cfg, false).build();
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
