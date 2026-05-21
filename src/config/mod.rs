use camino::{Utf8Path, Utf8PathBuf};

mod cli;
pub use cli::Cli;

mod pyproject;
use pyproject::{OxitestConfig, PyprojectToml};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerCount {
    Auto,
    Fixed(usize),
}

fn parse_workers(s: &str) -> Result<WorkerCount, String> {
    if s.eq_ignore_ascii_case("auto") {
        return Ok(WorkerCount::Auto);
    }
    let n: usize = s
        .parse()
        .map_err(|_| format!("expected \"auto\" or a positive integer, got \"{s}\""))?;
    if n == 0 {
        return Err("worker count must be at least 1".into());
    }
    Ok(WorkerCount::Fixed(n))
}

#[derive(clap::ValueEnum, serde::Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TbStyle {
    Long,
    Short,
    Line,
    No,
}

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
    pub verbose: bool,
    pub durations: Option<usize>,
    pub color: ColorMode,
    pub plugins: Vec<String>,
    pub plugin_settings: std::collections::HashMap<String, toml::Value>,
    pub async_backend: String,
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
            workers: None,
            cache_max_age: 50,
            min_parallel_tests: 100,
            timeout_multiplier: None,
            spawn_overhead_ms: 250.0,
            strict: None,
            markers_without_description: vec![],
            schedule: ScheduleStrategy::LongestFirst,
            failed: None,
            tb: TbStyle::Short,
            verbose: false,
            durations: None,
            color: ColorMode::Auto,
            plugins: vec![],
            plugin_settings: std::collections::HashMap::new(),
            async_backend: "asyncio".to_string(),
        }
    }
}

/// Apply fields from a parsed `[tool.oxitest]` section onto `config`.
/// `rootdir` controls how `testpaths` are resolved:
///   - `Some(root)` → each path is joined to root (used by `Config::load`)
///   - `None`       → each path is taken as-is (used by `Config::from_str`)
fn apply_oxitest_config(config: &mut Config, tc: OxitestConfig, rootdir: Option<&Utf8Path>) {
    if let Some(paths) = tc.testpaths {
        config.testpaths = match rootdir {
            Some(root) => paths.iter().map(|s| root.join(s)).collect(),
            None => paths.into_iter().map(Utf8PathBuf::from).collect(),
        };
    }
    if let Some(files) = tc.python_files {
        config.python_files = files;
    }
    if let Some(dirs) = tc.norecursedirs {
        config.norecursedirs = dirs;
    }
    if let Some(raw_markers) = tc.markers {
        let mut names = Vec::new();
        let mut no_desc = Vec::new();
        for s in raw_markers {
            if let Some((name, _)) = s.split_once(':') {
                names.push(name.trim().to_string());
            } else {
                let name = s.trim().to_string();
                no_desc.push(name.clone());
                names.push(name);
            }
        }
        config.registered_markers = names;
        config.markers_without_description = no_desc;
    }
    if let Some(s) = tc.strict {
        config.strict = Some(s);
    }
    config.timeout_secs = tc.timeout;
    config.cache_max_age = tc.cache_max_age.unwrap_or(config.cache_max_age);
    config.min_parallel_tests = tc.min_parallel_tests.unwrap_or(config.min_parallel_tests);
    config.timeout_multiplier = tc.timeout_multiplier;
    config.spawn_overhead_ms = tc.spawn_overhead_ms.unwrap_or(config.spawn_overhead_ms);
    if tc.workers.is_some() {
        config.workers = tc.workers;
    }
    if let Some(s) = tc.schedule {
        config.schedule = s;
    }
    if let Some(f) = tc.failed {
        config.failed = Some(f);
    }
    if let Some(tb) = tc.tb {
        config.tb = tb;
    }
    if let Some(v) = tc.verbose {
        config.verbose = v;
    }
    if let Some(n) = tc.maxfail {
        config.maxfail = n;
    }
    if tc.durations.is_some() {
        config.durations = tc.durations;
    }
    if let Some(s) = tc.serial {
        config.serial = s;
    }
    if let Some(c) = tc.color {
        config.color = c;
    }
    if let Some(plugins) = tc.plugins {
        config.plugins = plugins;
    }
    config.plugin_settings = tc.plugin_settings;
    if let Some(ab) = tc.async_backend {
        config.async_backend = ab;
    }
}

pub fn find_rootdir(start: Option<&Utf8Path>) -> Utf8PathBuf {
    let start = start.unwrap_or(Utf8Path::new("."));
    let start = if start.is_file() {
        start.parent().unwrap_or(start)
    } else {
        start
    };

    let mut current = start.to_owned();
    loop {
        if current.join("pyproject.toml").exists()
            || current.join("setup.cfg").exists()
            || current.join("tox.ini").exists()
        {
            return current;
        }
        match current.parent() {
            Some(parent) if parent != current.as_path() => current = parent.to_owned(),
            _ => return start.to_owned(),
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
        let mut config = Config {
            rootdir: rootdir.to_owned(),
            testpaths: vec![rootdir.to_owned()],
            ..Config::default()
        };

        let pyproject_path = rootdir.join("pyproject.toml");
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
        apply_oxitest_config(&mut config, tc, Some(rootdir));
        config
    }

    pub fn merge_cli(mut self, cli: &Cli) -> Self {
        if !cli.paths.is_empty() {
            self.testpaths = cli
                .paths
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
        if cli.exitfirst {
            self.maxfail = 1;
        } else if let Some(n) = cli.maxfail {
            if n > 0 {
                self.maxfail = n;
            }
        }
        if cli.serial {
            self.serial = true;
        }
        if cli.verbose {
            self.verbose = true;
        }
        if cli.durations.is_some() {
            self.durations = cli.durations;
        }
        if let Some(c) = cli.color {
            self.color = c;
        }
        if cli.workers.is_some() {
            self.workers = cli.workers;
        }
        if cli.strict.is_some() {
            self.strict = cli.strict.clone();
        }
        if let Some(schedule) = cli.schedule {
            self.schedule = schedule;
        }
        if cli.failed.is_some() {
            self.failed = cli.failed;
        }
        if let Some(tb) = cli.tb.clone() {
            self.tb = tb;
        }
        if let Some(timeout) = cli.timeout {
            self.timeout_secs = Some(timeout);
        }
        self
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

#[cfg(test)]
impl Config {
    pub fn from_str(s: &str) -> Result<Self, toml::de::Error> {
        let pyproject: PyprojectToml = toml::from_str(s)?;
        let tc = pyproject.tool.and_then(|t| t.oxitest).unwrap_or_default();
        let mut config = Config::default();
        apply_oxitest_config(&mut config, tc, None);
        Ok(config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::{Utf8Path, Utf8PathBuf};
    use clap::Parser;
    use std::fs;
    use tempfile::TempDir;

    /// Returns a `Cli` parsed from no flags — all fields at their defaults.
    /// Use struct-update syntax (`Cli { field: val, ..base_cli() }`) to override
    /// only the field under test, so adding a new flag never breaks these tests.
    fn base_cli() -> Cli {
        Cli::try_parse_from(["oxitest"]).expect("default CLI parse must succeed")
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
    fn test_pytest_section_is_ignored() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.pytest]\ntestpaths = [\"tests\"]\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        // [tool.pytest] must not be read — only [tool.oxitest] is supported
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
    fn test_merge_cli_exitfirst_sets_maxfail_1() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let cli = Cli {
            exitfirst: true,
            ..base_cli()
        };
        let merged = config.merge_cli(&cli);
        assert_eq!(merged.maxfail, 1);
    }

    #[test]
    #[test]
    fn test_merge_cli_maxfail_sets_maxfail() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let cli = Cli {
            maxfail: Some(3),
            ..base_cli()
        };
        let merged = config.merge_cli(&cli);
        assert_eq!(merged.maxfail, 3);
    }

    #[test]
    fn test_merge_cli_paths_overrides_testpaths() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let config = Config::load(utf8_dir);
        let custom = utf8_dir.join("custom_tests");
        let cli = Cli {
            paths: vec![custom.clone()],
            ..base_cli()
        };
        let merged = config.merge_cli(&cli);
        assert_eq!(merged.testpaths, vec![custom]);
    }

    #[test]
    fn test_cli_tb_default_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.tb, None);
    }

    #[test]
    fn test_cli_tb_short() {
        let cli = Cli::try_parse_from(["oxitest", "--tb", "short"]).unwrap();
        assert_eq!(cli.tb, Some(TbStyle::Short));
    }

    #[test]
    fn test_cli_tb_no() {
        let cli = Cli::try_parse_from(["oxitest", "--tb", "no"]).unwrap();
        assert_eq!(cli.tb, Some(TbStyle::No));
    }

    #[test]
    fn test_cli_tb_long() {
        let cli = Cli::try_parse_from(["oxitest", "--tb", "long"]).unwrap();
        assert_eq!(cli.tb, Some(TbStyle::Long));
    }

    #[test]
    fn test_tb_from_pyproject() {
        let toml = "[tool.oxitest]\ntb = \"long\"\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.tb, TbStyle::Long);
    }

    #[test]
    fn test_tb_default_is_short() {
        let cfg = Config::default();
        assert_eq!(cfg.tb, TbStyle::Short);
    }

    #[test]
    fn test_verbose_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nverbose = true\n").unwrap();
        assert!(cfg.verbose);
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
        let cli = Cli::try_parse_from(["oxitest", "--tips"]).unwrap();
        assert!(cli.tips);
    }

    #[test]
    fn test_cli_color_never() {
        let cli = Cli::try_parse_from(["oxitest", "--color", "never"]).unwrap();
        assert_eq!(cli.color, Some(ColorMode::Never));
    }

    #[test]
    fn test_cli_color_always() {
        let cli = Cli::try_parse_from(["oxitest", "--color", "always"]).unwrap();
        assert_eq!(cli.color, Some(ColorMode::Always));
    }

    #[test]
    fn test_cli_color_default_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.color, None);
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
        assert_eq!(
            cfg.color,
            ColorMode::Always,
            "pyproject color must load correctly"
        );
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.color,
            ColorMode::Always,
            "pyproject color must be preserved when CLI does not specify --color"
        );
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
        let cli = Cli::try_parse_from(["oxitest", "--color", "never"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.color,
            ColorMode::Never,
            "CLI --color must override pyproject value"
        );
    }

    #[test]
    fn test_cli_warnings_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--warnings"]).unwrap();
        assert!(cli.warnings);
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
        // Only the name before ":" is stored
        assert_eq!(config.registered_markers, vec!["slow".to_string()]);
    }

    #[test]
    fn test_cli_marker_flag() {
        let cli = Cli::try_parse_from(["oxitest", "-m", "slow"]).unwrap();
        assert_eq!(cli.marker, Some("slow".to_string()));
    }

    #[test]
    fn test_cli_marker_flag_long() {
        let cli = Cli::try_parse_from(["oxitest", "--marker", "slow and not integration"]).unwrap();
        assert_eq!(cli.marker, Some("slow and not integration".to_string()));
    }

    #[test]
    fn test_cli_json_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--json", "/tmp/results.json"]).unwrap();
        assert_eq!(cli.json, Some(Utf8PathBuf::from("/tmp/results.json")));
    }

    #[test]
    fn test_cli_json_flag_absent() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.json.is_none());
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
        let cli = Cli::try_parse_from(["oxitest", "--serial"]).unwrap();
        assert!(cli.serial);
    }

    #[test]
    fn test_cli_serial_default_is_false() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(!cli.serial);
    }

    #[test]
    fn test_cli_workers_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--workers", "4"]).unwrap();
        assert_eq!(cli.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_cli_workers_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.workers.is_none());
    }

    #[test]
    fn test_config_worker_count_serial_returns_1() {
        let dir = TempDir::new().unwrap();
        let cli = Cli::try_parse_from(["oxitest", "--serial"]).unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_cli(&cli);
        assert_eq!(config.worker_count(), 1);
    }

    #[test]
    fn test_config_worker_count_explicit_workers() {
        let dir = TempDir::new().unwrap();
        let cli = Cli::try_parse_from(["oxitest", "--workers", "3"]).unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_cli(&cli);
        assert_eq!(config.worker_count(), 3);
    }

    #[test]
    fn test_config_worker_count_default_is_cpu_count() {
        let dir = TempDir::new().unwrap();
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap()).merge_cli(&cli);
        // Must be >= 1
        assert!(config.worker_count() >= 1);
    }

    #[test]
    fn test_cli_workers_zero_is_rejected() {
        let result = Cli::try_parse_from(["oxitest", "--workers", "0"]);
        assert!(result.is_err(), "Expected --workers 0 to be rejected");
    }

    #[test]
    fn test_cli_serial_and_workers_conflict() {
        let result = Cli::try_parse_from(["oxitest", "--serial", "--workers", "4"]);
        assert!(result.is_err(), "Expected --serial --workers to conflict");
    }

    #[test]
    fn test_cli_serial_and_workers_auto_conflict() {
        let result = Cli::try_parse_from(["oxitest", "--serial", "--workers", "auto"]);
        assert!(
            result.is_err(),
            "Expected --serial --workers auto to conflict"
        );
    }

    #[test]
    fn test_cli_short_n_flag_auto() {
        let cli = Cli::try_parse_from(["oxitest", "-n", "auto"]).unwrap();
        assert_eq!(cli.workers, Some(WorkerCount::Auto));
    }

    #[test]
    fn test_cli_short_n_flag_fixed() {
        let cli = Cli::try_parse_from(["oxitest", "-n", "4"]).unwrap();
        assert_eq!(cli.workers, Some(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_cli_long_workers_auto() {
        let cli = Cli::try_parse_from(["oxitest", "--workers", "auto"]).unwrap();
        assert_eq!(cli.workers, Some(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_auto() {
        assert_eq!(parse_workers("auto"), Ok(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_auto_case_insensitive() {
        assert_eq!(parse_workers("AUTO"), Ok(WorkerCount::Auto));
        assert_eq!(parse_workers("Auto"), Ok(WorkerCount::Auto));
    }

    #[test]
    fn test_parse_workers_fixed() {
        assert_eq!(parse_workers("4"), Ok(WorkerCount::Fixed(4)));
    }

    #[test]
    fn test_parse_workers_zero_rejected() {
        assert!(parse_workers("0").is_err());
    }

    #[test]
    fn test_parse_workers_garbage_rejected() {
        assert!(parse_workers("abc").is_err());
    }

    #[test]
    fn test_cli_durations_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--durations", "10"]).unwrap();
        assert_eq!(cli.durations, Some(10));
    }

    #[test]
    fn test_cli_durations_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.durations.is_none());
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
    fn test_failed_flag_only() {
        let cli = Cli::try_parse_from(["oxitest", "--failed=only"]).unwrap();
        assert_eq!(cli.failed, Some(FailedMode::Only));
    }

    #[test]
    fn test_failed_flag_first() {
        let cli = Cli::try_parse_from(["oxitest", "--failed=first"]).unwrap();
        assert_eq!(cli.failed, Some(FailedMode::First));
    }

    #[test]
    fn test_no_failed_flag() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.failed, None);
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
        // timeout expects u64 but receives a string — toml::from_str will Err
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = \"not_a_number\"\n",
        )
        .unwrap();
        let config = Config::load(utf8_dir);
        // Must fall back to defaults, not crash
        assert_eq!(config.timeout_secs, Config::default().timeout_secs);
        assert_eq!(config.python_files, Config::default().python_files);
    }

    #[test]
    fn test_cli_strict_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(cli.strict.is_none());
    }

    #[test]
    fn test_cli_strict_bare_flag_defaults_to_abort() {
        let cli = Cli::try_parse_from(["oxitest", "--strict"]).unwrap();
        assert_eq!(cli.strict, Some(StrictMode::Abort));
    }

    #[test]
    fn test_cli_strict_enforce() {
        let cli = Cli::try_parse_from(["oxitest", "--strict=enforce"]).unwrap();
        assert_eq!(cli.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_cli_strict_abort_explicit() {
        let cli = Cli::try_parse_from(["oxitest", "--strict=abort"]).unwrap();
        assert_eq!(cli.strict, Some(StrictMode::Abort));
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
        let cli = Cli::try_parse_from(["oxitest", "--strict=enforce"]).unwrap();
        let merged = cfg.merge_cli(&cli);
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
        // No --strict flag on CLI — TOML value must be preserved.
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(merged.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_cli_capture_environment_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--capture-environment"]).unwrap();
        assert!(cli.capture_environment);
    }

    #[test]
    fn test_cli_capture_environment_absent_is_false() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(!cli.capture_environment);
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

        let cli = Cli::try_parse_from(["oxitest", "--workers", "2"]).unwrap();
        let merged = config.merge_cli(&cli);
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
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let merged = config.merge_cli(&cli);
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
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.schedule,
            ScheduleStrategy::Random,
            "pyproject schedule must be preserved when CLI does not specify --schedule"
        );
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
        let cli = Cli::try_parse_from(["oxitest", "--schedule", "failed-first"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.schedule,
            ScheduleStrategy::FailedFirst,
            "CLI --schedule must override pyproject value"
        );
    }

    #[test]
    fn test_cli_timeout_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--timeout", "30"]).unwrap();
        assert_eq!(cli.timeout, Some(30));
    }

    #[test]
    fn test_cli_timeout_absent_is_none() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.timeout, None);
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
        let cli = Cli::try_parse_from(["oxitest", "--timeout", "10"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.timeout_secs,
            Some(10),
            "CLI --timeout must override pyproject value"
        );
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
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        let merged = cfg.merge_cli(&cli);
        assert_eq!(
            merged.timeout_secs,
            Some(45),
            "pyproject timeout must be preserved when CLI does not specify --timeout"
        );
    }

    // ── WorkerCount serde visitor edge cases ─────────────────────────────────

    #[test]
    fn test_toml_workers_negative_rejected() {
        // TOML parses negative integers as i64 → exercises visit_i64 error path
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
        // TOML may route small positive integers through visit_i64 on some
        // deserializer implementations; this exercises the success path.
        // (toml crate uses visit_u64 for positives, so this tests via from_str
        // with serde_json which does use visit_i64)
        let v: WorkerCount = serde_json::from_str("3").unwrap();
        assert_eq!(v, WorkerCount::Fixed(3));
    }

    #[test]
    fn test_json_workers_negative_rejected() {
        // JSON integers can be negative → exercises visit_i64 error path
        let result = serde_json::from_str::<WorkerCount>("-5");
        assert!(result.is_err());
    }

    #[test]
    fn test_json_workers_zero_i64_rejected() {
        // JSON zero as i64 → exercises visit_i64 with v <= 0
        let result = serde_json::from_str::<WorkerCount>("0");
        assert!(result.is_err());
    }

    #[test]
    fn test_toml_workers_invalid_string_rejected() {
        // Exercises visit_str error path (string that isn't "auto")
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
        // Exercises the `expecting` method (triggered by type mismatch)
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
}
