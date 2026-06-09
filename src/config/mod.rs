use camino::{Utf8Path, Utf8PathBuf};

mod cli;
pub use cli::{Command, DebugArgs, DebugMode, OxitestCli, QueryArgs, QueryFormat, RunArgs};

mod merge;

mod pyproject;
use pyproject::PyprojectToml;

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
    use tempfile::TempDir;

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
    fn test_find_rootdir_finds_pyproject() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        std::fs::write(dir.path().join("pyproject.toml"), "").unwrap();
        let subdir = utf8_dir.join("tests");
        std::fs::create_dir(&subdir).unwrap();
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
        std::fs::write(dir.path().join("pyproject.toml"), "").unwrap();
        let subdir = utf8_dir.join("a").join("b");
        std::fs::create_dir_all(&subdir).unwrap();
        let rootdir = find_rootdir(Some(&subdir));
        assert!(
            rootdir.is_absolute(),
            "rootdir should be absolute, got: {rootdir}"
        );
        assert!(!rootdir.as_str().is_empty(), "rootdir must not be empty");
        assert!(rootdir.join("pyproject.toml").exists());
    }

    #[test]
    fn test_tb_default_is_detail() {
        let cfg = Config::default();
        assert_eq!(cfg.tb, TbStyle::Detail);
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
    fn test_cli_tb_default_is_none() {
        let args = RunArgs::default_for_test();
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
        let args = RunArgs::default_for_test();
        assert_eq!(args.color, None);
    }

    #[test]
    fn test_cli_warnings_flag() {
        let args = parse_run(&["--warnings"]);
        assert!(args.warnings);
    }

    #[test]
    fn test_cli_json_flag() {
        let args = parse_run(&["--json", "/tmp/results.json"]);
        assert_eq!(args.json, Some(Utf8PathBuf::from("/tmp/results.json")));
    }

    #[test]
    fn test_cli_json_flag_absent() {
        let args = RunArgs::default_for_test();
        assert!(args.json.is_none());
    }

    #[test]
    fn test_cli_serial_flag() {
        let args = parse_run(&["--serial"]);
        assert!(args.serial);
    }

    #[test]
    fn test_cli_serial_default_is_false() {
        let args = RunArgs::default_for_test();
        assert!(!args.serial);
    }

    #[test]
    fn test_cli_workers_flag() {
        let args = parse_run(&["--workers", "4"]);
        assert_eq!(args.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_cli_workers_absent_is_none() {
        let args = RunArgs::default_for_test();
        assert!(args.workers.is_none());
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
        let args = RunArgs::default_for_test();
        assert!(args.durations.is_none());
    }

    #[test]
    fn test_cache_max_age_default_is_50() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.cache_max_age, 50);
    }

    #[test]
    fn test_min_parallel_tests_default_is_100() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.min_parallel_tests, 100);
    }

    #[test]
    fn config_spawn_overhead_ms_defaults_to_250() {
        let cfg = Config::default();
        assert_eq!(cfg.spawn_overhead_ms, 250.0_f64);
    }

    #[test]
    fn test_cli_strict_absent_is_none() {
        let args = RunArgs::default_for_test();
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
    fn test_cli_affected_absent_is_none() {
        let args = RunArgs::default_for_test();
        assert!(args.filter.affected.is_none());
    }

    #[test]
    fn test_cli_affected_bare_is_empty_sentinel() {
        let args = parse_run(&["--affected"]);
        assert_eq!(args.filter.affected, Some("".to_string()));
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
        let args = RunArgs::default_for_test();
        assert!(args.retries.is_none());
    }

    #[test]
    fn test_cli_retries_flag() {
        let args = parse_run(&["--retries", "3"]);
        assert_eq!(args.retries, Some(3));
    }

    #[test]
    fn test_retries_default_is_zero() {
        let cfg = Config::default();
        assert_eq!(cfg.retries, 0);
    }

    #[test]
    fn test_cli_timeout_flag() {
        let args = parse_run(&["--timeout", "30"]);
        assert_eq!(args.timeout, Some(30));
    }

    #[test]
    fn test_cli_timeout_absent_is_none() {
        let args = RunArgs::default_for_test();
        assert_eq!(args.timeout, None);
    }

    // ── WorkerCount serde visitor edge cases ─────────────────────────────────

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
    fn test_plugins_default_empty() {
        let cfg = Config::default();
        assert!(cfg.plugins.is_empty());
    }

    #[test]
    fn test_async_backend_default() {
        let cfg = Config::default();
        assert_eq!(cfg.async_backend, "asyncio");
    }

    #[test]
    fn test_schedule_strategy_default_is_longest_first() {
        let cfg = Config::default();
        assert_eq!(cfg.schedule, ScheduleStrategy::LongestFirst);
    }

    #[test]
    fn test_keep_tmp_default_is_none() {
        let cfg = Config::default();
        assert!(cfg.keep_tmp.is_none());
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
    fn test_auto_arrange_default_is_some_70() {
        let cfg = Config::default();
        assert_eq!(cfg.auto_arrange_threshold, Some(70));
    }

    #[test]
    fn test_use_gitignore_default_is_true() {
        let cfg = Config::default();
        assert!(cfg.use_gitignore);
    }
}
