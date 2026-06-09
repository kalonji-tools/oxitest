//! Test execution: serial/parallel dispatch, harnesses, and auto-arrangement.

use std::sync::Arc;

use camino::Utf8PathBuf;
use pyo3::prelude::*;

use super::arrange::{
    evaluate_arrange_threshold, partition_by_fixture_groups, partition_inprocess_groups,
    ArrangeDecision,
};
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

#[allow(clippy::too_many_arguments)]
pub(crate) fn run_timed(
    py: Python<'_>,
    item: &types::TestItem,
    session: &bridge::FixtureSession,
    timeout: Option<u64>,
    debug_mode: Option<&str>,
    keep_tmp: Option<&str>,
    show_locals: bool,
    show_internals: bool,
) -> (types::TestOutcome, types::DurationMs) {
    let start = std::time::Instant::now();
    let outcome = bridge::run_test_with_session_obj(
        py,
        item,
        session.as_py_object(py),
        timeout,
        debug_mode,
        keep_tmp,
        show_locals,
        show_internals,
    );
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
    pub debug_mode: Option<&'a str>,
    pub keep_tmp: Option<&'a str>,
    pub show_locals: bool,
    pub show_internals: bool,
}

impl<'a> SerialHarness<'a> {
    fn from_ctx(py: Python<'a>, ctx: &'a ExecutionContext<'a>) -> Self {
        Self {
            py,
            session: ctx.session,
            cache: ctx.cache,
            timeout_secs: ctx.cfg.timeout_secs,
            timeout_multiplier: ctx.cfg.timeout_multiplier,
            maxfail: ctx.cfg.maxfail,
            debug_mode: ctx.cfg.debug.as_ref().map(|m| m.as_str()),
            keep_tmp: ctx.cfg.keep_tmp.as_ref().map(|m| m.as_str()),
            show_locals: ctx.cfg.show_locals,
            show_internals: ctx.cfg.show_internals,
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
                let (outcome, duration_ms) = run_timed(
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
        )
    }
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
    for item in &violated_items {
        // Per-test items may have multiple violations; we report only the first to keep
        // the error message focused. Users address violations one at a time.
        if let Some(pv) = all_violations.iter().find_map(|v| match v {
            strict::StrictViolation::PerTest(pv) if pv.node_id() == &item.node_id => Some(pv),
            _ => None,
        }) {
            let outcome = strict::per_test_error(pv);
            rep.test_started(item);
            rep.test_completed(item, &outcome, types::DurationMs::ZERO, None);
        }
    }

    let estimated = ctx.cache.estimated_duration(clean_items, ctx.ast_weight_ms);

    let mut groups = filter::group_by_module(clean_items);
    let failed_ids = ctx.cache.last_failed_ids();
    scheduler::apply_schedule_strategy(&mut groups, ctx.cfg.schedule, ctx.cache, &failed_ids);

    if ctx.cfg.verbosity >= config::Verbosity::Detailed {
        eprintln!("scheduling: strategy {:?}", ctx.cfg.schedule);
    }

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

    if ctx.cfg.verbosity >= config::Verbosity::Detailed {
        if ctx.cfg.serial {
            eprintln!("scheduling: serial (--serial flag)");
        } else if !use_parallel {
            if let Some(est) = estimated {
                eprintln!(
                    "scheduling: serial (est. {}ms <= spawn overhead {}ms x {} workers)",
                    est.as_millis(),
                    ctx.cfg.spawn_overhead_ms as u64,
                    ctx.cfg.worker_count(),
                );
            } else {
                eprintln!(
                    "scheduling: serial (cold cache, {} tests < min_parallel_tests {})",
                    total_tests, ctx.cfg.min_parallel_tests,
                );
            }
        } else if force_parallel {
            eprintln!("scheduling: parallel (explicit --workers)");
        } else if let Some(est) = estimated {
            eprintln!(
                "scheduling: parallel (est. {}ms > spawn overhead {}ms x {} workers)",
                est.as_millis(),
                ctx.cfg.spawn_overhead_ms as u64,
                ctx.cfg.worker_count(),
            );
        } else {
            eprintln!(
                "scheduling: parallel (cold cache, {} tests >= min_parallel_tests {})",
                total_tests, ctx.cfg.min_parallel_tests,
            );
        }
    }

    if use_parallel {
        debug_assert!(
            !ctx.cfg.serial,
            "compute_optimal_workers is unreachable in serial mode"
        );

        // Partition inprocess-marked tests: run on main process before parallel dispatch.
        let (inprocess_groups, parallel_groups) = partition_inprocess_groups(groups);

        if ctx.cfg.verbosity >= config::Verbosity::Detailed {
            let inprocess_count: usize =
                inprocess_groups.iter().map(|(_, items)| items.len()).sum();
            let parallel_count: usize = parallel_groups.iter().map(|(_, items)| items.len()).sum();
            if inprocess_count > 0 {
                eprintln!(
                    "scheduling: {inprocess_count} inprocess tests (main), {parallel_count} parallel",
                );
            }
        }
        let mut inprocess_result = if inprocess_groups.is_empty() {
            parallel::PhaseResult {
                interrupted: false,
                timings: Vec::new(),
            }
        } else {
            let harness = SerialHarness::from_ctx(py, ctx);
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

        if ctx.cfg.verbosity >= config::Verbosity::Detailed {
            if let Some(config::WorkerCount::Fixed(n)) = ctx.cfg.workers {
                eprintln!("scheduling: {n} workers (explicit --workers {n})");
            } else if let Some(est) = estimated {
                eprintln!(
                    "scheduling: {optimal_worker_count} workers (est. {}ms / {}ms overhead, capped to {cpu_count} CPUs)",
                    est.as_millis(),
                    ctx.cfg.spawn_overhead_ms as u64,
                );
            } else {
                eprintln!("scheduling: {optimal_worker_count} workers (cold cache, using {cpu_count} CPUs)");
            }
        }

        // Auto-arrange: group tests by shared fixture dependencies.
        let (parallel_groups, mut inprocess_result) = if let Some(threshold) =
            ctx.cfg.auto_arrange_threshold
        {
            let fixture_groups = ctx.session.shared_fixture_groups(py);
            if fixture_groups.is_empty() {
                (parallel_groups, inprocess_result)
            } else {
                let (arranged, remaining) =
                    partition_by_fixture_groups(parallel_groups, &fixture_groups);

                let decision = evaluate_arrange_threshold(&arranged, &remaining, threshold);

                if let ArrangeDecision::FallbackSerial { ratio } = decision {
                    // Threshold exceeded — fall back to serial.
                    if ctx.cfg.verbosity >= config::Verbosity::Detailed {
                        eprintln!(
                            "scheduling: auto-arrange fallback to serial \
                                 (largest fixture group {ratio}% > threshold {threshold}%)",
                        );
                    }
                    let mut all_groups: Vec<(Utf8PathBuf, Vec<Arc<types::TestItem>>)> = Vec::new();
                    for group_modules in arranged {
                        all_groups.extend(group_modules);
                    }
                    all_groups.extend(remaining);

                    let harness = SerialHarness::from_ctx(py, ctx);
                    let serial_result = harness.execute_groups(all_groups, rep);
                    inprocess_result.timings.extend(serial_result.timings);
                    inprocess_result.interrupted |= serial_result.interrupted;
                    return inprocess_result;
                }

                // Run each arranged fixture group serially on main process.
                let fixture_names: Vec<String> = fixture_groups
                    .iter()
                    .flat_map(|g| g.iter().cloned())
                    .collect();
                let list = fixture_names.join(", ");
                let arranged_count: usize = arranged
                    .iter()
                    .map(|g| g.iter().map(|(_, items)| items.len()).sum::<usize>())
                    .sum();
                if ctx.cfg.verbosity >= config::Verbosity::Detailed {
                    eprintln!(
                            "scheduling: auto-arranged {arranged_count} tests by shared fixtures ({list})",
                        );
                }

                let harness = SerialHarness::from_ctx(py, ctx);
                for group_modules in arranged {
                    if inprocess_result.interrupted {
                        return inprocess_result;
                    }
                    let result = harness.execute_groups(group_modules, rep);
                    inprocess_result.timings.extend(result.timings);
                    inprocess_result.interrupted |= result.interrupted;
                }

                (remaining, inprocess_result)
            }
        } else {
            // Auto-arrange disabled — emit the existing warning.
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
            (parallel_groups, inprocess_result)
        };

        if parallel_groups.is_empty() {
            return inprocess_result;
        }

        let harness = ParallelHarness {
            cfg: ctx.cfg,
            workers: optimal_worker_count,
            conftest_files: ctx.conftest_files,
            python_bin: ctx.python_bin,
        };
        let parallel_result = harness.execute_groups(parallel_groups, rep);

        inprocess_result.timings.extend(parallel_result.timings);
        inprocess_result.interrupted |= parallel_result.interrupted;
        inprocess_result
    } else {
        let harness = SerialHarness::from_ctx(py, ctx);
        harness.execute_groups(groups, rep)
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
