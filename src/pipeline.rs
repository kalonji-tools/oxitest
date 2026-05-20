//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.

use crate::{
    bridge, cache, collector, config, filter, marker, parallel, reporter, scheduler, strict, types,
};
use clap::Parser;
use pyo3::prelude::*;
use std::io::IsTerminal;

fn file_mtime_secs(path: &camino::Utf8Path) -> u64 {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .map(|t| {
            t.duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
        })
        .unwrap_or(0)
}

fn collect_items(
    py: Python<'_>,
    test_files: &[camino::Utf8PathBuf],
    cfg: &config::Config,
    session: &bridge::FixtureSession,
    cache: &mut cache::TestCache,
) -> (
    Vec<types::TestItem>,
    Vec<types::CollectError>,
    Vec<bridge::RawViolation>,
) {
    let mut items = Vec::new();
    let mut errors = Vec::new();
    let mut raw_violations: Vec<bridge::RawViolation> = Vec::new();
    let collect_violations = cfg.strict.is_some();

    for file in test_files {
        let mtime = file_mtime_secs(file);
        // Skip cache when collecting violations — violations are not cached.
        let cached = if collect_violations {
            None
        } else {
            cache.cached_module_items(file, mtime)
        };
        if let Some(cached_items) = cached {
            items.extend(cached_items);
            continue;
        }
        match bridge::collect_module(py, file, Some(session), collect_violations) {
            Ok((file_items, file_violations)) => {
                // Skip cache write in strict mode: violations are not cached,
                // so the cached entry would silently drop violation data on the next run.
                if mtime != 0 && !collect_violations {
                    cache.update_module_cache(file, mtime, &file_items);
                }
                raw_violations.extend(file_violations);
                items.extend(file_items);
            }
            Err(e) => errors.push(e),
        }
    }

    if errors.is_empty() {
        let registered: std::collections::HashSet<&str> =
            cfg.registered_markers.iter().map(String::as_str).collect();
        let marker_errors = filter::validate_markers(&items, &registered);
        errors.extend(marker_errors);
    }

    (items, errors, raw_violations)
}

fn resolve_timeout(
    cache: &cache::TestCache,
    item: &types::TestItem,
    global: Option<u64>,
    multiplier: Option<f64>,
) -> Option<u64> {
    match multiplier {
        None => global,
        Some(mult) => cache
            .suggested_timeout_secs(item, mult)
            .map(|t| t.max(global.unwrap_or(1)))
            .or(global),
    }
}

/// Returns a human-readable OS description, e.g. "Ubuntu 24.04.2 LTS x86_64".
fn os_info() -> String {
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
fn env_string(py: Python<'_>) -> String {
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

fn run_phase(
    py: Python<'_>,
    groups: Vec<(camino::Utf8PathBuf, Vec<types::TestItem>)>,
    cfg: &config::Config,
    cache: &cache::TestCache,
    session: &bridge::FixtureSession,
    rep: &mut dyn reporter::Reporter,
) -> (bool, Vec<types::TestTiming>) {
    let mut failures = 0usize;
    let mut interrupted = false;
    let mut timings: Vec<types::TestTiming> = Vec::new();

    'run: for (module_path, items) in &groups {
        for item in items {
            rep.test_started(item);
            let start = std::time::Instant::now();
            let timeout = resolve_timeout(cache, item, cfg.timeout_secs, cfg.timeout_multiplier);
            let outcome = bridge::run_test(py, item, Some(session), timeout);
            let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
            timings.push(types::TestTiming {
                node_id: item.node_id.clone(),
                duration_ms,
                outcome: outcome.as_str().to_string(),
            });
            rep.test_completed(item, &outcome, duration_ms);
            if outcome.is_hard_failure() {
                failures += 1;
            }
            if cfg.maxfail > 0 && failures >= cfg.maxfail {
                interrupted = true;
                if let Err(e) = session.end_module(py, module_path) {
                    tracing::warn!(%e, module = %module_path, "teardown error in end_module");
                    rep.record_teardown_warning(
                        &format!("end_module({})", module_path),
                        &e.to_string(),
                    );
                }
                break 'run;
            }
        }
        if let Err(e) = session.end_module(py, module_path) {
            tracing::warn!(%e, module = %module_path, "teardown error in end_module");
            rep.record_teardown_warning(&format!("end_module({})", module_path), &e.to_string());
        }
    }
    if let Err(e) = session.end_session(py) {
        tracing::warn!(%e, "teardown error in end_session");
        rep.record_teardown_warning("end_session", &e.to_string());
    }

    (interrupted, timings)
}

struct SetupContext {
    cfg: config::Config,
    cache: cache::TestCache,
    cli: config::Cli,
    rootdir: camino::Utf8PathBuf,
    is_tty: bool,
    use_color: bool,
    base: reporter::ReporterOptsBuilder,
}

enum SetupResult {
    EarlyExit(i32),
    Ready(Box<SetupContext>),
}

fn setup(py: Python<'_>, args: &[String]) -> PyResult<SetupResult> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string())
        .chain(args.iter().cloned())
        .collect();

    let cli = match config::Cli::try_parse_from(&argv) {
        Ok(c) => c,
        Err(e) => {
            // Clap formats this for the user; subscriber may not be initialised yet.
            eprintln!("{}", e);
            return Ok(SetupResult::EarlyExit(4));
        }
    };

    // Early-exit flags: handled before any filesystem setup.
    if cli.capture_environment {
        println!("{}", env_string(py));
        return Ok(SetupResult::EarlyExit(0));
    }

    let rootdir = config::find_rootdir(cli.paths.first().map(|p| p.as_path()));
    let cfg = config::Config::load(&rootdir).merge_cli(&cli);
    let cache = cache::TestCache::load(&rootdir);

    let is_tty = std::io::stdout().is_terminal();
    let use_color = match cfg.color {
        config::ColorMode::Always => true,
        config::ColorMode::Never => false,
        config::ColorMode::Auto => is_tty && console::colors_enabled(),
    };
    let resolved_tb = cli.tb.clone().unwrap_or(cfg.tb.clone());
    let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color)
        .tb(resolved_tb)
        .show_tips(cli.tips)
        .show_warnings(cli.warnings);

    Ok(SetupResult::Ready(Box::new(SetupContext {
        cfg,
        cache,
        cli,
        rootdir,
        is_tty,
        use_color,
        base,
    })))
}

struct StrictResult {
    clean_items: Vec<types::TestItem>,
    violated_items: Vec<types::TestItem>,
    all_violations: Vec<strict::StrictViolation>,
    suite_lines: Vec<String>,
}

/// Build violations, handle abort mode, produce enforce-mode suite lines, and
/// partition items into violated vs. clean.  Returns `Err(3)` when abort mode
/// detects violations (caller should propagate as `Ok(3)`).
fn apply_strict(
    cfg: &config::Config,
    items: Vec<types::TestItem>,
    raw_violations: Vec<bridge::RawViolation>,
    use_color: bool,
) -> Result<StrictResult, i32> {
    // Build the full violation list.
    let all_violations: Vec<strict::StrictViolation> = if cfg.strict.is_some() {
        let mut v = strict::check_config(cfg);
        v.extend(strict::check_collected(raw_violations));
        v
    } else {
        vec![]
    };

    // Abort mode: print and signal early exit.
    if cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
        reporter::print_strict_abort(&all_violations, use_color);
        return Err(3);
    }

    // Enforce mode: build suite-level violation lines and partition items.
    let suite_lines: Vec<String> = if cfg.strict == Some(config::StrictMode::Enforce) {
        strict::suite_level(&all_violations)
            .iter()
            .map(|v| strict::format_violation_line(v))
            .collect()
    } else {
        vec![]
    };

    let (violated_items, clean_items): (Vec<_>, Vec<_>) =
        if cfg.strict == Some(config::StrictMode::Enforce) {
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

    Ok(StrictResult {
        clean_items,
        violated_items,
        all_violations,
        suite_lines,
    })
}

/// Apply keyword, marker, and last-failed filters to the collected items.
///
/// Returns the filtered item list, or `Err(code)` for an invalid `-m` expression
/// (code 2, surfaced via the error reporter supplied by `make_error_rep`).
fn apply_filters(
    items: Vec<types::TestItem>,
    cli: &config::Cli,
    cfg: &config::Config,
    cache: &cache::TestCache,
    make_error_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> Result<Vec<types::TestItem>, i32> {
    // Keyword filter (-k).
    let items = filter::filter_items(items, cli.keyword.as_deref());

    // Marker expression filter (-m).
    let items = if let Some(expr) = &cli.marker {
        match marker::filter_by_marker_expr(items, expr) {
            Ok(items) => items,
            Err(e) => {
                let code = make_error_rep().finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -m expression: {}",
                        e
                    ))],
                    false,
                );
                return Err(code);
            }
        }
    } else {
        items
    };

    // Last-failed filter (--failed=only / --failed=first).
    let total_before_failed_filter = items.len();
    let items = match cfg.failed {
        Some(config::FailedMode::Only) => {
            let failed_ids = cache.last_failed_ids();
            if failed_ids.is_empty() {
                eprintln!("no recorded failures — running all {} tests", items.len());
                items
            } else {
                let filtered = filter::filter_last_failed(items, &failed_ids);
                eprintln!(
                    "running {}/{} tests (--failed=only mode)",
                    filtered.len(),
                    total_before_failed_filter
                );
                filtered
            }
        }
        Some(config::FailedMode::First) => {
            let failed_ids = cache.last_failed_ids();
            filter::sort_failed_first(items, &failed_ids)
        }
        None => items,
    };

    Ok(items)
}

/// Report violated items, group and schedule the clean items, decide
/// serial vs. parallel, dispatch, and return `(interrupted, timings)`.
///
/// `cache.invalidate()` and `cache.estimated_duration()` are called here
/// because they feed directly into the parallel-dispatch decision.
#[allow(clippy::too_many_arguments)]
fn execute(
    py: Python<'_>,
    clean_items: Vec<types::TestItem>,
    violated_items: Vec<types::TestItem>,
    all_violations: Vec<strict::StrictViolation>,
    cfg: &config::Config,
    cache: &cache::TestCache,
    session: &bridge::FixtureSession,
    conftest_files: &[camino::Utf8PathBuf],
    rep: &mut dyn reporter::Reporter,
) -> (bool, Vec<types::TestTiming>) {
    // Immediately report violated items as Error outcomes (no worker dispatch).
    for item in &violated_items {
        // Per-test items may have multiple violations; we report only the first to keep
        // the error message focused. Users address violations one at a time.
        if let Some(v) = all_violations
            .iter()
            .find(|v| v.node_id().is_some_and(|id| id == &item.node_id))
        {
            let outcome = strict::per_test_error(v);
            rep.test_started(item);
            rep.test_completed(item, &outcome, 0.0);
        }
    }

    let estimated = cache.estimated_duration(&clean_items);

    let mut groups = filter::group_by_module(clean_items);
    let failed_ids = cache.last_failed_ids();
    scheduler::apply_schedule_strategy(&mut groups, cfg.schedule, cache, &failed_ids);

    let total_tests: usize = groups.iter().map(|(_, items)| items.len()).sum();
    let cpu_count = config::cpu_count();

    let force_parallel = cfg.workers.is_some() && !cfg.serial;
    let use_parallel = !cfg.serial
        && cfg.worker_count() > 1
        && (force_parallel
            || match estimated {
                Some(est) => {
                    est.as_millis() as f64 > cfg.spawn_overhead_ms * cfg.worker_count() as f64
                }
                None => total_tests >= cfg.min_parallel_tests, // cold cache: fall back to configured threshold
            });

    if use_parallel {
        debug_assert!(
            !cfg.serial,
            "compute_optimal_workers is unreachable in serial mode"
        );
        let optimal_worker_count = parallel::compute_optimal_workers(
            cfg.workers,
            cfg.serial,
            cpu_count,
            estimated,
            cfg.spawn_overhead_ms,
        );
        // Warn when session-scoped (shared=True) fixtures are present: each worker
        // subprocess creates its own FixtureSession, so these fixtures execute once
        // per worker rather than once per run.
        let shared_names = session.shared_fixture_names(py);
        if !shared_names.is_empty() {
            let list = shared_names.join(", ");
            let noun = if shared_names.len() == 1 {
                "fixture"
            } else {
                "fixtures"
            };
            tracing::warn!(
                fixtures = %list,
                fixture_count = shared_names.len(),
                workers = optimal_worker_count,
                "shared {noun} will run once per worker; \
                 session-scoped fixtures are not shared across parallel worker processes — \
                 use --serial to run them once, or remove shared=True from fixtures \
                 that can be function-scoped"
            );
        }
        parallel::run_phase_parallel(groups, cfg, optimal_worker_count, conftest_files, rep)
    } else {
        run_phase(py, groups, cfg, cache, session, rep)
    }
}

/// Merge timings into the cache, record outcomes, and persist to disk.
fn finalize(
    cache: &mut cache::TestCache,
    timings: Vec<types::TestTiming>,
    cache_max_age: u32,
    rootdir: &camino::Utf8Path,
) {
    // Single pass: move node_id into outcome_pairs, clone once into timing_pairs.
    let mut timing_pairs: Vec<(types::NodeId, f64)> = Vec::with_capacity(timings.len());
    let mut outcome_pairs: Vec<(types::NodeId, String)> = Vec::with_capacity(timings.len());
    for t in timings {
        outcome_pairs.push((t.node_id.clone(), t.outcome));
        timing_pairs.push((t.node_id, t.duration_ms));
    }

    cache.merge(&timing_pairs, cache_max_age);
    cache.record_outcomes(&outcome_pairs);
    cache.save(rootdir);
}

pub(crate) fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let SetupContext {
        cfg,
        mut cache,
        cli,
        rootdir,
        is_tty,
        use_color,
        base,
    } = match setup(py, &args)? {
        SetupResult::EarlyExit(code) => return Ok(code),
        SetupResult::Ready(ctx) => *ctx,
    };

    let make_error_rep =
        || reporter::make_reporter(base.clone().verbose(false).build(), is_tty, None, vec![]);

    let (test_files, conftest_files) = collector::collect_files(&cfg);

    // Load conftest before importing test files — conftest_loader registers
    // sys.modules["conftest"] so test files can do `from conftest import my_fixture`.
    let session = match bridge::FixtureSession::new(py, &conftest_files) {
        Ok(s) => s,
        Err(e) => {
            let err =
                types::CollectError::PyError(format!("Failed to load conftest fixtures: {}", e));
            return Ok(make_error_rep().finish(&[err], false));
        }
    };

    // Load plugins declared in [tool.oxitest] plugins = [...]
    if !cfg.plugins.is_empty() {
        if let Err(e) = session.load_plugins(py, &cfg.plugins, &cfg.plugin_settings) {
            let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
            return Ok(make_error_rep().finish(&[err], false));
        }
    }

    cache.invalidate_modules();
    let (items, errors, raw_violations) =
        collect_items(py, &test_files, &cfg, &session, &mut cache);

    if !errors.is_empty() {
        return Ok(make_error_rep().finish(&errors, false));
    }

    let StrictResult {
        clean_items,
        violated_items,
        all_violations,
        suite_lines,
    } = match apply_strict(&cfg, items, raw_violations, use_color) {
        Ok(r) => r,
        Err(code) => return Ok(code),
    };

    let items = match apply_filters(clean_items, &cli, &cfg, &cache, &make_error_rep) {
        Ok(items) => items,
        Err(code) => return Ok(code),
    };

    let total = violated_items.len() + items.len();
    cache.invalidate(&items);

    // Fetch plugin reporters from Python registry.
    let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !cfg.plugins.is_empty() {
        bridge::get_plugin_reporters(py)
            .unwrap_or_default()
            .into_iter()
            .map(|obj| {
                Box::new(reporter::plugin::PyPluginReporter::new(obj))
                    as Box<dyn reporter::Reporter>
            })
            .collect()
    } else {
        vec![]
    };

    let mut rep = reporter::make_reporter(
        base.total(total).strict_suite_lines(suite_lines).build(),
        is_tty,
        cli.json.clone(),
        plugin_reporters,
    );

    let (interrupted, timings) = execute(
        py,
        items,
        violated_items,
        all_violations,
        &cfg,
        &cache,
        &session,
        &conftest_files,
        rep.as_mut(),
    );

    finalize(&mut cache, timings, cfg.cache_max_age, &rootdir);

    Ok(rep.finish(&[], interrupted))
}

#[cfg(test)]
#[path = "pipeline_tests.rs"]
mod tests;
