//! Config merge and canonicalization logic.
//!
//! Merges fields from `[tool.oxitest]` in `pyproject.toml` and CLI flags into
//! a [`Config`]. CLI flags take precedence over `pyproject.toml` values.

use camino::{Utf8Path, Utf8PathBuf};

use super::pyproject::OxitestConfig;
use super::*;

/// Applies an `Option` value to a config field when `Some`.
///
/// Two variants:
///   `apply_if_some!(target, field, value)` — assigns the inner value directly.
///   `apply_if_some!(target, field, value, wrap)` — wraps the inner value in `Some`
///     (for fields where the target is `Option<T>`).
macro_rules! apply_if_some {
    ($target:expr, $field:ident, $value:expr) => {
        if let Some(v) = $value {
            $target.$field = v;
        }
    };
    ($target:expr, $field:ident, $value:expr, wrap) => {
        if let Some(v) = $value {
            $target.$field = Some(v);
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
    failed: Option<FailedMode>,
    tb: Option<TbStyle>,
    color: Option<ColorMode>,
    durations: Option<usize>,
    strict: Option<StrictMode>,
    keep_tmp: Option<KeepTmpMode>,
    show_locals: Option<bool>,
    show_internals: Option<bool>,
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

impl Config {
    /// Apply shared overrides from either TOML or CLI source.
    const fn apply_overrides(&mut self, ovr: Overrides) {
        // ── Execution ──────────────────────────────────────────────────
        apply_if_some!(self.filter, schedule, ovr.schedule);
        apply_if_some!(self.exec, retries, ovr.retries);
        apply_if_some!(self.exec, retries_delay_secs, ovr.retries_delay_secs);
        apply_if_some!(self.filter, failed, ovr.failed, wrap);
        apply_if_some!(self.output, keep_tmp, ovr.keep_tmp);

        // ── Output ─────────────────────────────────────────────────────
        apply_if_some!(self.output, tb, ovr.tb);
        apply_if_some!(self.output, show_locals, ovr.show_locals);
        apply_if_some!(self.output, show_internals, ovr.show_internals);
        apply_if_some!(self.output, color, ovr.color);
        apply_if_some!(self.output, durations, ovr.durations, wrap);

        // ── Filtering ──────────────────────────────────────────────────
        apply_if_some!(self.markers, strict, ovr.strict, wrap);
    }

    /// Merge fields from a parsed `[tool.oxitest]` section (fluent builder).
    ///
    /// `rootdir` controls how `testpaths` are resolved:
    ///   - `Some(root)` → each path is joined to root (used by `Config::load`)
    ///   - `None`       → each path is taken as-is (used by `Config::from_str`)
    pub(super) fn merge_toml(mut self, tc: OxitestConfig, rootdir: Option<&Utf8Path>) -> Self {
        // ── Paths ────────────────────────────────────────────────────────
        if let Some(paths) = tc.testpaths {
            let resolved: Vec<Utf8PathBuf> = match rootdir {
                Some(root) => resolve_testpaths(&paths, root),
                None => paths.into_iter().map(Utf8PathBuf::from).collect(),
            };
            // Both fields, from the same value and in the same arm: this is the
            // project's declaration, which is exactly what `declared_testpaths`
            // means. They diverge only in `merge_paths`, which is argv.
            self.paths.testpaths.clone_from(&resolved);
            self.paths.declared_testpaths = resolved;
        }
        apply_if_some!(self.paths, python_files, tc.python_files);
        apply_if_some!(self.paths, norecursedirs, tc.norecursedirs);
        apply_if_some!(self.paths, use_gitignore, tc.use_gitignore);

        // A present [tool.oxitest.doctest] table opts in — the doctest_enabled()
        // accessor returns true whenever this field is `Some`. To disable the
        // rule, drop the whole table.
        if let Some(dt) = tc.doctest {
            self.doctest = Some(dt);
        }

        // ── Execution (unique to TOML) ──────────────────────────────────
        apply_if_some!(self.exec, maxfail, tc.maxfail);
        if tc.serial == Some(true) {
            self.exec.mode = ExecutionMode::Serial;
        }
        apply_if_some!(self.features, async_backend, tc.async_backend);
        self.features.cache_max_age = tc.cache_max_age.unwrap_or(self.features.cache_max_age);
        self.exec.min_parallel_tests = tc
            .min_parallel_tests
            .unwrap_or(self.exec.min_parallel_tests);
        self.exec.spawn_overhead = tc
            .spawn_overhead_ms
            .map(crate::types::DurationMs::new)
            .unwrap_or(self.exec.spawn_overhead);
        self.exec.timeout_secs = tc.timeout;
        self.exec.timeout_multiplier = tc.timeout_multiplier;
        self.exec.inspect_timeout_secs =
            tc.inspect_timeout.unwrap_or(self.exec.inspect_timeout_secs);

        // ── Output (unique to TOML) ─────────────────────────────────────
        apply_if_some!(self.output, verbosity, tc.verbosity);

        // ── Filtering (unique to TOML) ──────────────────────────────────
        apply_if_some!(self.features, plugins, tc.plugins);
        self.features.plugin_settings = tc.plugin_settings;
        apply_if_some!(self.filter, affected_base, tc.affected_base);

        if let Some(raw_markers) = tc.markers {
            let (names, no_desc) = parse_marker_descriptions(&raw_markers);
            self.markers.registered_markers = names;
            self.markers.markers_without_description = no_desc;
        }

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: tc.schedule,
            retries: tc.retries,
            retries_delay_secs: tc.retries_delay,
            failed: tc.failed,
            tb: tc.tb,
            color: tc.color,
            durations: tc.durations,
            strict: tc.strict,
            keep_tmp: tc.keep_tmp,
            show_locals: tc.show_locals,
            show_internals: tc.show_internals,
        });

        // Apply workers after overrides (workers is no longer in Overrides).
        if let Some(w) = tc.workers {
            self.exec.mode = ExecutionMode::Parallel { workers: w };
        }

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
            self.exec.maxfail = 1;
        }
        if let Some(n) = args.maxfail
            && n > 0
        {
            self.exec.maxfail = n;
        }
        if args.serial {
            self.exec.mode = ExecutionMode::Serial;
        }
        apply_if_some!(self.exec, timeout_secs, args.timeout, wrap);
        if args.doctest_modules {
            // Doctest collection opt-in from --doctest-modules.
            let dt = self.doctest.get_or_insert_with(DoctestConfig::default);
            dt.scope = Some(DoctestScope::Public);
        }

        // ── Filtering (unique to CLI) ───────────────────────────────────
        self.merge_affected(&args.filter.affected);

        // ── Coverage ──────────────────────────────────────────────────
        self.features.cov = args.cov;
        if args.cov_report.is_some() {
            self.features.cov_report = args.cov_report.clone();
        }

        // ── Output (unique to CLI) ──────────────────────────────────
        if let Some(level) = args.verbosity.resolve() {
            self.output.verbosity = level;
        }
        self.output.collection_profile = args.collection_profile;

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: args.schedule,
            retries: args.retries,
            retries_delay_secs: None,
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
        });

        // --strict=off disables strict, overriding any config value.
        if self.markers.strict == Some(StrictMode::Off) {
            self.markers.strict = None;
        }

        // Apply workers after overrides (workers is no longer in Overrides).
        if let Some(w) = args.workers {
            self.exec.mode = ExecutionMode::Parallel { workers: w };
        }

        self
    }

    pub fn merge_debug_args(mut self, args: &DebugArgs) -> Self {
        // ── Paths ────────────────────────────────────────────────────────
        self.merge_paths(&args.paths);

        if !args.node_ids.is_empty() {
            self.canonicalize_node_ids(&args.node_ids);
        }

        // ── Debug mode ──────────────────────────────────────────────────
        let mode = args.mode();
        self.exec.mode = ExecutionMode::Debug(mode.clone());
        self.exec.timeout_secs = None;
        self.output.show_internals = true;
        if args.tb.is_none() {
            self.output.tb = TbStyle::Detail;
        }
        if matches!(mode, DebugMode::PostMortem) {
            self.exec.maxfail = 1;
        }

        // ── Filtering (unique to CLI) ───────────────────────────────────
        self.merge_affected(&args.filter.affected);

        // ── Output (unique to CLI) ──────────────────────────────────
        if let Some(level) = args.verbosity.resolve() {
            self.output.verbosity = level;
        }

        // ── Shared overrides ────────────────────────────────────────────
        self.apply_overrides(Overrides {
            schedule: None,
            retries: None,
            retries_delay_secs: None,
            failed: args.failed_filter.resolve(),
            tb: args.tb.clone(),
            color: args.color,
            durations: None,
            strict: None,
            keep_tmp: args.keep_tmp,
            show_locals: if args.show_locals { Some(true) } else { None },
            show_internals: None,
        });

        self
    }

    pub fn merge_query_args(mut self, args: &cli::QueryArgs) -> Self {
        self.merge_paths(&args.paths);
        if let Some(c) = args.color {
            self.output.color = c;
        }
        self
    }

    /// Merge CLI paths into testpaths, resolving relative paths against the
    /// directory `oxitest` was invoked from.
    ///
    /// **Not** against the rootdir (#2026). The rootdir is derived from these
    /// same paths, so resolving them against it is circular: a run from below
    /// the rootdir looked for `<rootdir>/<arg>`, found nothing, and exited 0.
    /// [`crate::config::resolve_target`] owns the rule.
    ///
    /// The Targets are resolved here rather than at the command-line boundary
    /// because [`crate::target::render_missing_paths`] echoes the argument back
    /// verbatim; absolutising earlier would print a path the user never typed.
    ///
    /// Writes [`PathConfig::testpaths`] and **not**
    /// [`PathConfig::declared_testpaths`] — see that field for why. This is the
    /// site the invariant is broken from (#1798).
    fn merge_paths(&mut self, paths: &[Utf8PathBuf]) {
        if !paths.is_empty() {
            self.filter.has_explicit_paths = true;
            self.paths.testpaths = paths
                .iter()
                .map(|p| crate::config::resolve_target(&self.invocation_dir, p))
                .collect();
        }
    }

    /// Canonicalize node IDs so their file paths match the canonical form used by collected items.
    ///
    /// Resolves relative paths in node IDs against the directory `oxitest` was
    /// invoked from, then canonicalizes via `std::fs::canonicalize`. Source
    /// files are computed on-demand via [`FilterConfig::source_files()`].
    ///
    /// The base is the invocation directory and not the rootdir, for the reason
    /// [`Config::merge_paths`] gives. A node ID carried the same defect in a
    /// louder form: `./test_x.py::test_x` run from below the rootdir was
    /// refused with `no such test` for a test that exists (#2026).
    fn canonicalize_node_ids(&mut self, raw_ids: &[crate::types::NodeId]) {
        use crate::types::NodeId;

        self.filter.node_ids = raw_ids
            .iter()
            .map(|id| {
                let id_str: &str = id.as_ref();
                let Some((file_part, rest)) = crate::types::node_id::split_node_id_once(id_str)
                else {
                    return id.clone();
                };

                let abs_path =
                    crate::config::resolve_target(&self.invocation_dir, Utf8Path::new(file_part));

                // Glob node IDs: absolutised like any other, but `fs::canonicalize`
                // is skipped because a pattern is not a real path. A glob is also
                // exempt from the existence check, so leaving it on the rootdir
                // made the same defect silent instead of loud (#2026).
                if crate::filter::contains_glob_chars(file_part) {
                    return NodeId::from_raw(&format!("{abs_path}::{rest}"));
                }

                match std::fs::canonicalize(abs_path.as_std_path()) {
                    Ok(canonical) => match Utf8PathBuf::from_path_buf(canonical) {
                        Ok(utf8) => NodeId::from_raw(&format!("{utf8}::{rest}")),
                        Err(_) => id.clone(),
                    },
                    Err(_) => id.clone(),
                }
            })
            .collect();
    }

    /// Merge the `--affected` flag into config, resolving empty sentinel to `affected_base`.
    fn merge_affected(&mut self, affected: &Option<String>) {
        if let Some(val) = affected {
            self.filter.has_explicit_paths = true;
            if val.is_empty() {
                self.filter.affected = Some(self.filter.affected_base.clone());
            } else {
                self.filter.affected = affected.clone();
            }
        }
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
    fn test_load_with_no_pyproject_returns_defaults() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(
            config.paths.python_files,
            Config::default().paths.python_files
        );
    }

    #[test]
    fn load_preserves_rootdir_as_given() {
        // load stores what it receives unchanged; the caller is responsible for
        // providing a canonical path.
        let dir = assert_fs::TempDir::new().unwrap();
        dir.child("pyproject.toml").write_str("").unwrap();
        let utf8_dir = camino::Utf8Path::from_path(dir.path()).unwrap();
        let cfg = Config::load_at(utf8_dir);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.paths.testpaths, vec![utf8_dir.to_owned()]);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.paths.testpaths, vec![utf8_dir.join("tests")]);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.paths.python_files, vec!["check_*.py".to_string()]);
    }

    #[test]
    fn test_merge_run_args_exitfirst_sets_maxfail_1() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let mut args = base_run_args();
        args.exitfirst = true;
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.exec.maxfail, 1);
    }

    #[test]
    fn test_merge_run_args_maxfail_sets_maxfail() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let mut args = base_run_args();
        args.maxfail = Some(3);
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.exec.maxfail, 3);
    }

    #[test]
    fn test_merge_run_args_paths_overrides_testpaths() {
        let dir = TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let config = Config::load_at(utf8_dir);
        let custom = utf8_dir.join("custom_tests");
        let mut args = base_run_args();
        args.paths = vec![custom.clone()];
        let merged = config.merge_run_args(&args);
        assert_eq!(merged.paths.testpaths, vec![custom]);
    }

    #[test]
    fn test_tb_from_pyproject() {
        let toml = "[tool.oxitest]\ntb = \"detail\"\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.output.tb, TbStyle::Detail);
    }

    #[test]
    fn test_verbosity_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nverbosity = \"detailed\"\n").unwrap();
        assert_eq!(cfg.output.verbosity, Verbosity::Detailed);
    }

    #[test]
    fn test_verbosity_full_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nverbosity = \"full\"\n").unwrap();
        assert_eq!(cfg.output.verbosity, Verbosity::Full);
    }

    #[test]
    fn test_maxfail_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nmaxfail = 5\n").unwrap();
        assert_eq!(cfg.exec.maxfail, 5);
    }

    #[test]
    fn test_durations_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\ndurations = 10\n").unwrap();
        assert_eq!(cfg.output.durations, Some(10));
    }

    #[test]
    fn test_serial_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nserial = true\n").unwrap();
        assert!(cfg.exec.mode.is_serial());
    }

    #[test]
    fn test_color_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\ncolor = \"never\"\n").unwrap();
        assert_eq!(cfg.output.color, ColorMode::Never);
    }

    #[test]
    fn test_color_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ncolor = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(cfg.output.color, ColorMode::Always);
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.output.color, ColorMode::Always);
    }

    #[test]
    fn test_color_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ncolor = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--color", "never"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.output.color, ColorMode::Never);
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
        let config = Config::load_at(utf8_dir);
        assert!(
            config
                .markers
                .registered_markers
                .contains(&"slow".to_string())
        );
        assert!(
            config
                .markers
                .registered_markers
                .contains(&"integration".to_string())
        );
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.markers.registered_markers, vec!["slow".to_string()]);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.exec.timeout_secs, Some(30));
    }

    #[test]
    fn test_timeout_absent_is_none() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("pyproject.toml"), "[tool.oxitest]\n").unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(config.exec.timeout_secs, None);
    }

    #[test]
    fn test_config_worker_count_serial_returns_1() {
        let dir = TempDir::new().unwrap();
        let args = parse_run(&["--serial"]);
        let config =
            Config::load_at(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert_eq!(config.worker_count(), 1);
    }

    #[test]
    fn test_config_worker_count_explicit_workers() {
        let dir = TempDir::new().unwrap();
        let args = parse_run(&["--workers", "3"]);
        let config =
            Config::load_at(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert_eq!(config.worker_count(), 3);
    }

    #[test]
    fn test_config_worker_count_default_is_cpu_count() {
        let dir = TempDir::new().unwrap();
        let args = base_run_args();
        let config =
            Config::load_at(Utf8Path::from_path(dir.path()).unwrap()).merge_run_args(&args);
        assert!(config.worker_count() >= 1);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.features.cache_max_age, 20);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.exec.min_parallel_tests, 50);
    }

    #[test]
    fn test_timeout_multiplier_default_is_none() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert!(config.exec.timeout_multiplier.is_none());
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.exec.timeout_multiplier, Some(3.0));
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.filter.failed, Some(FailedMode::First));
    }

    #[test]
    fn config_spawn_overhead_ms_loads_from_toml() {
        let toml = r#"
[tool.oxitest]
spawn_overhead_ms = 100.0
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(
            cfg.exec.spawn_overhead,
            crate::types::DurationMs::new(100.0)
        );
    }

    #[test]
    fn test_strict_loads_from_toml() {
        let toml = "[tool.oxitest]\nstrict = \"enforce\"\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.markers.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_strict_absent_in_toml_is_none() {
        let toml = "[tool.oxitest]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.markers.strict.is_none());
    }

    #[test]
    fn test_cli_strict_overrides_toml() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nstrict = \"abort\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--strict=enforce"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.markers.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_markers_without_description_populated() {
        let toml = "[tool.oxitest]\nmarkers = [\"slow: marks slow tests\", \"db\"]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(
            cfg.markers.markers_without_description,
            vec!["db".to_string()]
        );
    }

    #[test]
    fn test_markers_all_with_description_leaves_list_empty() {
        let toml = "[tool.oxitest]\nmarkers = [\"slow: marks slow\", \"db: hits db\"]\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.markers.markers_without_description.is_empty());
    }

    #[test]
    fn test_cli_absent_strict_does_not_clear_toml_strict() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nstrict = \"enforce\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.markers.strict, Some(StrictMode::Enforce));
    }

    #[test]
    fn test_strict_off_overrides_toml_strict() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nstrict = \"abort\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let mut args = base_run_args();
        args.strict = Some(StrictMode::Off);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(
            merged.markers.strict, None,
            "--strict=off should clear strict regardless of pyproject.toml"
        );
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
        assert_eq!(
            config.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Auto
            }
        );
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
        assert_eq!(
            config.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Fixed(4)
            }
        );
    }

    #[test]
    fn test_toml_workers_absent_is_default() {
        let config = Config::from_str(
            r#"
        [tool.oxitest]
        testpaths = ["tests"]
        "#,
        )
        .unwrap();
        assert_eq!(
            config.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Auto
            }
        );
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
        assert_eq!(
            config.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Auto
            }
        );

        let args = parse_run(&["--workers", "2"]);
        let merged = config.merge_run_args(&args);
        assert_eq!(
            merged.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Fixed(2)
            }
        );
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
        assert_eq!(
            merged.exec.mode,
            ExecutionMode::Parallel {
                workers: WorkerCount::Fixed(8)
            }
        );
    }

    #[test]
    fn test_config_worker_count_auto_is_cpu_count() {
        let mut config = Config::default();
        config.exec.mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        assert!(config.worker_count() >= 1);
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
        let config = Config::load_at(utf8_dir);
        assert_eq!(config.filter.schedule, ScheduleStrategy::FailedFirst);
    }

    #[test]
    fn test_schedule_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nschedule = \"random\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.filter.schedule, ScheduleStrategy::Random);
    }

    #[test]
    fn test_schedule_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nschedule = \"random\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--schedule", "failed-first"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.filter.schedule, ScheduleStrategy::FailedFirst);
    }

    #[test]
    fn test_timeout_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 60\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert_eq!(cfg.exec.timeout_secs, Some(60));
        let args = parse_run(&["--timeout", "10"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.exec.timeout_secs, Some(10));
    }

    #[test]
    fn test_timeout_pyproject_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 45\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.exec.timeout_secs, Some(45));
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
    fn test_plugins_from_pyproject() {
        let toml = r#"
[tool.oxitest]
plugins = ["oxitest_loguru", "oxitest_db"]
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.features.plugins, vec!["oxitest_loguru", "oxitest_db"]);
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
        assert!(cfg.features.plugin_settings.contains_key("myplugin"));
        let settings = &cfg.features.plugin_settings["myplugin"];
        assert_eq!(
            settings.get("level").and_then(|v| v.as_str()),
            Some("DEBUG")
        );
    }

    #[test]
    fn test_async_backend_from_pyproject() {
        let toml = r#"
[tool.oxitest]
async_backend = "trio"
"#;
        let cfg = Config::from_str(toml).unwrap();
        assert_eq!(cfg.features.async_backend, "trio");
    }

    #[test]
    fn test_affected_bare_resolves_to_config_base() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\naffected_base = \"main\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.filter.affected, Some("main".to_string()));
    }

    #[test]
    fn test_affected_bare_defaults_to_head_without_config() {
        let dir = TempDir::new().unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.filter.affected, Some("HEAD".to_string()));
    }

    #[test]
    fn test_affected_explicit_overrides_config_base() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\naffected_base = \"main\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--affected=develop"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.filter.affected, Some("develop".to_string()));
    }

    #[test]
    fn test_retries_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nretries = 2\n").unwrap();
        assert_eq!(cfg.exec.retries, 2);
    }

    #[test]
    fn test_retries_delay_from_pyproject() {
        let cfg = Config::from_str("[tool.oxitest]\nretries_delay = 5\n").unwrap();
        assert_eq!(cfg.exec.retries_delay_secs, 5);
    }

    #[test]
    fn test_retries_cli_overrides_pyproject() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nretries = 2\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--retries", "5"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.exec.retries, 5);
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

    // ── Debug mode merge tests ─────────────────────────────────────────

    #[test]
    fn test_debug_post_mortem_merge() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = DebugArgs::default_for_test(); // defaults to PostMortem
        let merged = config.merge_debug_args(&args);
        assert!(merged.exec.mode.is_serial(), "debug should imply serial");
        assert_eq!(merged.exec.maxfail, 1, "post-mortem should imply maxfail=1");
        assert_eq!(
            merged.output.tb,
            TbStyle::Detail,
            "debug should imply tb=detail"
        );
        assert!(
            merged.exec.mode.debug_mode().is_some(),
            "debug should be stored on config"
        );
        assert_eq!(merged.exec.timeout_secs, None, "debug should clear timeout");
        assert!(
            merged.output.show_internals,
            "debug should imply show_internals"
        );
    }

    #[test]
    fn test_debug_post_mortem_clears_pyproject_timeout() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\ntimeout = 30\n",
        )
        .unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = DebugArgs::default_for_test();
        let merged = config.merge_debug_args(&args);
        assert_eq!(
            merged.exec.timeout_secs, None,
            "debug should clear pyproject timeout"
        );
    }

    #[test]
    fn test_merge_debug_args_applies_mode() {
        let dir = TempDir::new().unwrap();
        let config = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = DebugArgs::default_for_test();
        let merged = config.merge_debug_args(&args);
        assert!(merged.exec.mode.is_serial());
        assert_eq!(merged.exec.maxfail, 1);
        assert!(merged.exec.mode.debug_mode().is_some());
    }

    // ── keep_tmp tests ──────────────────────────────────────────────────

    #[test]
    fn test_keep_tmp_from_pyproject_failed() {
        let cfg = Config::from_str("[tool.oxitest]\nkeep_tmp = \"failed\"\n").unwrap();
        assert_eq!(cfg.output.keep_tmp, KeepTmpMode::Failed);
    }

    #[test]
    fn test_keep_tmp_from_pyproject_always() {
        let cfg = Config::from_str("[tool.oxitest]\nkeep_tmp = \"always\"\n").unwrap();
        assert_eq!(cfg.output.keep_tmp, KeepTmpMode::Always);
    }

    #[test]
    fn test_keep_tmp_cli_overrides_toml() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nkeep_tmp = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = parse_run(&["--keep-tmp=failed"]);
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.output.keep_tmp, KeepTmpMode::Failed);
    }

    #[test]
    fn test_keep_tmp_toml_preserved_when_cli_absent() {
        let dir = TempDir::new().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[tool.oxitest]\nkeep_tmp = \"always\"\n",
        )
        .unwrap();
        let cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        let args = base_run_args();
        let merged = cfg.merge_run_args(&args);
        assert_eq!(merged.output.keep_tmp, KeepTmpMode::Always);
    }

    #[test]
    fn test_show_locals_from_pyproject() {
        let toml = "[tool.oxitest]\nshow_locals = true\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.output.show_locals);
    }

    #[test]
    fn test_show_internals_from_pyproject() {
        let toml = "[tool.oxitest]\nshow_internals = true\n";
        let cfg = Config::from_str(toml).unwrap();
        assert!(cfg.output.show_internals);
    }

    // ── use_gitignore tests ─────────────────────────────────────────────

    #[test]
    fn test_use_gitignore_from_pyproject_false() {
        let cfg = Config::from_str("[tool.oxitest]\nuse_gitignore = false\n").unwrap();
        assert!(!cfg.paths.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_from_pyproject_true() {
        let cfg = Config::from_str("[tool.oxitest]\nuse_gitignore = true\n").unwrap();
        assert!(cfg.paths.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_absent_defaults_true() {
        let cfg = Config::from_str("[tool.oxitest]\n").unwrap();
        assert!(cfg.paths.use_gitignore);
    }

    #[test]
    fn test_use_gitignore_cli_disables() {
        let dir = TempDir::new().unwrap();
        let mut cfg = Config::load_at(Utf8Path::from_path(dir.path()).unwrap());
        assert!(cfg.paths.use_gitignore);
        cfg.paths.use_gitignore = false; // simulates what pipeline does with CLI flag
        assert!(!cfg.paths.use_gitignore);
    }
}
