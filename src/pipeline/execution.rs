//! Test execution: serial/parallel dispatch, harnesses, and auto-arrangement.

use std::sync::Arc;

use camino::Utf8PathBuf;
use pyo3::prelude::*;

use super::arrange::{self, ExecutionStrategy};
use crate::scheduler::{ModuleGroup, TaskGroup};
use crate::{bridge, cache, config, filter, parallel, reporter, scheduler, strict, types};

pub(super) struct ExecutionContext<'a> {
    pub(super) cfg: &'a config::Config,
    pub(super) cache: &'a cache::TestCache,
    pub(super) session: &'a bridge::FixtureSession,
    pub(super) conftest_files: &'a [Utf8PathBuf],
    pub(super) fixture_modules: &'a [types::FixtureModule],
    pub(super) python_bin: &'a str,
    /// Sum of AST-derived body weights from prescan; used as fallback for cold-cache estimation.
    pub(super) ast_weight: Option<crate::types::DurationMs>,
}

/// Debug and display options passed through the execution pipeline.
#[derive(Debug, Clone, Copy)]
pub(crate) struct DebugOptions<'a> {
    pub debug_mode: Option<&'a str>,
    pub keep_tmp: &'a str,
    pub show_locals: bool,
    pub show_internals: bool,
}

impl Default for DebugOptions<'_> {
    fn default() -> Self {
        Self {
            debug_mode: None,
            keep_tmp: "cleanup",
            show_locals: false,
            show_internals: false,
        }
    }
}

/// Move any diagnostics the session has accumulated into the reporter.
///
/// Must run after every lifecycle call, not only after each test. Scope
/// teardowns swallow their exception and emit a `Diagnostic` instead of
/// raising, so `end_module` / `end_session` returning `Ok` does **not** mean
/// nothing went wrong. Draining only inside the test loop means a failing
/// module- or session-scope teardown surfaces just when a later test happens
/// to trigger a drain — and is lost outright when nothing runs afterwards.
fn drain_diagnostics(
    py: Python<'_>,
    session: &bridge::FixtureSession,
    rep: &mut dyn reporter::Reporter,
) {
    let diags = bridge::drain_session_diagnostics(py, session);
    if !diags.is_empty() {
        rep.record_diagnostics(diags);
    }
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

pub(crate) fn run_timed(
    py: Python<'_>,
    item: &types::TestItem,
    session: &bridge::FixtureSession,
    timeout: Option<u64>,
    opts: DebugOptions<'_>,
) -> (types::TestOutcome, types::DurationMs) {
    let start = std::time::Instant::now();
    let outcome =
        bridge::run_test_with_session_obj(py, item, session.as_py_object(py), timeout, opts);
    let duration_ms = types::DurationMs::new(start.elapsed().as_secs_f64() * 1000.0);
    (outcome, duration_ms)
}

/// Anchor directories that declared a `lifetime="package"` fixture.
///
/// Read off the prescan results carried on [`types::FixtureModule`], so the
/// decision is available before any Python runs and holds even if registration
/// of a declaration failed.
fn declaring_package_dirs(fixture_modules: &[types::FixtureModule]) -> Vec<Utf8PathBuf> {
    let mut dirs: Vec<Utf8PathBuf> = fixture_modules
        .iter()
        .filter(|m| m.declares_package())
        .map(|m| m.anchor.clone())
        .collect();
    // Both declaration homes in one directory would otherwise list it twice,
    // which would merge the same subtree into two groups.
    dirs.sort();
    dirs.dedup();
    dirs
}

/// Warn about each package declaration that costs the run its parallelism.
///
/// Choosing a structural guarantee (#1710 decision 1) means the tier never
/// lies about *how many* instances exist. Staying silent about what that costs
/// would reinstate the lie from the other side: a suite dropping from N workers
/// to one because of a single decorator, diagnosed weeks later by whoever
/// bisects CI times.
///
/// Emitted only when the collapse actually merges more than one module. A
/// declaring package holding a single module costs nothing, and warning there
/// would be noise that trains users to ignore the message.
fn warn_about_package_collapse(
    fixture_modules: &[types::FixtureModule],
    groups: &[ModuleGroup],
    declaring_dirs: &[Utf8PathBuf],
    rep: &mut dyn reporter::Reporter,
) {
    use crate::reporter::stats::{DiagnosticEntry, DiagnosticSeverity};

    for dir in declaring_dirs {
        let merged = groups
            .iter()
            .filter(|g| g.module_path.starts_with(dir))
            .count();
        if merged < 2 {
            continue;
        }
        for module in fixture_modules.iter().filter(|m| &m.anchor == dir) {
            for decl in &module.package_declarations {
                rep.record_diagnostics(vec![DiagnosticEntry {
                    severity: DiagnosticSeverity::Warning,
                    context: std::sync::Arc::from(decl.fn_name.as_str()),
                    message: format!(
                        "{} (lifetime=\"package\") co-locates {merged} modules onto \
                         one worker — parallelism is disabled for {dir}. Narrow the \
                         fixture's package, or drop to lifetime=\"module\".",
                        decl.fn_name,
                    ),
                    file: Some(module.module.clone()),
                    lineno: Some(decl.lineno),
                }]);
            }
        }
    }
}

/// Dispatch enum for serial vs. parallel execution.
///
/// Replaces the former `ExecutionHarness` trait and its two implementers
/// (`SerialHarness`, `ParallelHarness`). Construct the appropriate variant
/// and call `execute_groups` directly — no dynamic dispatch needed.
pub(super) enum ExecutionDispatch<'a> {
    /// Runs tests in-process, one at a time.
    Serial {
        py: Python<'a>,
        session: &'a bridge::FixtureSession,
        cache: &'a cache::TestCache,
        timeout_secs: Option<u64>,
        timeout_multiplier: Option<f64>,
        maxfail: usize,
        opts: DebugOptions<'a>,
    },
    /// Delegates to worker subprocesses.
    Parallel {
        cfg: &'a config::Config,
        workers: usize,
        session_inputs: parallel::SessionInputs<'a>,
        python_bin: &'a str,
        /// Pre-warmed worker pool, consumed on first call.
        pool: Option<Vec<parallel::PrewarmedWorker>>,
    },
}

impl<'a> ExecutionDispatch<'a> {
    /// Build a `Serial` variant from an [`ExecutionContext`].
    fn serial_from_ctx(py: Python<'a>, ctx: &'a ExecutionContext<'a>) -> Self {
        ExecutionDispatch::Serial {
            py,
            session: ctx.session,
            cache: ctx.cache,
            timeout_secs: ctx.cfg.exec.timeout_secs,
            timeout_multiplier: ctx.cfg.exec.timeout_multiplier,
            maxfail: ctx.cfg.exec.maxfail,
            opts: DebugOptions {
                debug_mode: ctx.cfg.exec.mode.debug_mode().map(|m| m.as_str()),
                keep_tmp: ctx.cfg.output.keep_tmp.as_str(),
                show_locals: ctx.cfg.output.show_locals,
                show_internals: ctx.cfg.output.show_internals,
            },
        }
    }

    /// Run one phase's worth of work.
    ///
    /// Takes [`TaskGroup`]s rather than [`ModuleGroup`]s: a group is what one
    /// worker task covers, which `group_by_package` (#1710) makes wider than a
    /// single module. Callers wrap with [`TaskGroup::single`] until then.
    fn execute_groups(
        &mut self,
        groups: Vec<TaskGroup>,
        rep: &mut dyn reporter::Reporter,
    ) -> parallel::PhaseResult {
        match self {
            ExecutionDispatch::Serial {
                py,
                session,
                cache,
                timeout_secs,
                timeout_multiplier,
                maxfail,
                opts,
            } => {
                let mut acc = types::FailureAccumulator::new(*maxfail);
                let mut interrupted = false;
                let total: usize = groups.iter().map(|g| g.item_count()).sum();
                let mut timings: Vec<types::TestTiming> = Vec::with_capacity(total);

                let end_scope =
                    |rep: &mut dyn reporter::Reporter, context: String, result: PyResult<()>| {
                        if let Err(e) = result {
                            tracing::warn!(%e, %context, "teardown error");
                            rep.record_teardown_warning(&context, &e.to_string());
                        }
                        drain_diagnostics(*py, session, rep);
                    };

                // Modules drain as they finish; the package drains once the whole
                // group has. A package-lifetime value must outlive every module
                // in its subtree, so end_package cannot fire per module — and it
                // cannot ride on end_session either, because a serial run uses
                // one session for the entire run.
                'run: for group in &groups {
                    for ModuleGroup { module_path, items } in &group.modules {
                        for item in items {
                            rep.test_started(item);
                            let timeout =
                                resolve_timeout(cache, item, *timeout_secs, *timeout_multiplier);
                            let (outcome, duration_ms) =
                                run_timed(*py, item, session, timeout, *opts);
                            // Drain diagnostics emitted during test execution
                            drain_diagnostics(*py, session, rep);
                            timings.push(types::TestTiming {
                                node_id: item.node_id.clone(),
                                duration_ms,
                                outcome: types::OutcomeKind::from(&outcome),
                            });
                            rep.test_completed(item, &outcome, duration_ms, None);
                            if acc.record(&outcome) {
                                interrupted = true;
                                end_scope(
                                    rep,
                                    format!("end_module({})", module_path),
                                    session.end_module(*py, module_path),
                                );
                                break 'run;
                            }
                        }
                        end_scope(
                            rep,
                            format!("end_module({})", module_path),
                            session.end_module(*py, module_path),
                        );
                    }
                    let package = group.label();
                    end_scope(
                        rep,
                        format!("end_package({})", package),
                        session.end_package(*py, package),
                    );
                }
                end_scope(rep, "end_session".to_string(), session.end_session(*py));

                parallel::PhaseResult {
                    interrupted,
                    timings,
                }
            }
            ExecutionDispatch::Parallel {
                cfg,
                workers,
                session_inputs,
                python_bin,
                pool,
            } => parallel::run_phase_parallel(
                groups,
                cfg,
                *workers,
                *session_inputs,
                python_bin,
                rep,
                pool.take(),
            ),
        }
    }
}

/// Emit verbose scheduling diagnostics to stderr.
fn emit_scheduling_diagnostics(
    ctx: &ExecutionContext<'_>,
    plan: &arrange::ExecutionPlan,
    estimated: Option<std::time::Duration>,
    cpu_count: usize,
    shared_fixture_groups: &[Vec<String>],
) {
    let total_tests: usize = plan
        .inprocess_groups
        .iter()
        .chain(plan.parallel_groups.iter())
        .map(|g| g.items.len())
        .sum::<usize>()
        + plan
            .arranged_groups
            .iter()
            .flat_map(|g| g.iter())
            .map(|g| g.items.len())
            .sum::<usize>();

    match &plan.strategy {
        ExecutionStrategy::Serial => {
            if ctx.cfg.exec.mode.is_serial() {
                eprintln!("scheduling: serial (--serial flag)");
            } else if let Some(est) = estimated {
                eprintln!(
                    "scheduling: serial (est. {}ms <= spawn overhead {}ms x {} workers)",
                    est.as_millis(),
                    ctx.cfg.exec.spawn_overhead.as_f64() as u64,
                    ctx.cfg.worker_count(),
                );
            } else {
                eprintln!(
                    "scheduling: serial (cold cache, {} tests < min_parallel_tests {})",
                    total_tests, ctx.cfg.exec.min_parallel_tests,
                );
            }
            // Check if this was an auto-arrange fallback.
            if !plan.inprocess_groups.is_empty() && ctx.cfg.exec.auto_arrange_threshold > 0 {
                {
                    let threshold = ctx.cfg.exec.auto_arrange_threshold;
                    let arranged_count: usize = plan
                        .arranged_groups
                        .iter()
                        .flat_map(|g| g.iter())
                        .map(|g| g.items.len())
                        .sum();
                    // If there are inprocess groups but no arranged groups, this was a
                    // threshold fallback. Recalculate ratio for diagnostic.
                    if arranged_count == 0 && !plan.parallel_groups.is_empty() {
                        // This is a fallback-to-serial case from arrange threshold.
                        // The parallel_groups contain the collapsed groups.
                    }
                    let _ = threshold; // suppress unused
                }
            }
        }
        ExecutionStrategy::Parallel { worker_count } => {
            let force_parallel = matches!(
                ctx.cfg.exec.mode,
                config::ExecutionMode::Parallel {
                    workers: config::WorkerCount::Fixed(_)
                }
            );
            if force_parallel {
                eprintln!("scheduling: parallel (explicit --workers)");
            } else if let Some(est) = estimated {
                eprintln!(
                    "scheduling: parallel (est. {}ms > spawn overhead {}ms x {} workers)",
                    est.as_millis(),
                    ctx.cfg.exec.spawn_overhead.as_f64() as u64,
                    ctx.cfg.worker_count(),
                );
            } else {
                eprintln!(
                    "scheduling: parallel (cold cache, {} tests >= min_parallel_tests {})",
                    total_tests, ctx.cfg.exec.min_parallel_tests,
                );
            }

            if let Some(config::WorkerCount::Fixed(n)) = ctx.cfg.exec.mode.workers() {
                eprintln!("scheduling: {n} workers (explicit --workers {n})");
            } else if let Some(est) = estimated {
                eprintln!(
                    "scheduling: {worker_count} workers (est. {}ms / {}ms overhead, capped to {cpu_count} CPUs)",
                    est.as_millis(),
                    ctx.cfg.exec.spawn_overhead.as_f64() as u64,
                );
            } else {
                eprintln!(
                    "scheduling: {worker_count} workers (cold cache, using {cpu_count} CPUs)",
                );
            }

            // Auto-arrange diagnostics.
            if !plan.arranged_groups.is_empty() {
                let fixture_names: Vec<String> = shared_fixture_groups
                    .iter()
                    .flat_map(|g| g.iter().cloned())
                    .collect();
                let list = fixture_names.join(", ");
                let arranged_count: usize = plan
                    .arranged_groups
                    .iter()
                    .map(|g| g.iter().map(|g| g.items.len()).sum::<usize>())
                    .sum();
                eprintln!(
                    "scheduling: auto-arranged {arranged_count} tests by shared fixtures ({list})",
                );
            }
        }
    }
}

fn report_violations(
    violated_items: &[Arc<types::TestItem>],
    all_violations: &[strict::StrictViolation],
    rep: &mut dyn reporter::Reporter,
) {
    for item in violated_items {
        if let Some(pv) = all_violations.iter().find_map(|v| match v {
            strict::StrictViolation::PerTest(pv) if pv.node_id() == &item.node_id => Some(pv),
            _ => None,
        }) {
            let outcome = strict::per_test_error(pv);
            rep.test_started(item);
            rep.test_completed(item, &outcome, types::DurationMs::ZERO, None);
        }
    }
}

fn emit_shared_fixture_warning(
    py: Python<'_>,
    session: &bridge::FixtureSession,
    cfg: &config::Config,
    worker_count: usize,
) {
    if cfg.exec.auto_arrange_threshold > 0 {
        return;
    }
    let shared_names = session.shared_fixture_names(py);
    if shared_names.is_empty() {
        return;
    }
    let list = shared_names.join(", ");
    let noun = if shared_names.len() == 1 {
        "fixture"
    } else {
        "fixtures"
    };
    tracing::warn!(
        fixtures = %list,
        fixture_count = shared_names.len(),
        workers = worker_count,
        "shared {noun} will run once per worker; \
         session-scoped fixtures are not shared across parallel worker processes — \
         use --serial to run them once, or remove shared=True from fixtures \
         that can be function-scoped"
    );
}

/// Report violated items, group and schedule the clean items, decide
/// serial vs. parallel, dispatch, and return `(interrupted, timings)`.
///
/// `cache.invalidate()` and `cache.estimated_duration()` are called here
/// because they feed directly into the parallel-dispatch decision.
pub(super) fn execute(
    py: Python<'_>,
    clean_items: &[Arc<types::TestItem>],
    violated_items: Vec<Arc<types::TestItem>>,
    all_violations: Vec<strict::StrictViolation>,
    ctx: &ExecutionContext<'_>,
    rep: &mut dyn reporter::Reporter,
) -> parallel::PhaseResult {
    // Immediately report violated items as Error outcomes (no worker dispatch).
    report_violations(&violated_items, &all_violations, rep);

    let estimated = ctx
        .cache
        .estimated_duration(clean_items, ctx.ast_weight.map(|d| d.as_f64()));

    let mut groups = filter::group_by_module(clean_items);
    let failed_ids = ctx.cache.last_failed_ids();
    scheduler::apply_schedule_strategy(
        &mut groups,
        ctx.cfg.filter.schedule,
        ctx.cache,
        &failed_ids,
    );

    if ctx.cfg.output.verbosity >= config::Verbosity::Detailed {
        eprintln!("scheduling: strategy {:?}", ctx.cfg.filter.schedule);
    }

    let cpu_count = config::cpu_count();

    // Resolve shared fixture groups before plan (requires PyO3).
    let shared_fixture_groups = if ctx.cfg.exec.auto_arrange_threshold > 0 {
        ctx.session.shared_fixture_groups(py)
    } else {
        vec![]
    };

    // Build a pure execution plan — no I/O, no PyO3.
    let plan = arrange::plan_execution(
        groups,
        &ctx.cfg.exec.mode,
        ctx.cfg.worker_count(),
        ctx.cfg.exec.spawn_overhead.as_f64(),
        ctx.cfg.exec.min_parallel_tests,
        ctx.cfg.exec.auto_arrange_threshold,
        &shared_fixture_groups,
        estimated,
        cpu_count,
    );

    // ── Verbose scheduling diagnostics ─────────────────────────────────

    if ctx.cfg.output.verbosity >= config::Verbosity::Detailed {
        emit_scheduling_diagnostics(ctx, &plan, estimated, cpu_count, &shared_fixture_groups);
    }

    // ── Dispatch based on plan ─────────────────────────────────────────

    // Directories whose subtree must stay on one worker so a package-lifetime
    // fixture is built exactly once (#1710). Empty for every suite that does
    // not use the tier, in which case grouping is unchanged.
    let declaring_dirs = declaring_package_dirs(ctx.fixture_modules);
    if !declaring_dirs.is_empty() {
        // Counted across every phase's groups, so the number quoted is the whole
        // subtree rather than whichever slice this dispatch happens to hold.
        let all_groups: Vec<ModuleGroup> = plan
            .inprocess_groups
            .iter()
            .chain(plan.parallel_groups.iter())
            .chain(plan.arranged_groups.iter().flatten())
            .cloned()
            .collect();
        warn_about_package_collapse(ctx.fixture_modules, &all_groups, &declaring_dirs, rep);
    }

    // Run inprocess tests first (always serial, main process).
    let mut result = if plan.inprocess_groups.is_empty() {
        parallel::PhaseResult {
            interrupted: false,
            timings: Vec::new(),
        }
    } else {
        if ctx.cfg.output.verbosity >= config::Verbosity::Detailed {
            let inprocess_count: usize = plan.inprocess_groups.iter().map(|g| g.items.len()).sum();
            let parallel_count: usize = plan.parallel_groups.iter().map(|g| g.items.len()).sum();
            if inprocess_count > 0 {
                eprintln!(
                    "scheduling: {inprocess_count} inprocess tests (main), {parallel_count} parallel",
                );
            }
        }
        let mut mode = ExecutionDispatch::serial_from_ctx(py, ctx);
        mode.execute_groups(
            filter::group_by_package(plan.inprocess_groups, &declaring_dirs),
            rep,
        )
    };

    if result.interrupted {
        return result;
    }

    match plan.strategy {
        ExecutionStrategy::Serial => {
            // Serial path: run all remaining groups (arranged + parallel) serially.
            let mut all_groups = Vec::new();
            for group_modules in plan.arranged_groups {
                all_groups.extend(group_modules);
            }
            all_groups.extend(plan.parallel_groups);

            if all_groups.is_empty() {
                return result;
            }

            let mut mode = ExecutionDispatch::serial_from_ctx(py, ctx);
            let serial_result =
                mode.execute_groups(filter::group_by_package(all_groups, &declaring_dirs), rep);
            result.timings.extend(serial_result.timings);
            result.interrupted |= serial_result.interrupted;
            result
        }
        ExecutionStrategy::Parallel { worker_count } => {
            // Pre-warm workers NOW — decision is final.
            let mut pool_guard =
                parallel::PoolGuard::new(parallel::prewarm_workers(ctx.python_bin, worker_count));

            // Run arranged fixture groups serially on main process.
            if !plan.arranged_groups.is_empty() {
                // Auto-arrange disabled warning is not needed here because
                // arranged_groups is only populated when auto_arrange is active.
                let mut serial = ExecutionDispatch::serial_from_ctx(py, ctx);
                for group_modules in plan.arranged_groups {
                    if result.interrupted {
                        return result;
                    }
                    let phase = serial.execute_groups(
                        filter::group_by_package(group_modules, &declaring_dirs),
                        rep,
                    );
                    result.timings.extend(phase.timings);
                    result.interrupted |= phase.interrupted;
                }
            }

            // Emit shared-fixture warning when auto-arrange is disabled.
            emit_shared_fixture_warning(py, ctx.session, ctx.cfg, worker_count);

            if plan.parallel_groups.is_empty() || result.interrupted {
                return result;
            }

            let mut parallel = ExecutionDispatch::Parallel {
                cfg: ctx.cfg,
                workers: worker_count,
                session_inputs: parallel::SessionInputs {
                    conftest_paths: ctx.conftest_files,
                    fixture_modules: ctx.fixture_modules,
                },
                python_bin: ctx.python_bin,
                pool: Some(pool_guard.take()),
            };
            let parallel_result = parallel.execute_groups(
                filter::group_by_package(plan.parallel_groups, &declaring_dirs),
                rep,
            );

            result.timings.extend(parallel_result.timings);
            result.interrupted |= parallel_result.interrupted;
            result
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache::TestCache;
    use crate::types::TestItem;

    fn make_item(node_id: &str) -> std::sync::Arc<TestItem> {
        TestItem::builder_raw(node_id).arc()
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
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
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
