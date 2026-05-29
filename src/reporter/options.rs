use crate::config::Verbosity;

const DEFAULT_NAME_WIDTH: usize = 45;

// ─── Options ─────────────────────────────────────────────────────────────────

/// Configuration snapshot consumed by reporter implementations.
///
/// Built once per run via [`ReporterOptsBuilder`] and passed to [`make_reporter`](super::make_reporter).
/// Fields are `pub(crate)` — reporters read them directly without accessor methods.
#[derive(Debug)]
pub struct ReporterOpts {
    pub(crate) total: usize,
    pub(crate) async_count: usize,
    pub(crate) use_color: bool,
    pub(crate) tb: crate::config::TbStyle,
    pub(crate) show_tips: bool,
    pub(crate) show_warnings: bool,
    pub(crate) show_locals: bool,
    // Reserved for future use (e.g., --show-internals flag display in other reporters)
    #[allow(dead_code)]
    pub(crate) show_internals: bool,
    pub(crate) verbosity: Verbosity,
    pub(crate) show_durations: Option<usize>,
    pub(crate) name_width: usize,
    pub(crate) strict_suite_lines: Vec<String>,
}

// ─── Builder ──────────────────────────────────────────────────────────────────

/// Builder for [`ReporterOpts`].
///
/// Use [`ReporterOptsBuilder::new`] in tests (sensible defaults, color off) or
/// [`ReporterOptsBuilder::from_config`] in production to derive settings from a
/// resolved [`Config`](crate::config::Config). Chain setters then call `.build()`.
#[derive(Clone, Debug)]
pub struct ReporterOptsBuilder {
    total: usize,
    async_count: usize,
    use_color: bool,
    tb: crate::config::TbStyle,
    show_tips: bool,
    show_warnings: bool,
    show_locals: bool,
    show_internals: bool,
    verbosity: Verbosity,
    show_durations: Option<usize>,
    name_width: usize,
    strict_suite_lines: Vec<String>,
}

impl ReporterOptsBuilder {
    /// Sensible defaults for tests: total=0, use_color=false, tb=Detail,
    /// show_tips=false, show_warnings=false, verbosity=Normal.
    pub fn new() -> Self {
        Self {
            total: 0,
            async_count: 0,
            use_color: false,
            tb: crate::config::TbStyle::Detail,
            show_tips: false,
            show_warnings: false,
            show_locals: false,
            show_internals: false,
            verbosity: Verbosity::Normal,
            show_durations: None,
            name_width: DEFAULT_NAME_WIDTH,
            strict_suite_lines: vec![],
        }
    }

    /// Derive fields from resolved Config. Sets total=0 — call `.total(n)` before `.build()`.
    /// Use `.show_tips(true)` / `.show_warnings(true)` to apply CLI-only flags after.
    pub fn from_config(cfg: &crate::config::Config, use_color: bool) -> Self {
        Self {
            total: 0,
            async_count: 0,
            use_color,
            tb: cfg.tb.clone(),
            show_tips: cfg.verbosity >= Verbosity::Detailed,
            show_warnings: cfg.verbosity >= Verbosity::Detailed,
            show_locals: cfg.show_locals,
            show_internals: cfg.show_internals,
            verbosity: cfg.verbosity,
            show_durations: cfg.durations,
            name_width: DEFAULT_NAME_WIDTH,
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

    pub fn async_count(self, count: usize) -> Self {
        Self {
            async_count: count,
            ..self
        }
    }

    pub fn verbosity(self, v: Verbosity) -> Self {
        let detailed_or_more = v >= Verbosity::Detailed;
        Self {
            verbosity: v,
            show_tips: self.show_tips || detailed_or_more,
            show_warnings: self.show_warnings || detailed_or_more,
            ..self
        }
    }

    pub fn tb(self, tb: crate::config::TbStyle) -> Self {
        Self { tb, ..self }
    }

    #[allow(dead_code)]
    pub fn show_locals(self, v: bool) -> Self {
        Self {
            show_locals: v,
            ..self
        }
    }

    #[allow(dead_code)]
    pub fn show_internals(self, v: bool) -> Self {
        Self {
            show_internals: v,
            ..self
        }
    }

    pub fn strict_suite_lines(self, lines: Vec<String>) -> Self {
        Self {
            strict_suite_lines: lines,
            ..self
        }
    }

    pub fn name_width(self, w: usize) -> Self {
        Self {
            name_width: w.clamp(30, 70),
            ..self
        }
    }

    pub fn build(self) -> ReporterOpts {
        ReporterOpts {
            total: self.total,
            async_count: self.async_count,
            use_color: self.use_color,
            tb: self.tb,
            show_tips: self.show_tips,
            show_warnings: self.show_warnings,
            show_locals: self.show_locals,
            show_internals: self.show_internals,
            verbosity: self.verbosity,
            show_durations: self.show_durations,
            name_width: self.name_width,
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
        assert_eq!(opts.verbosity, Verbosity::Normal);
        assert_eq!(opts.tb, crate::config::TbStyle::Detail);
    }

    #[test]
    fn test_builder_total_override() {
        let opts = ReporterOptsBuilder::new().total(42).build();
        assert_eq!(opts.total, 42);
    }

    #[test]
    fn test_builder_verbose_propagates_to_tips_and_warnings() {
        let opts = ReporterOptsBuilder::new()
            .verbosity(Verbosity::Detailed)
            .build();
        assert_eq!(opts.verbosity, Verbosity::Detailed);
        assert!(opts.show_tips, "Detailed must set show_tips");
        assert!(opts.show_warnings, "Detailed must set show_warnings");
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
        let cfg = crate::config::Config {
            verbosity: Verbosity::Detailed,
            ..crate::config::Config::default()
        };
        let opts = ReporterOptsBuilder::from_config(&cfg, false).build();
        assert!(opts.show_tips);
        assert!(opts.show_warnings);
        assert_eq!(opts.verbosity, Verbosity::Detailed);
    }

    #[test]
    fn test_show_tips_setter() {
        let opts = ReporterOptsBuilder::new().show_tips(true).build();
        assert!(opts.show_tips);
        assert!(!opts.show_warnings);
        assert_eq!(opts.verbosity, Verbosity::Normal);
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
        let cfg = crate::config::Config {
            durations: Some(5),
            ..crate::config::Config::default()
        };
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
        let a = base.clone().verbosity(Verbosity::Normal).build();
        let b = base.verbosity(Verbosity::Detailed).build();
        assert_eq!(a.total, 5);
        assert_eq!(a.verbosity, Verbosity::Normal);
        assert_eq!(b.total, 5);
        assert_eq!(b.verbosity, Verbosity::Detailed);
    }

    #[test]
    fn test_builder_name_width_default_is_45() {
        let opts = ReporterOptsBuilder::new().build();
        assert_eq!(opts.name_width, 45);
    }

    #[test]
    fn test_builder_name_width_clamped_to_range() {
        let low = ReporterOptsBuilder::new().name_width(10).build();
        assert_eq!(low.name_width, 30, "must clamp to minimum 30");
        let high = ReporterOptsBuilder::new().name_width(100).build();
        assert_eq!(high.name_width, 70, "must clamp to maximum 70");
        let mid = ReporterOptsBuilder::new().name_width(55).build();
        assert_eq!(mid.name_width, 55);
    }

    #[test]
    fn test_builder_async_count_defaults_to_zero() {
        let opts = ReporterOptsBuilder::new().build();
        assert_eq!(opts.async_count, 0);
    }

    #[test]
    fn test_builder_async_count_override() {
        let opts = ReporterOptsBuilder::new().total(42).async_count(18).build();
        assert_eq!(opts.async_count, 18);
    }

    #[test]
    fn test_print_collected_no_async() {
        // Exercises the zero-async branch (output goes to stdout, not asserted)
        super::super::print_collected(10, 0);
    }

    #[test]
    fn test_print_collected_with_async() {
        // Exercises the async-count branch
        super::super::print_collected(10, 3);
    }
}
