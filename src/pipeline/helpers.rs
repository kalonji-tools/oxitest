//! Shared utility functions used by pipeline phases.

use crate::cache::{OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{bridge, cache, config, reporter, strict, types};
use pyo3::prelude::*;

/// Returns a human-readable OS description, e.g. "Ubuntu 24.04.2 LTS x86_64".
pub(super) fn os_info() -> String {
    let arch = std::env::consts::ARCH;

    #[cfg(target_os = "linux")]
    {
        if let Ok(content) = std::fs::read_to_string("/etc/os-release") {
            for line in content.lines() {
                if let Some(val) = line.strip_prefix("PRETTY_NAME=") {
                    let val = val.trim_matches('"');
                    return format!("{val} {arch}");
                }
            }
        }
        format!("Linux {arch}")
    }

    #[cfg(target_os = "macos")]
    {
        let ver = std::process::Command::new("sw_vers")
            .arg("-productVersion")
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|| "unknown".to_string());
        format!("macOS {ver} {arch}")
    }

    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    format!("{} {arch}", std::env::consts::OS)
}

/// Builds the environment snapshot string printed by `--capture-environment`.
pub(super) fn env_string(py: Python<'_>) -> String {
    let oxitest_ver = env!("CARGO_PKG_VERSION");
    let git_hash = env!("GIT_HASH");
    let pyver = py.version_info();
    let python_ver = format!("{}.{}.{}", pyver.major, pyver.minor, pyver.patch);
    let rustc_ver = env!("RUSTC_VERSION");
    let os = os_info();
    format!(
        "oxitest: {oxitest_ver} (git: {git_hash})\npython: {python_ver}\nrustc: {rustc_ver}\nos: {os}"
    )
}

pub(in crate::pipeline) fn early_exit_with_error(
    errors: &[types::CollectError],
    make_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> ExitCode {
    make_rep()
        .finish(errors, false, &reporter::ReporterSession::new(0))
        .code()
}

/// Initialize a FixtureSession: load conftest fixtures, plugins, and async backend.
pub(super) fn init_session(
    py: Python<'_>,
    conftest_files: &[camino::Utf8PathBuf],
    cfg: &crate::config::Config,
    make_reporter: impl Fn() -> Box<dyn reporter::Reporter>,
) -> Result<
    (
        crate::bridge::FixtureSession,
        Vec<crate::bridge::RawViolation>,
    ),
    ExitCode,
> {
    let (session, fixture_violations) = match crate::bridge::FixtureSession::new(py, conftest_files)
    {
        Ok(pair) => pair,
        Err(e) => {
            let err = crate::types::CollectError::PyError(format!(
                "Failed to load conftest fixtures: {}",
                e
            ));
            return Err(early_exit_with_error(&[err], &make_reporter));
        }
    };

    if !cfg.features.plugins.is_empty()
        && let Err(e) =
            session.load_plugins(py, &cfg.features.plugins, &cfg.features.plugin_settings)
    {
        let err = crate::types::CollectError::PyError(format!("Plugin loading failed: {}", e));
        return Err(early_exit_with_error(&[err], &make_reporter));
    }

    if let Err(e) = session.init_async_backend(py, &cfg.features.async_backend) {
        let err = crate::types::CollectError::PyError(format!("Async backend init failed: {}", e));
        return Err(early_exit_with_error(&[err], &make_reporter));
    }

    Ok((session, fixture_violations))
}

/// Merge timings into the cache, record outcomes, and persist to disk.
pub(super) fn finalize(
    cache: &mut cache::TestCache,
    timings: &[types::TestTiming],
    cache_max_age: u32,
    rootdir: &camino::Utf8Path,
) {
    cache.merge_timings(timings, cache_max_age);
    cache.record_timing_outcomes(timings);
    cache.save(rootdir);
}

/// Result of applying strict-mode violation processing.
pub(super) struct StrictModeResult {
    pub clean_items: Vec<std::sync::Arc<types::TestItem>>,
    pub violated_items: Vec<std::sync::Arc<types::TestItem>>,
    pub all_violations: Vec<strict::StrictViolation>,
    pub suite_lines: Vec<String>,
}

/// Evaluate strict-mode violations and partition items accordingly.
pub(super) fn apply_strict_mode(
    cfg: &config::Config,
    items: Vec<std::sync::Arc<types::TestItem>>,
    raw_violations: Vec<bridge::RawViolation>,
    use_color: bool,
) -> Result<StrictModeResult, types::ExitCode> {
    if cfg.markers.strict.is_none() {
        return Ok(StrictModeResult {
            clean_items: items,
            violated_items: vec![],
            all_violations: vec![],
            suite_lines: vec![],
        });
    }

    // Build the full violation list.
    let mut all_violations = strict::check_config(cfg);
    all_violations.extend(strict::check_collected(raw_violations));

    // Abort mode: print and signal early exit.
    if cfg.markers.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
        let abort_lines: Vec<String> = all_violations
            .iter()
            .map(strict::format_violation_line)
            .collect();
        reporter::print_strict_abort(&abort_lines, use_color);
        return Err(ExitCode::CollectError);
    }

    // Enforce mode: build suite-level violation lines.
    let suite_lines: Vec<String> = if cfg.markers.strict == Some(config::StrictMode::Enforce) {
        strict::suite_level(&all_violations)
            .iter()
            .map(|v| v.to_string())
            .collect()
    } else {
        vec![]
    };

    // Partition items into violated vs. clean.
    let (violated_items, clean_items): (Vec<_>, Vec<_>) =
        if cfg.markers.strict == Some(config::StrictMode::Enforce) {
            let violated_ids: std::collections::HashSet<&str> = all_violations
                .iter()
                .filter_map(|v| v.node_id())
                .map(|id| id.as_ref())
                .collect();
            items
                .into_iter()
                .partition(|i| violated_ids.contains(i.node_id.as_ref()))
        } else {
            (vec![], items)
        };

    Ok(StrictModeResult {
        clean_items,
        violated_items,
        all_violations,
        suite_lines,
    })
}
