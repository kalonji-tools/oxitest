//! Pure helper functions used by pipeline phases.
//!
//! Extracted from `mod.rs` to keep the orchestrator slim.

use std::sync::Arc;

use crate::cache::{OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{bridge, cache, config, filter, marker, parallel, reporter, scheduler, strict, types};
use pyo3::prelude::*;
use traits::{ExecutionHarness, ModuleCollector, ParallelRunner, Session, TestRunner};

use super::traits;

pub(super) fn file_mtime_secs(path: &camino::Utf8Path) -> u64 {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .map(|t| {
            t.duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
        })
        .unwrap_or(0)
}

pub(super) fn collect_items(
    py: Python<'_>,
    test_files: &[camino::Utf8PathBuf],
    cfg: &config::Config,
    session: &dyn Session,
    collector: &dyn ModuleCollector,
    cache: &mut impl cache::ModuleCache,
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

pub(super) fn resolve_timeout(
    cache: &(impl cache::TimingCache + ?Sized),
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

pub(super) struct ExecutionContext<'a> {
    pub(super) cfg: &'a config::Config,
    pub(super) cache: &'a cache::TestCache,
    pub(super) session: &'a dyn Session,
    pub(super) conftest_files: &'a [camino::Utf8PathBuf],
    pub(super) runner: &'a dyn TestRunner,
    pub(super) parallel: &'a dyn ParallelRunner,
}

pub(in crate::pipeline) fn early_exit_with_error(
    errors: &[types::CollectError],
    make_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> ExitCode {
    make_rep()
        .finish(errors, false, &reporter::RunStats::new())
        .code()
}

// ─── Execution Harnesses ─────────────────────────────────────────────────────

/// Serial execution harness — runs tests in-process, one at a time.
pub(super) struct SerialHarness<'a> {
    pub py: Python<'a>,
    pub runner: &'a dyn super::traits::TestRunner,
    pub session: &'a dyn super::traits::Session,
    pub cache: &'a dyn cache::TimingCache,
    pub timeout_secs: Option<u64>,
    pub timeout_multiplier: Option<f64>,
    pub maxfail: usize,
    pub debug_mode: Option<&'a str>,
    pub keep_tmp: Option<&'a str>,
    pub show_locals: bool,
    pub show_internals: bool,
}

impl super::traits::ExecutionHarness for SerialHarness<'_> {
    fn execute_groups(
        &self,
        groups: Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
        rep: &mut dyn reporter::Reporter,
    ) -> parallel::PhaseResult {
        let mut acc = types::FailureAccumulator::new(self.maxfail);
        let mut interrupted = false;
        let total: usize = groups.iter().map(|(_, items)| items.len()).sum();
        let mut timings: Vec<types::TestTiming> = Vec::with_capacity(total);

        'run: for (module_path, items) in &groups {
            for item in items {
                rep.test_started(item);
                let timeout =
                    resolve_timeout(self.cache, item, self.timeout_secs, self.timeout_multiplier);
                let (outcome, duration_ms) = self.runner.run_timed(
                    self.py,
                    item,
                    self.session,
                    timeout,
                    self.debug_mode,
                    self.keep_tmp,
                    self.show_locals,
                    self.show_internals,
                );
                timings.push(types::TestTiming {
                    node_id: item.node_id.clone(),
                    duration_ms,
                    outcome: types::OutcomeKind::from(&outcome),
                });
                rep.test_completed(item, &outcome, duration_ms);
                if acc.record(&outcome) {
                    interrupted = true;
                    if let Err(e) = self.session.end_module(self.py, module_path) {
                        tracing::warn!(%e, module = %module_path, "teardown error in end_module");
                        rep.record_teardown_warning(
                            &format!("end_module({})", module_path),
                            &e.to_string(),
                        );
                    }
                    break 'run;
                }
            }
            if let Err(e) = self.session.end_module(self.py, module_path) {
                tracing::warn!(%e, module = %module_path, "teardown error in end_module");
                rep.record_teardown_warning(
                    &format!("end_module({})", module_path),
                    &e.to_string(),
                );
            }
        }
        if let Err(e) = self.session.end_session(self.py) {
            tracing::warn!(%e, "teardown error in end_session");
            rep.record_teardown_warning("end_session", &e.to_string());
        }

        parallel::PhaseResult {
            interrupted,
            timings,
        }
    }
}

/// Parallel execution harness — delegates to worker subprocesses.
pub(super) struct ParallelHarness<'a> {
    pub parallel: &'a dyn super::traits::ParallelRunner,
    pub cfg: &'a config::Config,
    pub workers: usize,
    pub conftest_files: &'a [camino::Utf8PathBuf],
}

impl super::traits::ExecutionHarness for ParallelHarness<'_> {
    fn execute_groups(
        &self,
        groups: Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
        rep: &mut dyn reporter::Reporter,
    ) -> parallel::PhaseResult {
        self.parallel
            .run_parallel(groups, self.cfg, self.workers, self.conftest_files, rep)
    }
}

#[derive(Debug)]
pub(super) struct StrictResult {
    pub(super) clean_items: Vec<Arc<types::TestItem>>,
    pub(super) violated_items: Vec<Arc<types::TestItem>>,
    pub(super) all_violations: Vec<strict::StrictViolation>,
    pub(super) suite_lines: Vec<String>,
}

/// Build violations, handle abort mode, produce enforce-mode suite lines, and
/// partition items into violated vs. clean.  Returns `Err(ExitCode::CollectError)` when abort mode
/// detects violations (caller should propagate as early exit).
pub(super) fn apply_strict(
    cfg: &config::Config,
    items: Vec<Arc<types::TestItem>>,
    raw_violations: Vec<bridge::RawViolation>,
    use_color: bool,
) -> Result<StrictResult, ExitCode> {
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
        return Err(ExitCode::CollectError);
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
/// (surfaced via the error reporter supplied by `make_error_rep`).
pub(super) fn apply_filters(
    items: Vec<Arc<types::TestItem>>,
    cli: &config::Cli,
    cfg: &config::Config,
    cache: &impl cache::OutcomeCache,
    make_error_rep: &dyn Fn() -> Box<dyn reporter::Reporter>,
) -> Result<Vec<Arc<types::TestItem>>, ExitCode> {
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
                        &reporter::RunStats::new(),
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

/// Partition module groups into inprocess (main-process) and parallel-eligible groups.
///
/// Tests marked `@oxi.mark.inprocess` are extracted into their own group list.
/// If a module has a mix of inprocess and non-inprocess tests, the module appears
/// in both lists with the appropriate subset.
#[allow(dead_code)] // Used by later tasks (inprocess unit tests).
fn partition_inprocess_groups(
    groups: Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
) -> (
    Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
    Vec<(camino::Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
) {
    let mut inprocess = Vec::new();
    let mut parallel = Vec::new();

    for (module_path, items) in groups {
        let (inp, par): (Vec<_>, Vec<_>) = items
            .into_iter()
            .partition(|item| item.markers.iter().any(|m| m == "inprocess"));

        if !inp.is_empty() {
            inprocess.push((module_path.clone(), inp));
        }
        if !par.is_empty() {
            parallel.push((module_path, par));
        }
    }

    (inprocess, parallel)
}

/// Report violated items, group and schedule the clean items, decide
/// serial vs. parallel, dispatch, and return `(interrupted, timings)`.
///
/// `cache.invalidate()` and `cache.estimated_duration()` are called here
/// because they feed directly into the parallel-dispatch decision.
pub(super) fn execute(
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

        // Partition inprocess-marked tests: run on main process before parallel dispatch.
        let (inprocess_groups, parallel_groups) = partition_inprocess_groups(groups);
        let mut inprocess_result = if inprocess_groups.is_empty() {
            parallel::PhaseResult {
                interrupted: false,
                timings: Vec::new(),
            }
        } else {
            let harness = SerialHarness {
                py,
                runner: ctx.runner,
                session: ctx.session,
                cache: ctx.cache,
                timeout_secs: ctx.cfg.timeout_secs,
                timeout_multiplier: ctx.cfg.timeout_multiplier,
                maxfail: ctx.cfg.maxfail,
                debug_mode: ctx.cfg.debug.as_ref().map(|m| m.as_str()),
                keep_tmp: ctx.cfg.keep_tmp.as_ref().map(|m| m.as_str()),
                show_locals: ctx.cfg.show_locals,
                show_internals: ctx.cfg.show_internals,
            };
            harness.execute_groups(inprocess_groups, rep)
        };

        // If inprocess phase was interrupted (maxfail), skip parallel.
        if inprocess_result.interrupted {
            return inprocess_result;
        }

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

        if parallel_groups.is_empty() {
            return inprocess_result;
        }

        let harness = ParallelHarness {
            parallel: ctx.parallel,
            cfg: ctx.cfg,
            workers: optimal_worker_count,
            conftest_files: ctx.conftest_files,
        };
        let parallel_result = harness.execute_groups(parallel_groups, rep);

        // Merge results: combine timings, interrupted if either phase was.
        inprocess_result.timings.extend(parallel_result.timings);
        inprocess_result.interrupted |= parallel_result.interrupted;
        inprocess_result
    } else {
        let harness = SerialHarness {
            py,
            runner: ctx.runner,
            session: ctx.session,
            cache: ctx.cache,
            timeout_secs: ctx.cfg.timeout_secs,
            timeout_multiplier: ctx.cfg.timeout_multiplier,
            maxfail: ctx.cfg.maxfail,
            debug_mode: ctx.cfg.debug.as_ref().map(|m| m.as_str()),
            keep_tmp: ctx.cfg.keep_tmp.as_ref().map(|m| m.as_str()),
            show_locals: ctx.cfg.show_locals,
            show_internals: ctx.cfg.show_internals,
        };
        harness.execute_groups(groups, rep)
    }
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

/// Format collected tests as a string for `--list` output.
///
/// - **Normal**: one node ID per line (no footer).
/// - **Detailed**: node IDs with marks/fixtures metadata, plus a count footer.
/// - **Full**: parametrized cases grouped under their parent function, plus a count footer.
pub(super) fn format_test_list(
    items: &[Arc<types::TestItem>],
    verbosity: crate::config::Verbosity,
) -> String {
    use crate::config::Verbosity;
    use std::fmt::Write;

    if items.is_empty() {
        return "no tests collected".to_string();
    }

    let mut out = String::new();

    match verbosity {
        Verbosity::Normal => {
            let lines: Vec<&str> = items.iter().map(|i| i.node_id.as_ref()).collect();
            return lines.join("\n");
        }
        Verbosity::Detailed => {
            let id_width = items
                .iter()
                .map(|i| i.node_id.as_ref().len())
                .max()
                .unwrap_or(10);

            for item in items {
                let marks = if item.markers.is_empty() {
                    String::new()
                } else {
                    format!("marks: {}", item.markers.join(", "))
                };
                let fixtures = if item.fixture_names.is_empty() {
                    String::new()
                } else {
                    format!("fixtures: {}", item.fixture_names.join(", "))
                };
                let mut parts = Vec::new();
                if !marks.is_empty() {
                    parts.push(marks);
                }
                if !fixtures.is_empty() {
                    parts.push(fixtures);
                }
                let suffix = parts.join("    ");
                if suffix.is_empty() {
                    writeln!(out, "{}", item.node_id.as_ref()).unwrap();
                } else {
                    writeln!(out, "{:<id_width$}    {}", item.node_id.as_ref(), suffix,).unwrap();
                }
            }
        }
        Verbosity::Full => {
            let mut current_fn: Option<(&str, &str)> = None;

            for item in items {
                let is_param = item.param_id.is_some();
                let module_fn = (item.module_path.as_str(), item.fn_name.as_str());

                if is_param {
                    if current_fn != Some(module_fn) {
                        writeln!(out, "{}::{}", item.module_path, item.fn_name).unwrap();
                        current_fn = Some(module_fn);
                    }
                    let param_id = item.param_id.as_deref().unwrap_or("?");
                    let pv_repr = if item.param_values.is_empty() {
                        String::new()
                    } else {
                        let fields: Vec<String> = item
                            .param_values
                            .iter()
                            .map(|(k, v)| format!("{}={}", k, v))
                            .collect();
                        format!("Case({})", fields.join(", "))
                    };
                    let marks = if item.markers.is_empty() {
                        String::new()
                    } else {
                        format!("marks: [{}]", item.markers.join(", "))
                    };
                    let fixtures = if item.fixture_names.is_empty() {
                        String::new()
                    } else {
                        format!("fixtures: [{}]", item.fixture_names.join(", "))
                    };
                    let mut parts = Vec::new();
                    if !pv_repr.is_empty() {
                        parts.push(pv_repr);
                    }
                    if !marks.is_empty() {
                        parts.push(marks);
                    }
                    if !fixtures.is_empty() {
                        parts.push(fixtures);
                    }
                    writeln!(out, "  [{}]    {}", param_id, parts.join("    ")).unwrap();
                } else {
                    current_fn = None;
                    let marks = if item.markers.is_empty() {
                        String::new()
                    } else {
                        format!("marks: [{}]", item.markers.join(", "))
                    };
                    let fixtures = if item.fixture_names.is_empty() {
                        String::new()
                    } else {
                        format!("fixtures: [{}]", item.fixture_names.join(", "))
                    };
                    let mut parts = Vec::new();
                    if !marks.is_empty() {
                        parts.push(marks);
                    }
                    if !fixtures.is_empty() {
                        parts.push(fixtures);
                    }
                    let suffix = parts.join("    ");
                    if suffix.is_empty() {
                        writeln!(out, "{}", item.node_id.as_ref()).unwrap();
                    } else {
                        writeln!(out, "{}    {}", item.node_id.as_ref(), suffix).unwrap();
                    }
                }
            }
        }
    }

    write!(
        out,
        "\n{} test{}",
        items.len(),
        if items.len() == 1 { "" } else { "s" }
    )
    .unwrap();
    out
}
