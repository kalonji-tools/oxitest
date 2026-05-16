#![allow(clippy::useless_conversion)] // pyo3 macros generate this
use clap::Parser;
use pyo3::prelude::*;
use std::io::IsTerminal;

mod bridge;
mod cache;
mod collector;
mod config;
mod filter;
mod marker;
mod parallel;
mod reporter;
mod scheduler;
mod strict;
mod types;
mod worker_result;

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
                session.end_module(py, module_path).ok(); // ensure teardown runs before break
                break 'run;
            }
        }
        session.end_module(py, module_path).ok();
    }
    session.end_session(py).ok();

    (interrupted, timings)
}

#[pyfunction]
fn run(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let argv: Vec<String> = std::iter::once("oxitest".to_string()).chain(args).collect();

    let cli = match config::Cli::try_parse_from(&argv) {
        Ok(c) => c,
        Err(e) => {
            // Clap formats this for the user; subscriber may not be initialised yet.
            eprintln!("{}", e);
            return Ok(4);
        }
    };

    // Early-exit flags: handled before any filesystem setup.
    if cli.capture_environment {
        println!("{}", env_string(py));
        return Ok(0);
    }

    let rootdir = config::find_rootdir(cli.paths.first().map(|p| p.as_path()));
    let cfg = config::Config::load(&rootdir).merge_cli(&cli);
    let mut cache = cache::TestCache::load(&rootdir);

    let is_tty = std::io::stdout().is_terminal();
    let use_color = !cli.no_color && console::colors_enabled();
    let base = reporter::ReporterOptsBuilder::from_cli(&cli, use_color);
    let make_error_rep =
        || reporter::make_reporter(base.clone().verbose(false).build(), is_tty, None);

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

    cache.invalidate_modules();
    let (items, errors, raw_violations) =
        collect_items(py, &test_files, &cfg, &session, &mut cache);

    if !errors.is_empty() {
        return Ok(make_error_rep().finish(&errors, false));
    }

    // ── Strict: build violations list ────────────────────────────────────────────
    let all_violations: Vec<strict::StrictViolation> = if cfg.strict.is_some() {
        let mut v = strict::check_config(&cfg);
        v.extend(strict::check_collected(raw_violations));
        v
    } else {
        vec![]
    };

    // ── Strict abort mode ─────────────────────────────────────────────────────────
    if cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
        reporter::print_strict_abort(&all_violations, use_color);
        return Ok(3);
    }

    let items = filter::filter_items(items, cli.keyword.as_deref());
    let items = if let Some(expr) = &cli.marker {
        match marker::filter_by_marker_expr(items, expr) {
            Ok(items) => items,
            Err(e) => {
                return Ok(make_error_rep().finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -m expression: {}",
                        e
                    ))],
                    false,
                ));
            }
        }
    } else {
        items
    };

    let total_before_failed_filter = items.len();
    let items = match cfg.failed {
        Some(crate::config::FailedMode::Only) => {
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
        Some(crate::config::FailedMode::First) => {
            let failed_ids = cache.last_failed_ids();
            filter::sort_failed_first(items, &failed_ids)
        }
        None => items,
    };

    // ── Strict enforce mode ───────────────────────────────────────────────────────
    let suite_lines: Vec<String> = if cfg.strict == Some(config::StrictMode::Enforce) {
        strict::suite_level(&all_violations)
            .iter()
            .map(|v| strict::format_violation_line(v))
            .collect()
    } else {
        vec![]
    };

    // Partition items: those with per-test violations bypass workers.
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

    let total = violated_items.len() + clean_items.len();

    cache.invalidate(&clean_items);
    let estimated = cache.estimated_duration(&clean_items);

    let mut rep = reporter::make_reporter(
        base.total(total).strict_suite_lines(suite_lines).build(),
        is_tty,
        cli.json.clone(),
    );

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

    // Continue pipeline with only the clean items.
    let items = clean_items;

    let mut groups = filter::group_by_module(items);
    let failed_ids = cache.last_failed_ids();
    scheduler::apply_schedule_strategy(&mut groups, cfg.schedule, &cache, &failed_ids);

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

    let (interrupted, timings) = if use_parallel {
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
        parallel::run_phase_parallel(
            groups,
            &cfg,
            optimal_worker_count,
            &conftest_files,
            rep.as_mut(),
        )
    } else {
        run_phase(py, groups, &cfg, &cache, &session, rep.as_mut())
    };

    // Single pass: move node_id into outcome_pairs, clone once into timing_pairs.
    let mut timing_pairs: Vec<(types::NodeId, f64)> = Vec::with_capacity(timings.len());
    let mut outcome_pairs: Vec<(types::NodeId, String)> = Vec::with_capacity(timings.len());
    for t in timings {
        outcome_pairs.push((t.node_id.clone(), t.outcome));
        timing_pairs.push((t.node_id, t.duration_ms));
    }

    cache.merge(&timing_pairs, cfg.cache_max_age);
    cache.record_outcomes(&outcome_pairs);
    cache.save(&rootdir);

    Ok(rep.finish(&[], interrupted))
}

#[pymodule]
fn _oxitest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register a stderr tracing subscriber. try_init() is a no-op if a global
    // subscriber is already set (first cdylib imported in this process wins).
    // Users control verbosity via RUST_LOG (e.g. RUST_LOG=oxitest=debug).
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(std::io::stderr)
        .try_init();
    m.add_function(wrap_pyfunction!(run, m)?)?;
    Ok(())
}

#[cfg(test)]
mod mtime_tests {
    use super::*;

    #[test]
    fn file_mtime_secs_returns_nonzero_for_existing_file() {
        let mtime = file_mtime_secs(camino::Utf8Path::new(file!()));
        assert!(mtime > 0, "mtime must be non-zero for an existing file");
    }

    #[test]
    fn file_mtime_secs_returns_zero_for_missing_file() {
        let mtime = file_mtime_secs(camino::Utf8Path::new("/nonexistent/path/xyz.py"));
        assert_eq!(mtime, 0);
    }
}

#[cfg(test)]
mod timeout_tests {
    use super::*;
    use crate::cache::TestCache;
    use crate::types::{NodeId, TestItem};

    fn make_item(node_id: &str) -> TestItem {
        TestItem {
            node_id: NodeId::from_raw(node_id),
            module_path: camino::Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: node_id.to_string(),
            lineno: 0,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        }
    }

    #[test]
    fn no_multiplier_returns_global() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(resolve_timeout(&cache, &item, Some(30), None), Some(30));
    }

    #[test]
    fn no_multiplier_no_global_returns_none() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(resolve_timeout(&cache, &item, None, None), None);
    }

    #[test]
    fn multiplier_cold_cache_falls_back_to_global() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent")); // No cached entry → falls back to global
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(
            resolve_timeout(&cache, &item, Some(30), Some(3.0)),
            Some(30)
        );
    }

    #[test]
    fn multiplier_with_no_global_and_no_cache_returns_none() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(resolve_timeout(&cache, &item, None, Some(3.0)), None);
    }
}

#[cfg(test)]
mod ahash_tests {
    #[test]
    fn ahash_map_is_available() {
        // Fails to compile without the ahash dep.
        let mut m: ahash::AHashMap<String, usize> = ahash::AHashMap::new();
        m.insert("key".to_string(), 42);
        assert_eq!(m.get("key"), Some(&42));
    }
}

#[cfg(test)]
mod channel_tests {
    #[test]
    fn crossbeam_channel_drains_when_all_senders_dropped() {
        // Fails to compile without crossbeam-channel dep.
        let (tx, rx) = crossbeam_channel::unbounded::<u32>();
        let tx2 = tx.clone();
        tx.send(1).unwrap();
        tx2.send(2).unwrap();
        drop(tx);
        drop(tx2);
        let results: Vec<u32> = rx.into_iter().collect();
        assert_eq!(results.len(), 2);
    }
}

#[cfg(test)]
mod tracing_tests {
    #[test]
    fn tracing_macros_compile_without_subscriber() {
        // tracing macros are no-ops when no subscriber is active.
        // This test fails to compile without the tracing dep.
        tracing::warn!("no-op warning");
        tracing::error!("no-op error");
    }

    #[test]
    fn tracing_structured_fields_compile() {
        // Verify the structured field syntax used in parallel::spawn_worker compiles.
        let e = serde_json::from_str::<serde_json::Value>("bad").unwrap_err();
        let trimmed = "some output";
        tracing::warn!(error = %e, output = %trimmed, "bad worker output");
    }
}

#[cfg(test)]
mod strict_pipeline_tests {
    use super::*;
    use crate::config::{Config, StrictMode};
    use crate::strict::StrictViolation;
    use crate::types::NodeId;

    fn make_item(node_id: &str) -> types::TestItem {
        types::TestItem {
            node_id: NodeId::from_raw(node_id),
            module_path: camino::Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_foo".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        }
    }

    #[test]
    fn all_violations_empty_when_strict_none() {
        let cfg = Config::default(); // strict = None
        let raw: Vec<bridge::RawViolation> = vec![];
        let violations: Vec<StrictViolation> = if cfg.strict.is_some() {
            let mut v = strict::check_config(&cfg);
            v.extend(strict::check_collected(raw));
            v
        } else {
            vec![]
        };
        assert!(violations.is_empty());
    }

    #[test]
    fn partition_sends_violated_item_to_violated_vec() {
        let items = vec![
            make_item("tests/test_foo.py::test_bad"),
            make_item("tests/test_foo.py::test_good"),
        ];
        let violations = vec![StrictViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_bad"),
            lines: vec![5],
        }];
        let violated_ids: std::collections::HashSet<&str> = violations
            .iter()
            .filter_map(|v| v.node_id())
            .map(|id| id.as_ref())
            .collect();
        let (violated, clean): (Vec<_>, Vec<_>) = items
            .into_iter()
            .partition(|i| violated_ids.contains(i.node_id.as_ref()));
        assert_eq!(violated.len(), 1);
        assert_eq!(clean.len(), 1);
        assert!(violated[0].node_id.as_ref().contains("test_bad"));
        assert!(clean[0].node_id.as_ref().contains("test_good"));
    }
}
