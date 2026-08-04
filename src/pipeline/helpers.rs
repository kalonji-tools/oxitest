//! Shared utility functions used by pipeline phases.

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
    let (session, fixture_violations) =
        match crate::bridge::FixtureSession::new(py, conftest_files, &cfg.rootdir) {
            Ok(pair) => pair,
            Err(e) => {
                let err = crate::types::CollectError::PyError(format!(
                    "Failed to load conftest fixtures: {e}"
                ));
                return Err(early_exit_with_error(&[err], &make_reporter));
            }
        };

    if !cfg.features.plugins.is_empty() {
        if let Err(e) =
            session.load_plugins(py, &cfg.features.plugins, &cfg.features.plugin_settings)
        {
            let err = crate::types::CollectError::PyError(format!("Plugin loading failed: {e}"));
            return Err(early_exit_with_error(&[err], &make_reporter));
        }

        // Activate deferred plugins (those with CLI extensions that were
        // discovered in Phase 1 but not constructed until now).
        if let Err(e) = crate::bridge::activate_deferred_plugins(
            py,
            &session,
            &cfg.features.plugin_settings,
            &cfg.features.plugin_cli_values,
        ) {
            let err = crate::types::CollectError::PyError(format!(
                "Deferred plugin activation failed: {e}"
            ));
            return Err(early_exit_with_error(&[err], &make_reporter));
        }
    }

    if let Err(e) = session.init_async_backend(py, &cfg.features.async_backend) {
        let err = crate::types::CollectError::PyError(format!("Async backend init failed: {e}"));
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

/// Write the `--json` CTRF artifact for a `--strict=abort` run and return its
/// exit code.
///
/// This path renders its own console output via
/// [`print_strict_abort`](reporter::print_strict_abort), so it cannot reuse
/// `make_error_reporter` — that builds a console reporter too, and the
/// violations would print twice. Each violation becomes one `failed` entry
/// named after the test it belongs to; suite-level violations have no node ID
/// and fall back to `<strict>` (#1682).
fn write_strict_abort_report(
    shared: &super::PipelineShared,
    violations: &[strict::StrictViolation],
) -> ExitCode {
    let Some(path) = shared.json_path() else {
        return ExitCode::CollectError;
    };

    use reporter::Reporter as _;

    let mut json_reporter = reporter::json::JsonReporter::new(path);
    for violation in violations {
        let name = violation
            .node_id()
            .map_or_else(|| "<strict>".to_string(), ToString::to_string);
        json_reporter.record_run_failure(name, strict::format_violation_line(violation));
    }

    // A failed write votes `UsageError` (4), which outranks `CollectError` (3) —
    // matching the documented "`--json` output file cannot be written" rule.
    // A successful write abstains, which `code()` reads as `Success` (0).
    // Compared on `as_i32()`, never on `Ord`: `ExitCode` deliberately does not
    // derive it, so the enum's declaration order cannot reach this (#1863).
    std::cmp::max_by_key(
        json_reporter
            .finish(&[], false, &reporter::ReporterSession::new(0))
            .code(),
        ExitCode::CollectError,
        |code| code.as_i32(),
    )
}

/// Evaluate strict-mode violations and partition items accordingly.
pub(super) fn apply_strict_mode(
    shared: &super::PipelineShared,
    items: Vec<std::sync::Arc<types::TestItem>>,
    raw_violations: Vec<bridge::RawViolation>,
) -> Result<StrictModeResult, types::ExitCode> {
    let cfg = &shared.cfg;
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
        reporter::print_strict_abort(&abort_lines, shared.use_color);
        return Err(write_strict_abort_report(shared, &all_violations));
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

#[cfg(test)]
mod strict_abort_report_tests {
    use super::*;
    use crate::pipeline::{Pipeline, PipelinePhase};
    use crate::reporter::test_helpers::make_pipeline;

    /// A pipeline whose `--json` flag points at `json`, or omits `--json` for `None`.
    fn pipeline_with_json(json: Option<camino::Utf8PathBuf>) -> Pipeline {
        let mut pipeline = make_pipeline(PipelinePhase::Empty);
        let mut args = config::RunArgs::default_for_test();
        args.json = json;
        pipeline.shared.command = config::Command::Run(args);
        pipeline
    }

    #[test]
    fn test_writable_json_path_votes_collect_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("ctrf.json")).unwrap();
        let pipeline = pipeline_with_json(Some(path.clone()));

        let code = write_strict_abort_report(&pipeline.shared, &[]);

        assert_eq!(
            code,
            ExitCode::CollectError,
            "a strict-abort run that did manage to write its CTRF report must still exit on the strict violation itself, not on the report — letting the write's own Success win here would exit 0 and green a run that aborted"
        );
        assert!(
            path.exists(),
            "--json promises the file exists once the process is gone, whatever the exit code (#1682); a CollectError vote with no file on disk is indistinguishable from a job that never started"
        );
    }

    #[test]
    fn test_unwritable_json_path_votes_usage_error() {
        let dir = tempfile::tempdir().unwrap();
        let path =
            camino::Utf8PathBuf::from_path_buf(dir.path().join("no-such-dir").join("ctrf.json"))
                .unwrap();
        let pipeline = pipeline_with_json(Some(path));

        let code = write_strict_abort_report(&pipeline.shared, &[]);

        assert_eq!(
            code,
            ExitCode::UsageError,
            "an unwritable --json path is a usage error and must outrank the CollectError baseline; exiting 3 instead would tell CI the run merely failed to collect, sending the operator to hunt a broken test module rather than a bad --json argument"
        );
    }

    #[test]
    fn test_no_json_flag_votes_collect_error() {
        let pipeline = pipeline_with_json(None);

        let code = write_strict_abort_report(&pipeline.shared, &[]);

        assert_eq!(
            code,
            ExitCode::CollectError,
            "with no --json there is no report to fail at, so the strict violation alone decides the exit code — this early return is what keeps a plain strict-abort run from being reported as a usage error"
        );
    }
}
