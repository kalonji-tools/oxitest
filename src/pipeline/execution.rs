//! Test execution: serial/parallel dispatch, harnesses, and auto-arrangement.

use std::sync::Arc;

use camino::Utf8PathBuf;
use pyo3::prelude::*;

use super::arrange::{self, ExecutionStrategy};
use super::traits::ExecutionHarness;
use crate::cache::{OutcomeCache, TimingCache};
use crate::{bridge, cache, config, filter, parallel, reporter, scheduler, strict, types};

pub(super) struct ExecutionContext<'a> {
    pub(super) cfg: &'a config::Config,
    pub(super) cache: &'a cache::TestCache,
    pub(super) session: &'a bridge::FixtureSession,
    pub(super) conftest_files: &'a [Utf8PathBuf],
    pub(super) python_bin: &'a str,
    /// Sum of AST-derived body weights from prescan; used as fallback for cold-cache estimation.
    pub(super) ast_weight_ms: Option<f64>,
}

/// Debug and display options passed through the execution pipeline.
#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct DebugOptions<'a> {
    pub debug_mode: Option<&'a str>,
    pub keep_tmp: Option<&'a str>,
    pub show_locals: bool,
    pub show_internals: bool,
}

fn resolve_timeout(
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

/// Serial execution harness — runs tests in-process, one at a time.
pub(super) struct SerialHarness<'a> {
    pub py: Python<'a>,
    pub session: &'a bridge::FixtureSession,
    pub cache: &'a dyn cache::TimingCache,
    pub timeout_secs: Option<u64>,
    pub timeout_multiplier: Option<f64>,
    pub maxfail: usize,
    pub opts: DebugOptions<'a>,
}

impl<'a> SerialHarness<'a> {
    fn from_ctx(py: Python<'a>, ctx: &'a ExecutionContext<'a>) -> Self {
        Self {
            py,
            session: ctx.session,
            cache: ctx.cache,
            timeout_secs: ctx.cfg.exec.timeout_secs,
            timeout_multiplier: ctx.cfg.exec.timeout_multiplier,
            maxfail: ctx.cfg.exec.maxfail,
            opts: DebugOptions {
                debug_mode: ctx.cfg.exec.mode.debug_mode().map(|m| m.as_str()),
                keep_tmp: ctx.cfg.output.keep_tmp.as_ref().map(|m| m.as_str()),
                show_locals: ctx.cfg.output.show_locals,
                show_internals: ctx.cfg.output.show_internals,
            },
        }
    }
}

impl ExecutionHarness for SerialHarness<'_> {
    fn execute_groups(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
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
                let (outcome, duration_ms) =
                    run_timed(self.py, item, self.session, timeout, self.opts);
                timings.push(types::TestTiming {
                    node_id: item.node_id.clone(),
                    duration_ms,
                    outcome: types::OutcomeKind::from(&outcome),
                });
                rep.test_completed(item, &outcome, duration_ms, None);
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
    pub cfg: &'a config::Config,
    pub workers: usize,
    pub conftest_files: &'a [Utf8PathBuf],
    pub python_bin: &'a str,
    /// Pre-warmed worker pool; wrapped in `RefCell` so `execute_groups(&self)`
    /// can move it out without requiring `&mut self`.
    pub pool: std::cell::RefCell<Option<Vec<parallel::PrewarmedWorker>>>,
}

impl ExecutionHarness for ParallelHarness<'_> {
    fn execute_groups(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
        rep: &mut dyn reporter::Reporter,
    ) -> parallel::PhaseResult {
        parallel::run_phase_parallel(
            groups,
            self.cfg,
            self.workers,
            self.conftest_files,
            self.python_bin,
            rep,
            self.pool.borrow_mut().take(),
        )
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
        .map(|(_, items)| items.len())
        .sum::<usize>()
        + plan
            .arranged_groups
            .iter()
            .flat_map(|g| g.iter())
            .map(|(_, items)| items.len())
            .sum::<usize>();

    match &plan.strategy {
        ExecutionStrategy::Serial => {
            if ctx.cfg.exec.mode.is_serial() {
                eprintln!("scheduling: serial (--serial flag)");
            } else if let Some(est) = estimated {
                eprintln!(
                    "scheduling: serial (est. {}ms <= spawn overhead {}ms x {} workers)",
                    est.as_millis(),
                    ctx.cfg.exec.spawn_overhead_ms as u64,
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
                        .map(|(_, items)| items.len())
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
                    ctx.cfg.exec.spawn_overhead_ms as u64,
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
                    ctx.cfg.exec.spawn_overhead_ms as u64,
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
                    .map(|g| g.iter().map(|(_, items)| items.len()).sum::<usize>())
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

    let estimated = ctx.cache.estimated_duration(clean_items, ctx.ast_weight_ms);

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
        ctx.cfg.exec.spawn_overhead_ms,
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

    // Run inprocess tests first (always serial, main process).
    let mut result = if plan.inprocess_groups.is_empty() {
        parallel::PhaseResult {
            interrupted: false,
            timings: Vec::new(),
        }
    } else {
        if ctx.cfg.output.verbosity >= config::Verbosity::Detailed {
            let inprocess_count: usize = plan
                .inprocess_groups
                .iter()
                .map(|(_, items)| items.len())
                .sum();
            let parallel_count: usize = plan
                .parallel_groups
                .iter()
                .map(|(_, items)| items.len())
                .sum();
            if inprocess_count > 0 {
                eprintln!(
                    "scheduling: {inprocess_count} inprocess tests (main), {parallel_count} parallel",
                );
            }
        }
        let harness = SerialHarness::from_ctx(py, ctx);
        harness.execute_groups(plan.inprocess_groups, rep)
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

            let harness = SerialHarness::from_ctx(py, ctx);
            let serial_result = harness.execute_groups(all_groups, rep);
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
                let harness = SerialHarness::from_ctx(py, ctx);
                for group_modules in plan.arranged_groups {
                    if result.interrupted {
                        return result;
                    }
                    let phase = harness.execute_groups(group_modules, rep);
                    result.timings.extend(phase.timings);
                    result.interrupted |= phase.interrupted;
                }
            }

            // Emit shared-fixture warning when auto-arrange is disabled.
            emit_shared_fixture_warning(py, ctx.session, ctx.cfg, worker_count);

            if plan.parallel_groups.is_empty() || result.interrupted {
                return result;
            }

            let harness = ParallelHarness {
                cfg: ctx.cfg,
                workers: worker_count,
                conftest_files: ctx.conftest_files,
                python_bin: ctx.python_bin,
                pool: std::cell::RefCell::new(Some(pool_guard.take())),
            };
            let parallel_result = harness.execute_groups(plan.parallel_groups, rep);

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
