//! Pipeline orchestrator — the main entry point for an oxitest run.
//!
//! [`run()`] ties all modules together in a fixed sequence:
//! config → collect files → import tests → filter → schedule → execute → report → cache.
//!
//! Both serial and parallel execution paths converge through this module.

pub(crate) mod traits;

use std::sync::Arc;

use crate::{
    bridge, cache, collector, config, filter, marker, parallel, reporter, scheduler, strict, types,
};
use clap::Parser;
use pyo3::prelude::*;
use std::io::IsTerminal;
use traits::{ModuleCollector, ParallelRunner, Session, TestRunner};

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
    session: &dyn Session,
    collector: &dyn ModuleCollector,
    cache: &mut cache::TestCache,
) -> (
    Vec<Arc<types::TestItem>>,
    Vec<types::CollectError>,
    Vec<bridge::RawViolation>,
) {
    let mut items: Vec<Arc<types::TestItem>> = Vec::new();
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
        match collector.collect_module(py, file, session, collect_violations) {
            Ok((file_items, file_violations)) => {
                let arc_items: Vec<Arc<types::TestItem>> =
                    file_items.into_iter().map(Arc::new).collect();
                // Skip cache write in strict mode: violations are not cached,
                // so the cached entry would silently drop violation data on the next run.
                if mtime != 0 && !collect_violations {
                    cache.update_module_cache(file, mtime, &arc_items);
                }
                raw_violations.extend(file_violations);
                items.extend(arc_items);
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

fn resolve_color(mode: config::ColorMode, is_tty: bool) -> bool {
    match mode {
        config::ColorMode::Always => {
            console::set_colors_enabled(true);
            true
        }
        config::ColorMode::Never => false,
        config::ColorMode::Auto => is_tty && console::colors_enabled(),
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

struct ExecutionContext<'a> {
    cfg: &'a config::Config,
    cache: &'a cache::TestCache,
    session: &'a dyn Session,
    conftest_files: &'a [camino::Utf8PathBuf],
    runner: &'a dyn TestRunner,
    parallel: &'a dyn ParallelRunner,
}

fn early_exit_with_error(
    errors: &[types::CollectError],
    make_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> i32 {
    make_rep().finish(errors, false).code()
}

fn run_phase(
    py: Python<'_>,
    groups: Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
    ctx: &ExecutionContext<'_>,
    rep: &mut dyn reporter::Reporter,
) -> parallel::PhaseResult {
    let mut acc = types::FailureAccumulator::new(ctx.cfg.maxfail);
    let mut interrupted = false;
    let total: usize = groups.iter().map(|(_, items)| items.len()).sum();
    let mut timings: Vec<types::TestTiming> = Vec::with_capacity(total);

    'run: for (module_path, items) in &groups {
        for item in items {
            rep.test_started(item);
            let start = std::time::Instant::now();
            let timeout = resolve_timeout(
                ctx.cache,
                item,
                ctx.cfg.timeout_secs,
                ctx.cfg.timeout_multiplier,
            );
            let outcome = ctx.runner.run_test(py, item, ctx.session, timeout);
            let duration_ms = types::DurationMs::new(start.elapsed().as_secs_f64() * 1000.0);
            timings.push(types::TestTiming {
                node_id: item.node_id.clone(),
                duration_ms,
                outcome: types::OutcomeKind::from(&outcome),
            });
            rep.test_completed(item, &outcome, duration_ms);
            if acc.record(&outcome) {
                interrupted = true;
                if let Err(e) = ctx.session.end_module(py, module_path) {
                    tracing::warn!(%e, module = %module_path, "teardown error in end_module");
                    rep.record_teardown_warning(
                        &format!("end_module({})", module_path),
                        &e.to_string(),
                    );
                }
                break 'run;
            }
        }
        if let Err(e) = ctx.session.end_module(py, module_path) {
            tracing::warn!(%e, module = %module_path, "teardown error in end_module");
            rep.record_teardown_warning(&format!("end_module({})", module_path), &e.to_string());
        }
    }
    if let Err(e) = ctx.session.end_session(py) {
        tracing::warn!(%e, "teardown error in end_session");
        rep.record_teardown_warning("end_session", &e.to_string());
    }

    parallel::PhaseResult {
        interrupted,
        timings,
    }
}

#[derive(Debug)]
struct SetupContext {
    cfg: config::Config,
    cache: cache::TestCache,
    cli: config::Cli,
    rootdir: camino::Utf8PathBuf,
    is_tty: bool,
    use_color: bool,
    base: reporter::ReporterOptsBuilder,
}

fn setup(py: Python<'_>, args: &[String]) -> PyResult<Result<Box<SetupContext>, i32>> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string())
        .chain(args.iter().cloned())
        .collect();

    let cli = match config::Cli::try_parse_from(&argv) {
        Ok(c) => c,
        Err(e) => {
            // Clap formats this for the user; subscriber may not be initialised yet.
            eprintln!("{}", e);
            return Ok(Err(4));
        }
    };

    // Early-exit flags: handled before any filesystem setup.
    if cli.capture_environment {
        println!("{}", env_string(py));
        return Ok(Err(0));
    }

    let rootdir = config::find_rootdir(cli.paths.first().map(|p| p.as_path()));
    let cfg = config::Config::load(&rootdir).merge_cli(&cli);
    let cache = cache::TestCache::load(&rootdir);

    let is_tty = std::io::stdout().is_terminal();
    let use_color = resolve_color(cfg.color, is_tty);
    let resolved_tb = cli.tb.clone().unwrap_or(cfg.tb.clone());
    let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color)
        .tb(resolved_tb)
        .show_tips(cli.tips)
        .show_warnings(cli.warnings);

    Ok(Ok(Box::new(SetupContext {
        cfg,
        cache,
        cli,
        rootdir,
        is_tty,
        use_color,
        base,
    })))
}

#[derive(Debug)]
struct StrictResult {
    clean_items: Vec<Arc<types::TestItem>>,
    violated_items: Vec<Arc<types::TestItem>>,
    all_violations: Vec<strict::StrictViolation>,
    suite_lines: Vec<String>,
}

/// Build violations, handle abort mode, produce enforce-mode suite lines, and
/// partition items into violated vs. clean.  Returns `Err(3)` when abort mode
/// detects violations (caller should propagate as `Ok(3)`).
fn apply_strict(
    cfg: &config::Config,
    items: Vec<Arc<types::TestItem>>,
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
        let abort_lines: Vec<String> = all_violations
            .iter()
            .map(strict::format_violation_line)
            .collect();
        reporter::print_strict_abort(&abort_lines, use_color);
        return Err(3);
    }

    // Enforce mode: build suite-level violation lines and partition items.
    let suite_lines: Vec<String> = if cfg.strict == Some(config::StrictMode::Enforce) {
        strict::suite_level(&all_violations)
            .iter()
            .map(|v| v.to_string())
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
    items: Vec<Arc<types::TestItem>>,
    cli: &config::Cli,
    cfg: &config::Config,
    cache: &cache::TestCache,
    make_error_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> Result<Vec<Arc<types::TestItem>>, i32> {
    // Keyword filter (-k).
    let items = filter::filter_items(items, cli.keyword.as_deref());

    // Marker expression filter (-m).
    let items = if let Some(expr) = &cli.marker {
        match marker::filter_by_marker_expr(items, expr) {
            Ok(items) => items,
            Err(e) => {
                let code = make_error_rep()
                    .finish(
                        &[types::CollectError::PyError(format!(
                            "invalid -m expression: {}",
                            e
                        ))],
                        false,
                    )
                    .code();
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
                tracing::info!(
                    count = items.len(),
                    "no recorded failures — running all tests"
                );
                items
            } else {
                let filtered = filter::filter_last_failed(items, &failed_ids);
                tracing::info!(
                    running = filtered.len(),
                    total = total_before_failed_filter,
                    "running tests in --failed=only mode"
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
fn execute(
    py: Python<'_>,
    clean_items: Vec<Arc<types::TestItem>>,
    violated_items: Vec<Arc<types::TestItem>>,
    all_violations: Vec<strict::StrictViolation>,
    ctx: &ExecutionContext<'_>,
    rep: &mut dyn reporter::Reporter,
) -> parallel::PhaseResult {
    // Immediately report violated items as Error outcomes (no worker dispatch).
    for item in &violated_items {
        // Per-test items may have multiple violations; we report only the first to keep
        // the error message focused. Users address violations one at a time.
        if let Some(pv) = all_violations.iter().find_map(|v| match v {
            strict::StrictViolation::PerTest(pv) if pv.node_id() == &item.node_id => Some(pv),
            _ => None,
        }) {
            let outcome = strict::per_test_error(pv);
            rep.test_started(item);
            rep.test_completed(item, &outcome, types::DurationMs::ZERO);
        }
    }

    let estimated = ctx.cache.estimated_duration(&clean_items);

    let mut groups = filter::group_by_module(clean_items);
    let failed_ids = ctx.cache.last_failed_ids();
    scheduler::apply_schedule_strategy(&mut groups, ctx.cfg.schedule, ctx.cache, &failed_ids);

    let total_tests: usize = groups.iter().map(|(_, items)| items.len()).sum();
    let cpu_count = config::cpu_count();

    let force_parallel = ctx.cfg.workers.is_some() && !ctx.cfg.serial;
    let use_parallel = !ctx.cfg.serial
        && ctx.cfg.worker_count() > 1
        && (force_parallel
            || match estimated {
                Some(est) => {
                    est.as_millis() as f64
                        > ctx.cfg.spawn_overhead_ms * ctx.cfg.worker_count() as f64
                }
                None => total_tests >= ctx.cfg.min_parallel_tests, // cold cache: fall back to configured threshold
            });

    if use_parallel {
        debug_assert!(
            !ctx.cfg.serial,
            "compute_optimal_workers is unreachable in serial mode"
        );
        let optimal_worker_count = parallel::compute_optimal_workers(
            ctx.cfg.workers,
            ctx.cfg.serial,
            cpu_count,
            estimated,
            ctx.cfg.spawn_overhead_ms,
        );
        // Warn when session-scoped (shared=True) fixtures are present: each worker
        // subprocess creates its own FixtureSession, so these fixtures execute once
        // per worker rather than once per run.
        let shared_names = ctx.session.shared_fixture_names(py);
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
        let parallel::PhaseResult {
            interrupted,
            timings,
        } = ctx.parallel.run_parallel(
            groups,
            ctx.cfg,
            optimal_worker_count,
            ctx.conftest_files,
            rep,
        );
        parallel::PhaseResult {
            interrupted,
            timings,
        }
    } else {
        run_phase(py, groups, ctx, rep)
    }
}

/// Merge timings into the cache, record outcomes, and persist to disk.
fn finalize(
    cache: &mut cache::TestCache,
    timings: &[types::TestTiming],
    cache_max_age: u32,
    rootdir: &camino::Utf8Path,
) {
    cache.merge_timings(timings, cache_max_age);
    cache.record_timing_outcomes(timings);
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
        Err(code) => return Ok(code),
        Ok(ctx) => *ctx,
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
            return Ok(early_exit_with_error(&[err], &make_error_rep));
        }
    };

    // Load plugins declared in [tool.oxitest] plugins = [...]
    if !cfg.plugins.is_empty() {
        if let Err(e) = session.load_plugins(py, &cfg.plugins, &cfg.plugin_settings) {
            let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
            return Ok(early_exit_with_error(&[err], &make_error_rep));
        }
    }

    // Resolve and set the async backend
    if let Err(e) = session.init_async_backend(py, &cfg.async_backend) {
        let err = types::CollectError::PyError(format!("Async backend init failed: {}", e));
        return Ok(early_exit_with_error(&[err], &make_error_rep));
    }

    let collector_impl = traits::BridgeCollector;
    let runner_impl = traits::BridgeRunner;
    let parallel_impl = traits::DefaultParallelRunner;

    cache.invalidate_modules();
    let (items, errors, raw_violations) =
        collect_items(py, &test_files, &cfg, &session, &collector_impl, &mut cache);

    if !errors.is_empty() {
        return Ok(early_exit_with_error(&errors, &make_error_rep));
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
    let async_count = items.iter().filter(|i| i.is_async).count();
    let max_name_width = items
        .iter()
        .chain(violated_items.iter())
        .map(|i| i.fn_name.len())
        .max()
        .unwrap_or(30);
    cache.invalidate(&items);

    // Fetch plugin reporters from Python registry.
    let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !cfg.plugins.is_empty() {
        bridge::get_plugin_reporters(py, &session)
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
        base.total(total)
            .async_count(async_count)
            .name_width(max_name_width)
            .strict_suite_lines(suite_lines)
            .build(),
        is_tty,
        cli.json.clone(),
        plugin_reporters,
    );

    let ctx = ExecutionContext {
        cfg: &cfg,
        cache: &cache,
        session: &session,
        conftest_files: &conftest_files,
        runner: &runner_impl,
        parallel: &parallel_impl,
    };

    let parallel::PhaseResult {
        interrupted,
        timings,
    } = execute(
        py,
        items,
        violated_items,
        all_violations,
        &ctx,
        rep.as_mut(),
    );

    finalize(&mut cache, &timings, cfg.cache_max_age, &rootdir);

    Ok(rep.finish(&[], interrupted).code())
}

#[cfg(test)]
#[path = "../pipeline_tests.rs"]
mod tests;
