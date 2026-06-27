//! Subprocess worker pool for parallel test execution.
//!
//! Spawns `python -m oxitest._bridge.worker` subprocesses, one per worker slot.
//! Each worker is persistent within a run — it receives JSON tasks over stdin
//! (one module group per task) and streams JSON result lines back over stdout.
//!
//! Includes a per-result watchdog timeout: if a worker stalls, it is killed and
//! remaining tests are marked as timed out. Worker crashes produce sentinel
//! error results so the pipeline never hangs.

mod drain;
mod pool;

pub(crate) use drain::{DrainContext, DrainOutcome, drain_worker_results, handle_drain_outcome};
pub(crate) use pool::{PoolGuard, PrewarmedWorker, kill_pool, prewarm_workers};

use crate::{
    config, reporter, scheduler, types,
    worker_session::{WorkerParams, spawn_worker, spawn_worker_with_process},
};

use drain::{drain_remaining_into_crashed, handle_worker_result};

/// Channel item carrying the result of one test execution from a worker thread.
pub(crate) struct WorkerResult {
    pub resolved: types::ResolvedOutcome,
    pub worker_id: usize,
}

/// Result of a test execution phase (serial or parallel).
pub(crate) struct PhaseResult {
    /// Whether execution was interrupted (e.g., by maxfail).
    pub interrupted: bool,
    /// Per-test timing data.
    pub timings: Vec<types::TestTiming>,
}

pub(crate) fn run_phase_parallel(
    groups: Vec<scheduler::ModuleGroup>,
    cfg: &config::Config,
    worker_count: usize, // caller computes optimal count
    conftest_paths: &[camino::Utf8PathBuf],
    python_bin: &str,
    rep: &mut dyn reporter::Reporter,
    pool: Option<Vec<PrewarmedWorker>>,
) -> PhaseResult {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    let worker_count = worker_count.max(1).min(groups.len().max(1));
    let total: usize = groups.iter().map(|g| g.items.len()).sum();
    // Build node_id → Arc<TestItem> before groups are consumed by the scheduler.
    // Items are already Arc-wrapped from collection — no deep clone needed.
    let item_lookup: ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>> = groups
        .iter()
        .flat_map(|g| g.items.iter())
        .map(|item| (item.node_id.clone(), Arc::clone(item)))
        .collect();
    let in_flight: std::sync::Arc<parking_lot::Mutex<ahash::AHashSet<String>>> =
        std::sync::Arc::new(parking_lot::Mutex::new(ahash::AHashSet::new()));
    let sched = Arc::new(scheduler::Scheduler::new(groups));
    let cancelled = Arc::new(AtomicBool::new(false));
    let conftest_raw: std::sync::Arc<serde_json::value::RawValue> = {
        let paths: Vec<&str> = conftest_paths.iter().map(|p| p.as_str()).collect();
        let json_str = serde_json::to_string(&paths).expect("conftest paths serialize");
        std::sync::Arc::from(
            serde_json::value::RawValue::from_string(json_str).expect("valid JSON"),
        )
    };
    let timeout_secs = cfg.exec.timeout_secs;
    let keep_tmp: Option<Arc<str>> = cfg.output.keep_tmp.as_ref().map(|m| Arc::from(m.as_str()));
    let show_locals = cfg.output.show_locals;
    let show_internals = cfg.output.show_internals;
    let python_bin: Arc<str> = Arc::from(python_bin);

    let (tx, rx) = crossbeam_channel::unbounded::<WorkerResult>();

    // Use pre-warmed workers first, fall back to spawning fresh ones.
    let mut prewarmed = pool.unwrap_or_default();
    let handles: Vec<_> = (0..worker_count)
        .map(|i| {
            let worker_params = WorkerParams {
                worker_id: i,
                sched: Arc::clone(&sched),
                cancelled: Arc::clone(&cancelled),
                conftest_json: std::sync::Arc::clone(&conftest_raw),
                timeout_secs,
                keep_tmp: keep_tmp.clone(),
                show_locals,
                show_internals,
                tx: tx.clone(),
                in_flight: std::sync::Arc::clone(&in_flight),
            };
            if let Some(pw) = prewarmed.pop() {
                spawn_worker_with_process(pw, worker_params)
            } else {
                spawn_worker(Arc::clone(&python_bin), worker_params)
            }
        })
        .collect();

    // Kill any excess pre-warmed workers that weren't needed.
    if !prewarmed.is_empty() {
        kill_pool(prewarmed);
    }

    drop(tx);

    let mut acc = types::FailureAccumulator::new(cfg.exec.maxfail);
    let mut interrupted = false;
    let mut timings: Vec<types::TestTiming> = Vec::with_capacity(total);

    for result in rx {
        let WorkerResult {
            resolved,
            worker_id,
        } = result;
        // Snapshot concurrent tests (excluding the one that just completed)
        let node_id = resolved.node_id.clone(); // Arc refcount bump — cheap
        let concurrent_tests: Vec<String> = {
            let mut set = in_flight.lock();
            set.remove(node_id.as_ref());
            if set.is_empty() {
                Vec::new()
            } else {
                set.iter().cloned().collect()
            }
        };

        let parallel_ctx = crate::parallel_context::ParallelContext {
            worker_id: worker_id + 1, // 1-indexed for display
            concurrent_tests,
        };

        let Some(outcome) = handle_worker_result(
            resolved,
            &item_lookup,
            rep,
            &mut timings,
            Some(&parallel_ctx),
        ) else {
            continue;
        };
        if acc.record(&outcome) {
            interrupted = true;
            break;
        }
    }

    // Drain any groups the scheduler still holds after all workers have exited.
    // This surfaces tests that were never assigned because every worker crashed
    // before popping them.  Safe here: the rx loop above only completes once all
    // worker threads have dropped their `tx` clone (i.e., returned).
    drain_remaining_into_crashed(&sched, &item_lookup, rep, &mut timings);

    cancelled.store(true, Ordering::Relaxed);
    for h in handles {
        let _ = h.join();
    }

    PhaseResult {
        interrupted,
        timings,
    }
}

#[cfg(test)]
mod worker_count_tests {
    use crate::config::{self, ExecutionMode, WorkerCount};
    use crate::worker_result::WireResult;
    use std::time::Duration;

    #[test]
    fn explicit_workers_bypasses_auto_scale() {
        // Fixed(8) is returned directly — estimated duration is ignored.
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Fixed(8),
        };
        let count =
            config::compute_optimal_workers(&mode, 16, Some(Duration::from_millis(100)), 250.0);
        assert_eq!(count, 8);
    }

    #[test]
    fn serial_returns_1() {
        // Serial always returns 1, regardless of other params.
        let count = config::compute_optimal_workers(
            &ExecutionMode::Serial,
            8,
            Some(Duration::from_millis(5000)),
            250.0,
        );
        assert_eq!(count, 1);
    }

    #[test]
    fn auto_scale_caps_to_needed_workers() {
        let est_ms: f64 = 500.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 8;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)) = min(8, 2) = 2
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize);
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(
            &mode,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn auto_scale_caps_to_cpu_count() {
        let est_ms: f64 = 10_000.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 4;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)) = min(4, 40) = 4
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize);
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(
            &mode,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn cold_cache_returns_cpu_count() {
        // No timing estimate (cold cache) → fall back to cpu_count.
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(&mode, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn short_suite_uses_one_worker() {
        let est_ms: f64 = 200.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 8;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)).max(1) = min(8, 1).max(1) = 1
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize).max(1);
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(
            &mode,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn serial_overrides_explicit_workers() {
        // Serial mode always returns 1.
        let count = config::compute_optimal_workers(&ExecutionMode::Serial, 8, None, 250.0);
        assert_eq!(count, 1);
    }

    #[test]
    fn auto_warm_cache_scales_to_needed() {
        let est_ms: f64 = 500.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 8;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)) = min(8, 2) = 2
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize);
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(
            &mode,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn auto_warm_cache_caps_to_cpu() {
        let est_ms: f64 = 10_000.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 4;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)) = min(4, 40) = 4
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize);
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(
            &mode,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn auto_cold_cache_returns_cpu_count() {
        // WorkerCount::Auto + cold cache (None) → fall back to cpu_count.
        let mode = ExecutionMode::Parallel {
            workers: WorkerCount::Auto,
        };
        let count = config::compute_optimal_workers(&mode, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn worker_result_populates_failed_diagnostic_fields() {
        // Construct WireResult as if deserialized from worker JSON
        let json = r#"{
            "node_id": "test_mod::test_fn",
            "outcome": "failed",
            "duration_ms": 42.0,
            "message": "assert x == y",
            "file": "test_mod.py",
            "lineno": 10,
            "source_line": "assert x == y",
            "left": "1",
            "right": "2",
            "op": "=="
        }"#;
        let result: WireResult = serde_json::from_str(json).unwrap();
        let resolved = result.into_outcome();

        match resolved.outcome {
            crate::types::TestOutcome::Failed(d) => {
                assert_eq!(d.file, "test_mod.py");
                assert_eq!(d.lineno, crate::types::LineNo::new(10));
                assert_eq!(d.source_line, "assert x == y");
                let cmp = d.comparison.as_ref().expect("expected comparison");
                assert_eq!(cmp.left, "1");
                assert_eq!(cmp.right, "2");
                assert_eq!(cmp.op, "==");
            }
            other => panic!("Expected Failed, got {:?}", other),
        }
    }

    #[test]
    fn worker_result_populates_error_diagnostic_fields() {
        let json = r#"{
            "node_id": "t::e",
            "outcome": "error",
            "duration_ms": 1.0,
            "file": "t.py",
            "lineno": 5,
            "source_line": "import bad"
        }"#;
        let result: WireResult = serde_json::from_str(json).unwrap();
        let resolved = result.into_outcome();
        match resolved.outcome {
            crate::types::TestOutcome::Error(d) => {
                assert_eq!(d.file, "t.py");
                assert_eq!(d.lineno, crate::types::LineNo::new(5));
                assert_eq!(d.source_line, "import bad");
            }
            other => panic!("Expected Error, got {:?}", other),
        }
    }

    fn make_wire_result(status: &str) -> crate::types::ResolvedOutcome {
        let wire: WireResult = serde_json::from_str(&format!(
            r#"{{"node_id":"t::f","outcome":"{status}","duration_ms":1.0,"message":"reason"}}"#
        ))
        .expect("test JSON must be valid");
        wire.into_outcome()
    }

    #[test]
    fn worker_result_to_outcome_maps_all_known_status_strings() {
        use crate::types::TestOutcome;

        assert!(matches!(
            make_wire_result("passed").outcome,
            TestOutcome::Passed { .. }
        ));
        assert!(matches!(
            make_wire_result("failed").outcome,
            TestOutcome::Failed(..)
        ));
        assert!(matches!(
            make_wire_result("error").outcome,
            TestOutcome::Error(..)
        ));
        assert!(matches!(
            make_wire_result("skipped").outcome,
            TestOutcome::Skipped { .. }
        ));
        assert!(matches!(
            make_wire_result("xfailed").outcome,
            TestOutcome::XFailed { .. }
        ));
        assert!(matches!(
            make_wire_result("xpassed").outcome,
            TestOutcome::XPassed { .. }
        ));
        assert!(matches!(
            make_wire_result("warned").outcome,
            TestOutcome::Warned { .. }
        ));
        assert!(matches!(
            make_wire_result("timeout").outcome,
            TestOutcome::Timeout { .. }
        ));
    }

    #[test]
    fn unknown_status_is_deser_error() {
        // With internally-tagged enum, unknown outcome values fail deserialization.
        let json = r#"{"node_id":"t::f","outcome":"flaky","duration_ms":1.0}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn timed_out_sentinel_has_error_outcome_and_preserves_duration() {
        use crate::types::TestOutcome;
        let (outcome, dur) = TestOutcome::timed_out_sentinel(std::time::Duration::from_secs(30));
        assert!(
            dur.as_f64() > 0.0,
            "timed_out must produce a positive duration"
        );
        match outcome {
            TestOutcome::Error(d) => {
                assert!(
                    d.message.contains("30"),
                    "message should mention the watchdog duration"
                );
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn crashed_sentinel_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let outcome = TestOutcome::crashed_sentinel();
        match outcome {
            TestOutcome::Error(d) => {
                assert!(
                    d.message.contains("unexpectedly"),
                    "crashed result must include a message explaining the failure"
                );
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn timed_out_sentinel_is_error_outcome() {
        use crate::types::TestOutcome;
        let (outcome, _dur) = TestOutcome::timed_out_sentinel(std::time::Duration::from_secs(60));
        assert!(
            matches!(outcome, TestOutcome::Error(..)),
            "timed_out_sentinel must produce TestOutcome::Error"
        );
    }

    #[test]
    fn crashed_sentinel_is_error_outcome() {
        use crate::types::TestOutcome;
        let outcome = TestOutcome::crashed_sentinel();
        assert!(
            matches!(outcome, TestOutcome::Error(..)),
            "crashed_sentinel must produce TestOutcome::Error"
        );
    }

    #[test]
    fn worker_result_no_message_lines_filters_negatives_and_zero() {
        use crate::types::TestOutcome;

        let wr: WireResult = serde_json::from_str(
            r#"{"node_id":"t::f","outcome":"passed","duration_ms":1.0,"no_message_lines":[-1,0,5,10]}"#,
        )
        .expect("test JSON must be valid");

        let resolved = wr.into_outcome();
        match resolved.outcome {
            TestOutcome::Passed { tips } => {
                assert_eq!(
                    tips.as_deref(),
                    Some([5usize, 10].as_slice()),
                    "negative and zero values in no_message_lines must be filtered before usize cast"
                );
            }
            other => panic!("expected Passed outcome, got {other:?}"),
        }
    }
}
