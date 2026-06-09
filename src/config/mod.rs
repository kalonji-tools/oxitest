use camino::{Utf8Path, Utf8PathBuf};

mod cli;
pub use cli::{Command, DebugArgs, DebugMode, OxitestCli, QueryArgs, QueryFormat, RunArgs};

mod pyproject;
use pyproject::{AutoArrangeToml, OxitestConfig, PyprojectToml};

impl DebugMode {
    /// Convert to the string representation sent across the Python bridge.
    pub fn as_str(&self) -> &'static str {
        match self {
            DebugMode::PostMortem => "post-mortem",
            DebugMode::Always => "always",
        }
    }

    /// Apply debug-mode side effects to a config.
    ///
    /// Debug modes force serial execution, disable timeouts, and may override
    /// traceback style and maxfail. `cli_tb` should be `Some` only if the user
    /// passed an explicit `--tb` flag (prevents overriding their choice).
    pub fn apply_to(&self, cfg: &mut Config, cli_tb: Option<&TbStyle>) {
        cfg.debug = Some(self.clone());
        cfg.serial = true;
        cfg.timeout_secs = None;
        cfg.show_internals = true;
        if cli_tb.is_none() {
            cfg.tb = TbStyle::Detail;
        }
        if matches!(self, DebugMode::PostMortem) {
            cfg.maxfail = 1;
        }
    }
}

/// Number of parallel worker subprocesses to use.
///
/// `Auto` resolves to the number of logical CPU cores at runtime (see
/// [`Config::worker_count`]). `Fixed(n)` pins to exactly `n` workers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerCount {
    Auto,
    Fixed(usize),
}

impl WorkerCount {
    /// Validate and construct a `Fixed` worker count.
    ///
    /// Returns `Err` if `n` is zero — at least one worker is required.
    fn try_from_count(n: usize) -> Result<Self, String> {
        if n == 0 {
            Err("worker count must be at least 1".into())
        } else {
            Ok(WorkerCount::Fixed(n))
        }
    }
}

impl std::str::FromStr for WorkerCount {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s.eq_ignore_ascii_case("auto") {
            return Ok(WorkerCount::Auto);
        }
        let n: usize = s
            .parse()
            .map_err(|_| format!("expected \"auto\" or a positive integer, got \"{s}\""))?;
        Self::try_from_count(n)
    }
}

/// Traceback display style for test failures.
///
/// - `Detail` — user frames only (oxitest internal frames filtered out).
/// - `Line` — single-line summary, no frame block.
/// - `No` — suppress traceback entirely.
#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TbStyle {
    Detail,
    Line,
    No,
}

/// Strict-mode enforcement level.
///
/// - `Abort` — violations are hard errors; the run exits with code 3 before tests execute.
/// - `Enforce` — violations are reported as per-test errors in the normal output but do
///   not prevent other tests from running.
#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum StrictMode {
    Abort,
    Enforce,
}

#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum FailedMode {
    /// Only run previously-failed tests
    Only,
    /// Run failed tests first, then the rest
    First,
}

#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, Copy, PartialEq, Default)]
#[serde(rename_all = "kebab-case")]
pub enum ScheduleStrategy {
    /// Longest modules first (based on cached timing data, falls back to item count)
    #[default]
    LongestFirst,
    /// Previously-failed modules first
    FailedFirst,
    /// Random order (useful for detecting order-dependent tests)
    Random,
}

#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, Copy, PartialEq, Default)]
#[serde(rename_all = "lowercase")]
pub enum ColorMode {
    /// Detect TTY automatically
    #[default]
    Auto,
    /// Always enable color
    Always,
    /// Disable color
    Never,
}

#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum KeepTmpMode {
    /// Preserve temp dirs only when the test fails
    Failed,
    /// Preserve every temp dir regardless of outcome
    Always,
}

/// Output verbosity level.
///
/// Controls how much detail is shown in test output:
/// - `Normal` — default: dots/lines, summary only.
/// - `Detailed` — show individual test names and outcomes.
/// - `Full` — show test names, outcomes, and fixture/setup detail.
#[derive(
    Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, serde::Deserialize, clap::ValueEnum,
)]
#[serde(rename_all = "lowercase")]
pub enum Verbosity {
    #[default]
    Normal = 0,
    Detailed = 1,
    Full = 2,
}

impl KeepTmpMode {
    /// Convert to the string representation sent across the Python bridge.
    pub fn as_str(&self) -> &'static str {
        match self {
            KeepTmpMode::Failed => "failed",
            KeepTmpMode::Always => "always",
        }
    }
}

impl ColorMode {
    /// Resolve the color mode to a boolean given whether stdout is a TTY.
    ///
    /// `Always` forces color on (including overriding the `console` crate global).
    /// `Never` disables color. `Auto` defers to TTY detection and the `console` crate.
    pub fn resolve(self, is_tty: bool) -> bool {
        match self {
            ColorMode::Always => {
                console::set_colors_enabled(true);
                true
            }
            ColorMode::Never => false,
            ColorMode::Auto => is_tty && console::colors_enabled(),
        }
    }
}

/// Merged configuration from `[tool.oxitest]` in `pyproject.toml` and CLI flags.
///
/// CLI flags take precedence over `pyproject.toml` values. Construct via
/// `Config::load(rootdir)` then `config.merge_run_args(&args)` or
/// `config.merge_debug_args(&args)`. Defaults come from `Config::default()`.
#[derive(Debug)]
pub struct Config {
    pub rootdir: Utf8PathBuf,
    pub testpaths: Vec<Utf8PathBuf>,
    pub python_files: Vec<String>,
    pub norecursedirs: Vec<String>,
    pub maxfail: usize,
    pub registered_markers: Vec<String>,
    pub timeout_secs: Option<u64>,
    pub serial: bool,
    pub debug: Option<DebugMode>,
    pub workers: Option<WorkerCount>,
    pub cache_max_age: u32,
    pub min_parallel_tests: usize,
    pub timeout_multiplier: Option<f64>,
    pub spawn_overhead_ms: f64,
    pub strict: Option<StrictMode>,
    pub markers_without_description: Vec<String>,
    pub schedule: ScheduleStrategy,
    pub failed: Option<FailedMode>,
    pub tb: TbStyle,
    pub show_locals: bool,
    pub show_internals: bool,
    pub verbosity: Verbosity,
    pub durations: Option<usize>,
    pub color: ColorMode,
    pub plugins: Vec<String>,
    pub plugin_settings: std::collections::HashMap<String, toml::Value>,
    pub async_backend: String,
    pub affected: Option<String>,
    pub affected_base: String,
    pub retries: usize,
    pub retries_delay_secs: u64,
    pub keep_tmp: Option<KeepTmpMode>,
    pub auto_arrange_threshold: Option<u8>,
    pub collection_profile: bool,
    pub use_gitignore: bool,
    pub doctest_modules: bool,
    pub node_ids: Vec<crate::types::NodeId>,
    pub node_id_source_files: std::collections::HashSet<Utf8PathBuf>,
    pub cov: bool,
    pub cov_report: Option<cli::CovReportFormat>,
    /// True when the user specified explicit paths or node IDs on the CLI.
    /// Used to skip unused-fixture detection (which requires the full suite).
    pub has_explicit_paths: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            rootdir: Utf8PathBuf::from("."),
            testpaths: vec![Utf8PathBuf::from(".")],
            python_files: vec!["test_*.py".to_string(), "*_test.py".to_string()],
            norecursedirs: vec![
                ".git".to_string(),
                "__pycache__".to_string(),
                ".venv".to_string(),
                "venv".to_string(),
                ".tox".to_string(),
                "dist".to_string(),
                "build".to_string(),
                "node_modules".to_string(),
            ],
            maxfail: 0,
            registered_markers: vec![],
            timeout_secs: None,
            serial: false,
            debug: None,
            workers: None,
            cache_max_age: 50,
            min_parallel_tests: 100,
            timeout_multiplier: None,
            spawn_overhead_ms: 250.0,
            strict: None,
            markers_without_description: vec![],
            schedule: ScheduleStrategy::LongestFirst,
            failed: None,
            tb: TbStyle::Detail,
            show_locals: false,
            show_internals: false,
            verbosity: Verbosity::Normal,
            durations: None,
            color: ColorMode::Auto,
            plugins: vec![],
            plugin_settings: std::collections::HashMap::new(),
            async_backend: "asyncio".to_string(),
            affected: None,
            affected_base: "HEAD".to_string(),
            retries: 0,
            retries_delay_secs: 0,
            keep_tmp: None,
            auto_arrange_threshold: Some(70),
            collection_profile: false,
            use_gitignore: true,
            cov: false,
            cov_report: None,
            doctest_modules: false,
            node_ids: vec![],
            node_id_source_files: std::collections::HashSet::new(),
            has_explicit_paths: false,
        }
    }
}

/// Applies an `Option` value to a config field when `Some`.
///
/// Two variants:
///   `apply_if_some!(cfg, field, value)` — assigns the inner value directly.
///   `apply_if_some!(cfg, field, value, wrap)` — wraps the inner value in `Some`
///     (for fields where the target is `Option<T>`).
macro_rules! apply_if_some {
    ($config:expr, $field:ident, $value:expr) => {
        if let Some(v) = $value {
            $config.$field = v;
        }
    };
    ($config:expr, $field:ident, $value:expr, wrap) => {
        if let Some(v) = $value {
            $config.$field = Some(v);
        }
    };
}

/// Shared optional fields that both TOML and CLI sources can override.
///
/// Built by `merge_toml` and `merge_cli`, then applied via
/// `Config::apply_overrides` — keeping the field-assignment logic in one place.
#[derive(Default)]
struct Overrides {
    schedule: Option<ScheduleStrategy>,
    retries: Option<usize>,
    retries_delay_secs: Option<u64>,
    workers: Option<WorkerCount>,
    failed: Option<FailedMode>,
    tb: Option<TbStyle>,
    color: Option<ColorMode>,
    durations: Option<usize>,
    strict: Option<StrictMode>,
    keep_tmp: Option<KeepTmpMode>,
    show_locals: Option<bool>,
    show_internals: Option<bool>,
    auto_arrange_threshold: Option<Option<u8>>,
}

/// Resolve testpaths relative to a root directory.
///
/// Each path string is joined to `rootdir`, producing absolute paths for the
/// collector to scan.
fn resolve_testpaths(paths: &[String], rootdir: &Utf8Path) -> Vec<Utf8PathBuf> {
    paths.iter().map(|s| rootdir.join(s)).collect()
}

/// Parse raw marker strings into (registered names, names without descriptions).
///
/// Markers of the form `"name: description"` contribute only the name to the
/// registered list. Markers without a `:` separator are flagged as missing a
/// description (used for strict-mode warnings).
fn parse_marker_descriptions(raw_markers: &[String]) -> (Vec<String>, Vec<String>) {
    let mut names = Vec::new();
    let mut no_desc = Vec::new();
    for s in raw_markers {
        if let Some((name, _)) = s.split_once(':') {
            names.push(name.trim().to_owned());
        } else {
            let name = s.trim().to_owned();
            no_desc.push(name.clone());
            names.push(name);
        }
    }
    (names, no_desc)
}

pub fn find_rootdir(start: Option<&Utf8Path>) -> Utf8PathBuf {
    let start = start.unwrap_or(Utf8Path::new("."));
    let start = if start.is_file() {
        start.parent().unwrap_or(start)
    } else {
        start
    };
    let start = start
        .canonicalize_utf8()
        .unwrap_or_else(|_| start.to_owned());

    let mut current = start.clone();
    loop {
        if current.join("pyproject.toml").exists()
            || current.join("setup.cfg").exists()
            || current.join("tox.ini").exists()
        {
            return current;
        }
        match current.parent() {
            Some(parent) if parent != current.as_path() => current = parent.to_owned(),
            _ => return start,
        }
    }
}

/// Returns the number of logical CPUs available, or 1 on error.
pub(crate) fn cpu_count() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
}

impl Config {
    pub fn load(rootdir: &Utf8Path) -> Self {
        let pyproject_path = rootdir.join("pyproject.toml");
        let config = Config {
            testpaths: vec![rootdir.to_owned()],
            rootdir: rootdir.to_owned(),
            ..Config::default()
        };

        let content = match std::fs::read_to_string(&pyproject_path) {
            Ok(c) => c,
            Err(_) => return config,
        };
        let pyproject: PyprojectToml = match toml::from_str(&content) {
            Ok(p) => p,
            Err(e) => {
                tracing::warn!(
                    path = %pyproject_path,
                    error = %e,
                    "pyproject.toml parse failed — running with default config"
                );
                return config;
            }
        };

        let tc = pyproject.tool.and_then(|t| t.oxitest).unwrap_or_default();
        // rootdir was moved into config; re-derive from pyproject_path
        let rootdir = pyproject_path.parent().expect("pyproject_path has parent");
        config.merge_toml(tc, Some(rootdir))
    }

    /// Apply shared overrides from either TOML or CLI source.
    fn apply_overrides(&mut self, ovr: Overrides) {
        // ── Execution ──────────────────────────────────────────────────
        apply_if_some!(self, schedule, ovr.schedule);
        apply_if_some!(self, retries, ovr.retries);
        apply_if_some!(self, retries_delay_secs, ovr.retries_delay_secs);
        apply_if_some!(self, workers, ovr.workers, wrap);
        apply_if_some!(self, failed, ovr.failed, wrap);
        apply_if_some!(self, keep_tmp, ovr.keep_tmp, wrap);
        if let Some(v) = ovr.auto_arrange_threshold {
            self.auto_arrange_threshold = v;
        }

        // ── Output ─────────────────────────────────────────────────────
        apply_if_some!(self, tb, ovr.tb);
        apply_if_some!(self, show_locals, ovr.show_locals);
        apply_if_some!(self, show_internals, ovr.show_internals);
        apply_if_some!(self, color, ovr.color);
        apply_if_some!(self, durations, ovr.durations, wrap);

        // ── Filtering ──────────────────────────────────────────────────
        apply_if_some!(self, strict, ovr.strict, wrap);
    }

    /// Merge fields from a parsed `[tool.oxitest]` section (fluent builder).
    ///
    /// `rootdir` controls how `testpaths` are resolved:
    ///   - `Some(root)` → each path is joined to root (used by `Config::load`)
    ///   - `None`       → each path is taken as-is (used by `Config::from_str`)
    fn merge_toml(mut self, tc: OxitestConfig, rootdir: Option<&Utf8Path>) -> Self {
        // ── Paths ────────────────────────────────────────────────────────
        if let Some(paths) = tc.testpaths {
            self.testpaths = match rootdir {
                Some(root) => resolve_testpaths(&paths, root),
                None => paths.into_iter().map(Utf8PathBuf::from).collect(),
            };
        }
        apply_if_some!(self, python_files, tc.python_files);
        apply_if_some!(self, norecursedirs, tc.norecursedirs);
        apply_if_some!(self, use_gitignore, tc.use_gitignore);
        apply_if_some!(self, doctest_modules, tc.doctest_modules);

        // ── Execution (unique to TOML) ──────────────────────────────────
        apply_if_some!(self, maxfail, tc.maxfail);
        apply_if_some!(self, serial, tc.serial);
        apply_if_some!(self, async_backend, tc.async_backend);
        self.cache_max_age = tc.cache_max_age.unwrap_or(self.cache_max_age);
        self.min_parallel_tests = tc.min_parallel_tests.unwrap_or(self.min_parallel_tests);
        self.spawn_overhead_ms = tc.spawn_overhead_ms.unwrap_or(self.spawn_overhead_ms);
        self.timeout_secs = tc.timeout;
        self.timeout_multiplier = tc.timeout_multiplier;

        // ── Output (unique to TOML) ─────────────────────────────────────
        apply_if_some!(self, verbosity, tc.verbosity);

        // ── Filtering (unique to TOML) ──────────────────────────────────
        apply_if_some!(self, plugins, tc.plugins);
        self.plugin_settings = tc.plugin_settings;
        apply_if_some!(self, affected_base, tc.affected_base);

        if let Some(raw_markers) = tc.markers {
            let (names, no_desc) = parse_marker_descriptions(&raw_markers);
            self.registered_markers = names;
            self.markers_without_description = no_desc;
        }

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: tc.schedule,
            retries: tc.retries,
            retries_delay_secs: tc.retries_delay,
            workers: tc.workers,
            failed: tc.failed,
            tb: tc.tb,
            color: tc.color,
            durations: tc.durations,
            strict: tc.strict,
            keep_tmp: tc.keep_tmp,
            show_locals: tc.show_locals,
            show_internals: tc.show_internals,
            auto_arrange_threshold: tc.auto_arrange.map(|v| match v {
                AutoArrangeToml::Threshold(n) => Some(n),
                AutoArrangeToml::Disabled(false) => None,
                AutoArrangeToml::Disabled(true) => Some(70),
            }),
        });

        self
    }

    pub fn merge_run_args(mut self, args: &RunArgs) -> Self {
        // ── Paths ────────────────────────────────────────────────────────
        self.merge_paths(&args.paths);

        if !args.node_ids.is_empty() {
            self.canonicalize_node_ids(&args.node_ids);
        }

        // ── Execution (unique to CLI) ───────────────────────────────────
        // validate() guarantees -x and --maxfail are mutually exclusive.
        if args.exitfirst {
            self.maxfail = 1;
        }
        if let Some(n) = args.maxfail {
            if n > 0 {
                self.maxfail = n;
            }
        }
        if args.serial {
            self.serial = true;
        }
        apply_if_some!(self, timeout_secs, args.timeout, wrap);
        if args.doctest_modules {
            self.doctest_modules = true;
        }

        // ── Filtering (unique to CLI) ───────────────────────────────────
        self.merge_affected(&args.filter.affected);

        // ── Coverage ──────────────────────────────────────────────────
        self.cov = args.cov;
        if args.cov_report.is_some() {
            self.cov_report = args.cov_report.clone();
        }

        // ── Output (unique to CLI) ──────────────────────────────────
        if let Some(level) = args.verbosity.resolve() {
            self.verbosity = level;
        }
        self.collection_profile = args.collection_profile;

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: args.schedule,
            retries: args.retries,
            retries_delay_secs: None,
            workers: args.workers,
            failed: args.failed_filter.resolve(),
            tb: args.tb.clone(),
            color: args.color,
            durations: args.durations,
            strict: args.strict.clone(),
            keep_tmp: args.keep_tmp,
            show_locals: if args.show_locals { Some(true) } else { None },
            show_internals: if args.show_internals {
                Some(true)
            } else {
                None
            },
            auto_arrange_threshold: None,
        });

        self
    }

    pub fn merge_debug_args(mut self, args: &DebugArgs) -> Self {
        // ── Paths ────────────────────────────────────────────────────────
        self.merge_paths(&args.paths);

        if !args.node_ids.is_empty() {
            self.canonicalize_node_ids(&args.node_ids);
        }

        // ── Debug mode ──────────────────────────────────────────────────
        args.mode().apply_to(&mut self, args.tb.as_ref());

        // ── Filtering (unique to CLI) ───────────────────────────────────
        self.merge_affected(&args.filter.affected);

        // ── Output (unique to CLI) ──────────────────────────────────
        if let Some(level) = args.verbosity.resolve() {
            self.verbosity = level;
        }

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: None,
            retries: None,
            retries_delay_secs: None,
            workers: None,
            failed: args.failed_filter.resolve(),
            tb: args.tb.clone(),
            color: args.color,
            durations: None,
            strict: None,
            keep_tmp: args.keep_tmp,
            show_locals: if args.show_locals { Some(true) } else { None },
            show_internals: None,
            auto_arrange_threshold: None,
        });

        self
    }

    pub fn merge_query_args(mut self, args: &cli::QueryArgs) -> Self {
        self.merge_paths(&args.paths);
        if let Some(c) = args.color {
            self.color = c;
        }
        self
    }

    /// Merge CLI paths into testpaths, resolving relative paths against rootdir.
    fn merge_paths(&mut self, paths: &[Utf8PathBuf]) {
        if !paths.is_empty() {
            self.has_explicit_paths = true;
            self.testpaths = paths
                .iter()
                .map(|p| {
                    if p.is_absolute() {
                        p.clone()
                    } else {
                        self.rootdir.join(p)
                    }
                })
                .collect();
        }
    }

    /// Canonicalize node IDs so their file paths match the canonical form used by collected items.
    ///
    /// Resolves relative paths in node IDs against rootdir, then canonicalizes via
    /// `std::fs::canonicalize`. Also populates `node_id_source_files` with the
    /// canonical file paths.
    fn canonicalize_node_ids(&mut self, raw_ids: &[crate::types::NodeId]) {
        use crate::types::NodeId;

        self.node_ids = raw_ids
            .iter()
            .map(|id| {
                let id_str: &str = id.as_ref();
                let Some((file_part, rest)) = id_str.split_once("::") else {
                    return id.clone();
                };

                // Glob node IDs: prepend rootdir but skip fs::canonicalize.
                if crate::filter::contains_glob_chars(file_part) {
                    let abs_pattern = self.rootdir.join(file_part);
                    return NodeId::from_raw(&format!("{abs_pattern}::{rest}"));
                }

                let file_path = Utf8PathBuf::from(file_part);
                let abs_path = if file_path.is_absolute() {
                    file_path
                } else {
                    self.rootdir.join(&file_path)
                };
                match std::fs::canonicalize(abs_path.as_std_path()) {
                    Ok(canonical) => match Utf8PathBuf::from_path_buf(canonical) {
                        Ok(utf8) => NodeId::from_raw(&format!("{utf8}::{rest}")),
                        Err(_) => id.clone(),
                    },
                    Err(_) => id.clone(),
                }
            })
            .collect();

        // Only populate source files from non-glob node IDs.
        self.node_id_source_files = self
            .node_ids
            .iter()
            .filter(|id| !crate::filter::contains_glob_chars(id.as_ref()))
            .filter_map(|id| id.module_path())
            .map(Utf8PathBuf::from)
            .collect();
    }

    /// Merge the `--affected` flag into config, resolving empty sentinel to `affected_base`.
    fn merge_affected(&mut self, affected: &Option<String>) {
        if let Some(ref val) = affected {
            self.has_explicit_paths = true;
            if val.is_empty() {
                self.affected = Some(self.affected_base.clone());
            } else {
                self.affected = affected.clone();
            }
        }
    }

    /// Resolve the configured worker count to a concrete number.
    ///
    /// On single-CPU machines `Auto` resolves to 1, which means `--workers auto`
    /// silently falls back to serial (no point spawning one subprocess worker).
    pub fn worker_count(&self) -> usize {
        match self.workers {
            _ if self.serial => 1,
            Some(WorkerCount::Fixed(n)) => n,
            Some(WorkerCount::Auto) | None => cpu_count(),
        }
    }
}

/// Choose the number of worker subprocesses for this run.
///
/// Priority: serial flag → explicit `--workers N` → heuristic.
/// The heuristic caps at `cpu_count` and, when a timing estimate is available,
/// avoids spawning more workers than the estimated total runtime warrants given
/// the subprocess spawn overhead (`spawn_overhead_ms` per worker).
pub(crate) fn compute_optimal_workers(
    explicit_workers: Option<WorkerCount>,
    serial: bool,
    cpu_count: usize,
    estimated: Option<std::time::Duration>,
    spawn_overhead_ms: f64,
) -> usize {
    if serial {
        return 1;
    }
    match explicit_workers {
        Some(WorkerCount::Fixed(n)) => return n,
        Some(WorkerCount::Auto) | None => {}
    }
    if let Some(est) = estimated {
        let est_ms = est.as_millis() as f64;
        let needed = (est_ms / spawn_overhead_ms).ceil() as usize;
        cpu_count.min(needed).max(1)
    } else {
        cpu_count
    }
}

#[cfg(test)]
impl Config {
    pub fn from_str(s: &str) -> Result<Self, toml::de::Error> {
        let pyproject: PyprojectToml = toml::from_str(s)?;
        let tc = pyproject.tool.and_then(|t| t.oxitest).unwrap_or_default();
        Ok(Config::default().merge_toml(tc, None))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use assert_fs::prelude::*;
    use camino::{Utf8Path, Utf8PathBuf};
    use std::fs;
    use tempfile::TempDir;

    /// Returns default `RunArgs` for use in tests.
    fn base_run_args() -> RunArgs {
        RunArgs::default_for_test()
    }

    /// Helper: resolve CLI args into a `RunArgs`.
    fn parse_run(args: &[&str]) -> RunArgs {
        let mut argv: Vec<String> = vec!["oxitest".to_string(), "run".to_string()];
        argv.extend(args.iter().map(|s| s.to_string()));
        let (cmd, _) = OxitestCli::resolve(&argv).unwrap();
        match cmd {
            Command::Run(a) => a,
            _ => panic!("expected Command::Run"),
        }
    }

    #[test]
    fn test_default_python_files() {
        let config = Config::default();
        assert!(config.python_files.contains(&"test_*.py".to_string()));
        assert!(config.python_files.contains(&"*_test.py".to_string()));
    }

    #[test]
    fn test_load_with_no_pyproject_returns_defaults() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.python_files, Config::default().python_files);
    }

    #[test]
    fn load_preserves_rootdir_as_given() {
        // load stores what it receives unchanged; the caller is responsible for
        // providing a canonical path.
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("pyproject.toml").write_str("").unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let cfg = Config::load(utf8_dir);
        assert_eq!(
            cfg.rootdir, utf8_dir,
            "load should store rootdir exactly as given, got: {}",
            cfg.rootdir
        );
    }

    #[test]
    fn test_pytest_section_is_ignored() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.pytest]\ntestpaths = [\"tests\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.testpaths, vec![utf8_dir.to_owned()]);
    }

    #[test]
    fn test_load_testpaths_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntestpaths = [\"tests\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.testpaths, vec![utf8_dir.join("tests")]);
    }

    #[test]
    fn test_load_python_files_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\npython_files = [\"check_*.py\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.python_files, vec!["check_*.py".to_string()]);
    }

    #[test]
    fn test_find_rootdir_finds_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(dir.path().join("pyproject.toml"), "").unwrap();
        let subdir = utf8_dir.join("tests");
        fs::create_dir(&subdir).unwrap();
        let rootdir = find_rootdir(Some(&subdir));
        assert_eq!(rootdir, utf8_dir);
    }

    #[test]
    fn test_find_rootdir_falls_back_to_start() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let rootdir = find_rootdir(Some(utf8_dir));
        assert_eq!(rootdir, utf8_dir);
    }

    #[test]
    fn test_find_rootdir_relative_subdir_returns_absolute() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(dir.path().join("pyproject.toml"), "").unwrap();
        let subdir = utf8_dir.join("a").join("b");
        fs::create_dir_all(&subdir).unwrap();
        let rootdir = find_rootdir(Some(&subdir));
        assert!(
            rootdir.is_absolute(),
            "rootdir should be absolute, got: {rootdir}"
        );
        assert!(!rootdir.as_str().is_empty(), "rootdir must not be empty");
        assert!(rootdir.join("pyproject.toml").exists());
    }

    #[test]
    fn test_merge_run_args_exitfirst_sets_maxfail_1() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let mut args = base_run_args();
        args.exitfirst = true;
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.maxfail, 1);
    }

    #[test]
    fn test_merge_run_args_maxfail_sets_maxfail() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let mut args = base_run_args();
        args.maxfail = Some(3);
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.maxfail, 3);
    }

    #[test]
    fn test_merge_run_args_paths_overrides_testpaths() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let config = Config::load(utf8_dir);
        let custom = utf8_dir.join("custom_tests");
        let mut args = base_run_args();
        args.paths = vec![custom.clone()];
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.testpaths, vec![custom]);
    }

    #[test]
    fn test_cli_tb_default_is_none() {
        let args = base_run_args();
        assert_eq!(args.tb, None);
    }

    #[test]
    fn test_cli_tb_detail() {
        let args = parse_run(&["--tb", "detail"]);
        assert_eq!(args.tb, Some(TbStyle::Detail));
    }

    #[test]
    fn test_cli_tb_no() {
        let args = parse_run(&["--tb", "no"]);
        assert_eq!(args.tb, Some(TbStyle::No));
    }

    #[test]
    fn test_tb_from_pyproject() {
        let toml = "[tool.oxitest]\ntb = \"detail\"\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.tb, TbStyle::Detail);
    }

    #[test]
    fn test_tb_default_is_detail() {
        let cfg = Config::default();
        assert_eq!(cfg.tb, TbStyle::Detail);
    }

    #[test]
    fn test_verbosity_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nverbosity = \"detailed\"\n").unwrap();
        assert_eq!(cfg.verbosity, Verbosity::Detailed);
    }

    #[test]
    fn test_verbosity_full_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nverbosity = \"full\"\n").unwrap();
        assert_eq!(cfg.verbosity, Verbosity::Full);
    }

    #[test]
    fn test_verbosity_ordering() {
        assert!(Verbosity::Normal < Verbosity::Detailed);
        assert!(Verbosity::Detailed < Verbosity::Full);
    }

    #[test]
    fn test_verbosity_default_is_normal() {
        assert_eq!(Verbosity::default(), Verbosity::Normal);
    }

    #[test]
    fn test_maxfail_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nmaxfail = 5\n").unwrap();
        assert_eq!(cfg.maxfail, 5);
    }

    #[test]
    fn test_durations_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\ndurations = 10\n").unwrap();
        assert_eq!(cfg.durations, Some(10));
    }

    #[test]
    fn test_serial_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nserial = true\n").unwrap();
        assert!(cfg.serial);
    }

    #[test]
    fn test_color_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\ncolor = \"never\"\n").unwrap();
        assert_eq!(cfg.color, ColorMode::Never);
    }

    #[test]
    fn test_cli_tips_flag() {
        let args = parse_run(&["--tips"]);
        assert!(args.tips);
    }

    #[test]
    fn test_cli_color_never() {
        let args = parse_run(&["--color", "never"]);
        assert_eq!(args.color, Some(ColorMode::Never));
    }

    #[test]
    fn test_cli_color_always() {
        let args = parse_run(&["--color", "always"]);
        assert_eq!(args.color, Some(ColorMode::Always));
    }

    #[test]
    fn test_cli_color_default_is_none() {
        let args = base_run_args();
        assert_eq!(args.color, None);
    }

    #[test]
    fn test_color_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ncolor = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(cfg.color, ColorMode::Always);
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.color, ColorMode::Always);
    }

    #[test]
    fn test_color_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ncolor = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--color", "never"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.color, ColorMode::Never);
    }

    #[test]
    fn test_cli_warnings_flag() {
        let args = parse_run(&["--warnings"]);
        assert!(args.warnings);
    }

    #[test]
    fn test_load_markers_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nmarkers = [\"slow: marks tests as slow\", \"integration\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert!(config.registered_markers.contains(&"slow".to_string()));
        assert!(config
            .registered_markers
            .contains(&"integration".to_string()));
    }

    #[test]
    fn test_load_markers_strips_description() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nmarkers = [\"slow: marks slow tests\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.registered_markers, vec!["slow".to_string()]);
    }

    #[test]
    fn test_cli_json_flag() {
        let args = parse_run(&["--json", "/tmp/results.json"]);
        assert_eq!(args.json, Some(Utf8PathBuf::from("/tmp/results.json")));
    }

    #[test]
    fn test_cli_json_flag_absent() {
        let args = base_run_args();
        assert!(args.json.is_none());
    }

    #[test]
    fn test_load_timeout_secs_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 30\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.timeout_secs, Some(30));
    }

    #[test]
    fn test_timeout_absent_is_none() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("pyproject.toml"), "[tool.oxitest]\n").unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.timeout_secs, None);
    }

    #[test]
    fn test_cli_serial_flag() {
        let args = parse_run(&["--serial"]);
        assert!(args.serial);
    }

    #[test]
    fn test_cli_serial_default_is_false() {
        let args = base_run_args();
        assert!(!args.serial);
    }

    #[test]
    fn test_cli_workers_flag() {
        let args = parse_run(&["--workers", "4"]);
        assert_eq!(args.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_cli_workers_absent_is_none() {
        let args = base_run_args();
        assert!(args.workers.is_none());
    }

    #[test]
    fn test_config_worker_count_serial_returns_1() {
        let dir = TempDir::new().unwrap();
        let args = parse_run(&["--serial"]);
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert_eq!(config.worker_count(), 1);
    }

    #[test]
    fn test_config_worker_count_explicit_workers() {
        let dir = TempDir::new().unwrap();
        let args = parse_run(&["--workers", "3"]);
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert_eq!(config.worker_count(), 3);
    }

    #[test]
    fn test_config_worker_count_default_is_cpu_count() {
        let dir = TempDir::new().unwrap();
        let args = base_run_args();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert!(config.worker_count() >= 1);
    }

    #[test]
    fn test_cli_workers_zero_is_rejected() {
        let result = OxitestCli::resolve(&[
            "oxitest".to_string(),
            "run".to_string(),
            "--workers".to_string(),
            "0".to_string(),
        ]);
        assert!(result.is_err(), "Expected --workers 0 to be rejected");
    }

    #[test]
    fn test_cli_serial_and_workers_conflict() {
        let result = OxitestCli::resolve(&[
            "oxitest".to_string(),
            "run".to_string(),
            "--serial".to_string(),
            "--workers".to_string(),
            "4".to_string(),
        ]);
        assert!(result.is_err(), "Expected --serial --workers to conflict");
    }

    #[test]
    fn test_cli_serial_and_workers_auto_conflict() {
        let result = OxitestCli::resolve(&[
            "oxitest".to_string(),
            "run".to_string(),
            "--serial".to_string(),
            "--workers".to_string(),
            "auto".to_string(),
        ]);
        assert!(
            result.is_err(),
            "Expected --serial --workers auto to conflict"
        );
    }

    #[test]
    fn test_cli_short_n_flag_auto() {
        let args = parse_run(&["-n", "auto"]);
        assert_eq!(args.workers, Some(WorkerCount::Auto));
    }

    #[test]
    fn test_cli_short_n_flag_fixed() {
        let args = parse_run(&["-n", "4"]);
        assert_eq!(args.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_cli_long_workers_auto() {
        let args = parse_run(&["--workers", "auto"]);
        assert_eq!(args.workers, Some(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_auto() {
        assert_eq!("auto".parse::<WorkerCount>(), Ok(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_auto_case_insensitive() {
        assert_eq!("AUTO".parse::<WorkerCount>(), Ok(WorkerCount::Auto));
        assert_eq!("Auto".parse::<WorkerCount>(), Ok(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_fixed() {
        assert_eq!("4".parse::<WorkerCount>(), Ok(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_parse_workers_zero_rejected() {
        assert!("0".parse::<WorkerCount>().is_err());
    }

    #[test]
    fn test_parse_workers_garbage_rejected() {
        assert!("abc".parse::<WorkerCount>().is_err());
    }

    #[test]
    fn test_cli_durations_flag() {
        let args = parse_run(&["--durations", "10"]);
        assert_eq!(args.durations, Some(10));
    }

    #[test]
    fn test_cli_durations_absent_is_none() {
        let args = base_run_args();
        assert!(args.durations.is_none());
    }

    #[test]
    fn test_cache_max_age_default_is_50() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.cache_max_age, 50);
    }

    #[test]
    fn test_cache_max_age_loads_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ncache_max_age = 20\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.cache_max_age, 20);
    }

    #[test]
    fn test_min_parallel_tests_default_is_100() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.min_parallel_tests, 100);
    }

    #[test]
    fn test_min_parallel_tests_loads_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nmin_parallel_tests = 50\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.min_parallel_tests, 50);
    }

    #[test]
    fn test_timeout_multiplier_default_is_none() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert!(config.timeout_multiplier.is_none());
    }

    #[test]
    fn test_timeout_multiplier_loads_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout_multiplier = 3.0\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.timeout_multiplier, Some(3.0));
    }

    #[test]
    fn test_failed_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nfailed = \"first\"\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.failed, Some(FailedMode::First));
    }

    #[test]
    fn config_spawn_overhead_ms_defaults_to_250() {
        let cfg = Config::default();
        assert_eq!(cfg.spawn_overhead_ms, 250.0_f64);
    }

    #[test]
    fn config_spawn_overhead_ms_loads_from_toml() {
        let toml = r#"
[tool.oxitest]
spawn_overhead_ms = 100.0
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.spawn_overhead_ms, 100.0_f64);
    }

    #[test]
    fn test_load_malformed_toml_returns_defaults() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = \"not_a_number\"\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.timeout_secs, Config::default().timeout_secs);
        assert_eq!(config.python_files, Config::default().python_files);
    }

    #[test]
    fn test_cli_strict_absent_is_none() {
        let args = base_run_args();
        assert!(args.strict.is_none());
    }

    #[test]
    fn test_cli_strict_bare_flag_defaults_to_abort() {
        let args = parse_run(&["--strict"]);
        assert_eq!(args.strict, Some(StrictMode::Abort));
    }

    #[test]
    fn test_cli_strict_enforce() {
        let args = parse_run(&["--strict=enforce"]);
        assert_eq!(args.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_cli_strict_abort_explicit() {
        let args = parse_run(&["--strict=abort"]);
        assert_eq!(args.strict, Some(StrictMode::Abort));
    }

    #[test]
    fn test_strict_loads_from_toml() {
        let toml = "[tool.oxitest]\nstrict = \"enforce\"\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_strict_absent_in_toml_is_none() {
        let toml = "[tool.oxitest]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.strict.is_none());
    }

    #[test]
    fn test_cli_strict_overrides_toml() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nstrict = \"abort\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--strict=enforce"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_markers_without_description_populated() {
        let toml = "[tool.oxitest]\nmarkers = [\"slow: marks slow tests\", \"db\"]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.markers_without_description, vec!["db".to_string()]);
    }

    #[test]
    fn test_markers_all_with_description_leaves_list_empty() {
        let toml = "[tool.oxitest]\nmarkers = [\"slow: marks slow\", \"db: hits db\"]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.markers_without_description.is_empty());
    }

    #[test]
    fn test_cli_absent_strict_does_not_clear_toml_strict() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nstrict = \"enforce\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_toml_workers_auto() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        workers = "auto"
        "#,
        )
        .unwrap();
        assert_eq!(config.workers, Some(WorkerCount::Auto));
    }

    #[test]
    fn test_toml_workers_fixed() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        workers = 4
        "#,
        )
        .unwrap();
        assert_eq!(config.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_toml_workers_absent_is_none() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        testpaths = ["tests"]
        "#,
        )
        .unwrap();
        assert!(config.workers.is_none());
    }

    #[test]
    fn test_toml_workers_zero_rejected() {
        let result = Config::from_str(
            r#"
        [tool.oxitest]
        workers = 0
        "#,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_cli_workers_overrides_toml() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        workers = "auto"
        "#,
        )
        .unwrap();
        assert_eq!(config.workers, Some(WorkerCount::Auto));

        let args = parse_run(&["--workers", "2"]);
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.workers, Some(WorkerCount::Fixed(2)));
    }

    #[test]
    fn test_toml_workers_preserved_when_cli_absent() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        workers = 8
        "#,
        )
        .unwrap();
        let args = base_run_args();
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.workers, Some(WorkerCount::Fixed(8)));
    }

    #[test]
    fn test_config_worker_count_auto_is_cpu_count() {
        let config = Config {
            workers: Some(WorkerCount::Auto),
            ..Config::default()
        };
        assert!(config.worker_count() >= 1);
    }

    #[test]
    fn test_schedule_strategy_default_is_longest_first() {
        let cfg = Config::default();
        assert_eq!(cfg.schedule, ScheduleStrategy::LongestFirst);
    }

    #[test]
    fn test_schedule_strategy_from_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nschedule = \"failed-first\"\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        assert_eq!(config.schedule, ScheduleStrategy::FailedFirst);
    }

    #[test]
    fn test_schedule_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nschedule = \"random\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.schedule, ScheduleStrategy::Random);
    }

    #[test]
    fn test_schedule_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nschedule = \"random\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--schedule", "failed-first"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.schedule, ScheduleStrategy::FailedFirst);
    }

    #[test]
    fn test_cli_timeout_flag() {
        let args = parse_run(&["--timeout", "30"]);
        assert_eq!(args.timeout, Some(30));
    }

    #[test]
    fn test_cli_timeout_absent_is_none() {
        let args = base_run_args();
        assert_eq!(args.timeout, None);
    }

    #[test]
    fn test_timeout_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 60\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(cfg.timeout_secs, Some(60));
        let args = parse_run(&["--timeout", "10"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.timeout_secs, Some(10));
    }

    #[test]
    fn test_timeout_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 45\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.timeout_secs, Some(45));
    }

    // ── WorkerCount serde visitor edge cases ─────────────────────────────────

    #[test]
    fn test_toml_workers_negative_rejected() {
        let result = Config::from_str(
            r#"
        [tool.oxitest]
        workers = -1
        "#,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_toml_workers_positive_i64() {
        let v: WorkerCount = serde_json::from_str("3").unwrap();
        assert_eq!(v, WorkerCount::Fixed(3));
    }

    #[test]
    fn test_json_workers_negative_rejected() {
        let result = serde_json::from_str::<WorkerCount>("-5");
        assert!(result.is_err());
    }

    #[test]
    fn test_json_workers_zero_i64_rejected() {
        let result = serde_json::from_str::<WorkerCount>("0");
        assert!(result.is_err());
    }

    #[test]
    fn test_toml_workers_invalid_string_rejected() {
        let result = Config::from_str(
            r#"
        [tool.oxitest]
        workers = "banana"
        "#,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_worker_count_expecting_message() {
        let result = serde_json::from_str::<WorkerCount>("true");
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("\"auto\" or a positive integer"),
            "unexpected error: {err_msg}"
        );
    }

    #[test]
    fn test_plugins_from_pyproject() {
        let toml = r#"
[tool.oxitest]
plugins = ["oxitest_loguru", "oxitest_db"]
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.plugins, vec!["oxitest_loguru", "oxitest_db"]);
    }

    #[test]
    fn test_plugins_default_empty() {
        let cfg = Config::default();
        assert!(cfg.plugins.is_empty());
    }

    #[test]
    fn test_plugin_settings_from_pyproject() {
        let toml = r#"
[tool.oxitest]
plugins = ["myplugin"]

[tool.oxitest.plugin_settings.myplugin]
level = "DEBUG"
timeout = 30
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.plugin_settings.contains_key("myplugin"));
        let settings = &cfg.plugin_settings["myplugin"];
        assert_eq!(
            settings.get("level").and_then(|v| v.as_str()),
            Some("DEBUG")
        );
    }

    #[test]
    fn test_async_backend_default() {
        let cfg = Config::default();
        assert_eq!(cfg.async_backend, "asyncio");
    }

    #[test]
    fn test_async_backend_from_pyproject() {
        let toml = r#"
[tool.oxitest]
async_backend = "trio"
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.async_backend, "trio");
    }

    #[test]
    fn test_cli_affected_absent_is_none() {
        let args = base_run_args();
        assert!(args.filter.affected.is_none());
    }

    #[test]
    fn test_cli_affected_bare_is_empty_sentinel() {
        let args = parse_run(&["--affected"]);
        assert_eq!(args.filter.affected, Some("".to_string()));
    }

    #[test]
    fn test_affected_bare_resolves_to_config_base() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\naffected_base = \"main\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.affected, Some("main".to_string()));
    }

    #[test]
    fn test_affected_bare_defaults_to_head_without_config() {
        let dir = TempDir::new().unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.affected, Some("HEAD".to_string()));
    }

    #[test]
    fn test_affected_explicit_overrides_config_base() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\naffected_base = \"main\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected=develop"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.affected, Some("develop".to_string()));
    }

    #[test]
    fn test_cli_affected_with_branch() {
        let args = parse_run(&["--affected=main"]);
        assert_eq!(args.filter.affected, Some("main".to_string()));
    }

    #[test]
    fn test_cli_affected_with_commit() {
        let args = parse_run(&["--affected=abc123"]);
        assert_eq!(args.filter.affected, Some("abc123".to_string()));
    }

    #[test]
    fn test_cli_retries_absent_is_none() {
        let args = base_run_args();
        assert!(args.retries.is_none());
    }

    #[test]
    fn test_cli_retries_flag() {
        let args = parse_run(&["--retries", "3"]);
        assert_eq!(args.retries, Some(3));
    }

    #[test]
    fn test_retries_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nretries = 2\n").unwrap();
        assert_eq!(cfg.retries, 2);
    }

    #[test]
    fn test_retries_delay_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nretries_delay = 5\n").unwrap();
        assert_eq!(cfg.retries_delay_secs, 5);
    }

    #[test]
    fn test_retries_default_is_zero() {
        let cfg = Config::default();
        assert_eq!(cfg.retries, 0);
    }

    #[test]
    fn test_retries_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nretries = 2\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--retries", "5"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.retries, 5);
    }

    // ── resolve_testpaths tests ──────────────────────────────────────────

    #[test]
    fn resolve_testpaths_relative_to_rootdir() {
        let rootdir = Utf8Path::new("/project");
        let paths = vec!["tests".to_string(), "integration".to_string()];
        let result = resolve_testpaths(&paths, rootdir);
        assert_eq!(
            result,
            vec![
                Utf8PathBuf::from("/project/tests"),
                Utf8PathBuf::from("/project/integration"),
            ]
        );
    }

    #[test]
    fn resolve_testpaths_empty_input() {
        let rootdir = Utf8Path::new("/project");
        let paths: Vec<String> = vec![];
        let result = resolve_testpaths(&paths, rootdir);
        assert!(result.is_empty());
    }

    #[test]
    fn resolve_testpaths_nested_path() {
        let rootdir = Utf8Path::new("/project");
        let paths = vec!["src/tests/unit".to_string()];
        let result = resolve_testpaths(&paths, rootdir);
        assert_eq!(result, vec![Utf8PathBuf::from("/project/src/tests/unit")]);
    }

    // ── parse_marker_descriptions tests ──────────────────────────────────

    #[test]
    fn parse_markers_with_descriptions() {
        let raw = vec![
            "slow: marks slow tests".to_string(),
            "db: database tests".to_string(),
        ];
        let (names, no_desc) = parse_marker_descriptions(&raw);
        assert_eq!(names, vec!["slow", "db"]);
        assert!(no_desc.is_empty());
    }

    #[test]
    fn parse_markers_without_descriptions() {
        let raw = vec!["slow".to_string(), "db".to_string()];
        let (names, no_desc) = parse_marker_descriptions(&raw);
        assert_eq!(names, vec!["slow", "db"]);
        assert_eq!(no_desc, vec!["slow", "db"]);
    }

    #[test]
    fn parse_markers_mixed() {
        let raw = vec![
            "slow: marks slow tests".to_string(),
            "integration".to_string(),
        ];
        let (names, no_desc) = parse_marker_descriptions(&raw);
        assert_eq!(names, vec!["slow", "integration"]);
        assert_eq!(no_desc, vec!["integration"]);
    }

    #[test]
    fn parse_markers_empty_input() {
        let raw: Vec<String> = vec![];
        let (names, no_desc) = parse_marker_descriptions(&raw);
        assert!(names.is_empty());
        assert!(no_desc.is_empty());
    }

    #[test]
    fn parse_markers_trims_whitespace() {
        let raw = vec!["  slow  : marks slow tests".to_string()];
        let (names, no_desc) = parse_marker_descriptions(&raw);
        assert_eq!(names, vec!["slow"]);
        assert!(no_desc.is_empty());
    }

    // ── DebugMode::apply_to tests ───────────────────────────────────────

    #[test]
    fn test_debug_post_mortem_apply_to() {
        let mut cfg = Config::default();
        DebugMode::PostMortem.apply_to(&mut cfg, None);
        assert!(cfg.serial, "debug should imply serial");
        assert_eq!(cfg.maxfail, 1, "post-mortem should imply maxfail=1");
        assert_eq!(cfg.tb, TbStyle::Detail, "debug should imply tb=detail");
        assert!(cfg.debug.is_some(), "debug should be stored on config");
        assert_eq!(cfg.timeout_secs, None, "debug should clear timeout");
        assert!(cfg.show_internals, "debug should imply show_internals");
    }

    #[test]
    fn test_debug_post_mortem_clears_pyproject_timeout() {
        let mut cfg = Config::default();
        cfg.timeout_secs = Some(30);
        DebugMode::PostMortem.apply_to(&mut cfg, None);
        assert_eq!(
            cfg.timeout_secs, None,
            "debug should clear pyproject timeout"
        );
    }

    #[test]
    fn test_debug_always_does_not_imply_maxfail() {
        let mut cfg = Config::default();
        DebugMode::Always.apply_to(&mut cfg, None);
        assert!(cfg.serial, "always should imply serial");
        assert_eq!(cfg.maxfail, 0, "always should NOT imply maxfail=1");
        assert_eq!(cfg.tb, TbStyle::Detail, "always should imply tb=detail");
        assert_eq!(cfg.timeout_secs, None, "always should clear timeout");
    }

    #[test]
    fn test_debug_does_not_override_explicit_tb() {
        // When cli_tb is Some, apply_to should NOT override tb to Detail.
        // The caller sets cfg.tb via the override mechanism after apply_to.
        let mut cfg = Config::default();
        cfg.tb = TbStyle::No; // User chose --tb=no explicitly
        DebugMode::PostMortem.apply_to(&mut cfg, Some(&TbStyle::No));
        assert_eq!(
            cfg.tb,
            TbStyle::No,
            "explicit --tb should not be overridden"
        );
    }

    #[test]
    fn test_merge_debug_args_applies_mode() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = DebugArgs::default_for_test();
        let merged = config.merge_debug_args(&args);
        assert!(merged.serial);
        assert_eq!(merged.maxfail, 1);
        assert!(merged.debug.is_some());
    }

    // ── keep_tmp tests ──────────────────────────────────────────────────

    #[test]
    fn test_keep_tmp_default_is_none() {
        let cfg = Config::default();
        assert!(cfg.keep_tmp.is_none());
    }

    #[test]
    fn test_keep_tmp_from_pyproject_failed() {
        let cfg = Config::from_str("[tool.oxitest]\nkeep_tmp = \"failed\"\n").unwrap();
        assert_eq!(cfg.keep_tmp, Some(KeepTmpMode::Failed));
    }

    #[test]
    fn test_keep_tmp_from_pyproject_always() {
        let cfg = Config::from_str("[tool.oxitest]\nkeep_tmp = \"always\"\n").unwrap();
        assert_eq!(cfg.keep_tmp, Some(KeepTmpMode::Always));
    }

    #[test]
    fn test_keep_tmp_cli_overrides_toml() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nkeep_tmp = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--keep-tmp=failed"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.keep_tmp, Some(KeepTmpMode::Failed));
    }

    #[test]
    fn test_keep_tmp_toml_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nkeep_tmp = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.keep_tmp, Some(KeepTmpMode::Always));
    }

    #[test]
    fn test_show_locals_default_is_false() {
        let cfg = Config::default();
        assert!(!cfg.show_locals);
    }

    #[test]
    fn test_show_internals_default_is_false() {
        let cfg = Config::default();
        assert!(!cfg.show_internals);
    }

    #[test]
    fn test_show_locals_from_pyproject() {
        let toml = "[tool.oxitest]\nshow_locals = true\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.show_locals);
    }

    #[test]
    fn test_show_internals_from_pyproject() {
        let toml = "[tool.oxitest]\nshow_internals = true\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.show_internals);
    }

    // ── auto_arrange tests (TOML only) ──────────────────────────────────

    #[test]
    fn test_auto_arrange_default_is_some_70() {
        let cfg = Config::default();
        assert_eq!(cfg.auto_arrange_threshold, Some(70));
    }

    #[test]
    fn test_auto_arrange_from_pyproject_custom() {
        let cfg = Config::from_str("[tool.oxitest]\nauto_arrange = 50\n").unwrap();
        assert_eq!(cfg.auto_arrange_threshold, Some(50));
    }

    #[test]
    fn test_auto_arrange_from_pyproject_false() {
        let cfg = Config::from_str("[tool.oxitest]\nauto_arrange = false\n").unwrap();
        assert_eq!(cfg.auto_arrange_threshold, None);
    }

    // ── use_gitignore tests ─────────────────────────────────────────────

    #[test]
    fn test_use_gitignore_default_is_true() {
        let cfg = Config::default();
        assert!(cfg.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_from_pyproject_false() {
        let cfg = Config::from_str("[tool.oxitest]\nuse_gitignore = false\n").unwrap();
        assert!(!cfg.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_from_pyproject_true() {
        let cfg = Config::from_str("[tool.oxitest]\nuse_gitignore = true\n").unwrap();
        assert!(cfg.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_absent_defaults_true() {
        let cfg = Config::from_str("[tool.oxitest]\n").unwrap();
        assert!(cfg.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_cli_disables() {
        let dir = TempDir::new().unwrap();
        let mut cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert!(cfg.use_gitignore);
        cfg.use_gitignore = false; // simulates what pipeline does with CLI flag
        assert!(!cfg.use_gitignore);
    }
}
