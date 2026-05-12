use camino::{Utf8Path, Utf8PathBuf};
use clap::Parser;
use serde::Deserialize;

#[derive(Deserialize, Default)]
struct PyprojectToml {
    tool: Option<ToolTable>,
}

#[derive(Deserialize, Default)]
struct ToolTable {
    oxitest: Option<OxitestConfig>,
}

#[derive(Deserialize, Default)]
struct OxitestConfig {
    testpaths: Option<Vec<String>>,
    python_files: Option<Vec<String>>,
    norecursedirs: Option<Vec<String>>,
    markers: Option<Vec<String>>,
    timeout: Option<u64>,
    cache_max_age: Option<u32>,
    min_parallel_tests: Option<usize>,
    timeout_multiplier: Option<f64>,
    spawn_overhead_ms: Option<f64>,
    strict: Option<StrictMode>,
}

fn parse_workers(s: &str) -> Result<usize, String> {
    let n: usize = s
        .parse()
        .map_err(|_| format!("'{s}' is not a valid number"))?;
    if n == 0 {
        return Err("--workers must be at least 1".to_string());
    }
    Ok(n)
}

#[derive(clap::ValueEnum, Debug, Clone, PartialEq)]
pub enum TbStyle {
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
    #[arg(long, default_value = "0", value_name = "NUM")]
    pub maxfail: usize,

    /// Traceback style: short (default), line, no
    #[arg(long, value_enum, default_value = "short")]
    pub tb: TbStyle,

    /// Expand assertions-without-messages tip list
    #[arg(long)]
    pub tips: bool,

    /// Expand captured Python warnings list
    #[arg(long)]
    pub warnings: bool,

    /// Disable color output
    #[arg(long)]
    pub no_color: bool,

    /// Write CTRF JSON results to PATH
    #[arg(long, value_name = "PATH")]
    pub json: Option<Utf8PathBuf>,

    /// Only run tests matching the marker expression
    #[arg(short = 'm', long = "marker", value_name = "EXPR")]
    pub marker: Option<String>,

    /// Run tests serially (single process, no workers)
    #[arg(long)]
    pub serial: bool,

    /// Number of parallel worker processes (default: cpu count)
    #[arg(long, value_name = "N", conflicts_with = "serial", value_parser = parse_workers)]
    pub workers: Option<usize>,

    /// Show the N slowest tests at end of run (0 = disabled)
    #[arg(long, value_name = "N")]
    pub durations: Option<usize>,

    /// Run only tests that failed on the last run
    #[arg(long = "lf", conflicts_with = "failed_first")]
    pub last_failed: bool,

    /// Run failed tests first, then the rest
    #[arg(long = "ff", conflicts_with = "last_failed")]
    pub failed_first: bool,

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
    pub workers: Option<usize>,
    pub cache_max_age: u32,
    pub min_parallel_tests: usize,
    pub timeout_multiplier: Option<f64>,
    pub spawn_overhead_ms: f64,
    pub strict: Option<StrictMode>,
    pub markers_without_description: Vec<String>,
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
        } else if cli.maxfail > 0 {
            self.maxfail = cli.maxfail;
        }
        self.serial = cli.serial;
        self.workers = cli.workers;
        if cli.strict.is_some() {
            self.strict = cli.strict.clone();
        }
        self
    }

    pub fn worker_count(&self) -> usize {
        if self.serial {
            return 1;
        }
        self.workers.unwrap_or_else(cpu_count)
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
    fn test_merge_cli_maxfail_sets_maxfail() {
        let dir = TempDir::new().unwrap();
        let config = Config::load(Utf8Path::from_path(dir.path()).unwrap());
        let cli = Cli {
            maxfail: 3,
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
    fn test_cli_tb_default_is_short() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert_eq!(cli.tb, TbStyle::Short);
    }

    #[test]
    fn test_cli_tb_no() {
        let cli = Cli::try_parse_from(["oxitest", "--tb", "no"]).unwrap();
        assert_eq!(cli.tb, TbStyle::No);
    }

    #[test]
    fn test_cli_tips_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--tips"]).unwrap();
        assert!(cli.tips);
    }

    #[test]
    fn test_cli_no_color_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--no-color"]).unwrap();
        assert!(cli.no_color);
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
        assert_eq!(cli.workers, Some(4));
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
    fn test_cli_lf_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--lf"]).unwrap();
        assert!(cli.last_failed);
    }

    #[test]
    fn test_cli_ff_flag() {
        let cli = Cli::try_parse_from(["oxitest", "--ff"]).unwrap();
        assert!(cli.failed_first);
    }

    #[test]
    fn test_cli_lf_default_false() {
        let cli = Cli::try_parse_from(["oxitest"]).unwrap();
        assert!(!cli.last_failed);
        assert!(!cli.failed_first);
    }

    #[test]
    fn test_cli_lf_and_ff_conflict() {
        let result = Cli::try_parse_from(["oxitest", "--lf", "--ff"]);
        assert!(result.is_err(), "--lf and --ff must conflict");
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
}
