//! Shared utility functions used by pipeline phases.

use crate::cache::{OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{cache, reporter, types};
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
        .finish(errors, false, &reporter::RunStats::new())
        .code()
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
