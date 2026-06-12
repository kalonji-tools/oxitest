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

pub(crate) use drain::{drain_worker_results, handle_drain_outcome, DrainContext, DrainOutcome};
pub(crate) use pool::{kill_pool, prewarm_workers, PoolGuard, PrewarmedWorker};

use crate::{
    config, reporter, scheduler, types,
    worker_result::WorkerOutcome,
    worker_session::{spawn_worker, spawn_worker_with_process, WorkerParams},
};

use drain::{drain_remaining_into_crashed, handle_worker_result};

/// Channel item carrying the result of one test execution from a worker thread.
pub(crate) struct WorkerResult {
    pub node_id: String,
    pub duration_ms: f64,
    pub outcome: WorkerOutcome,
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
    groups: Vec<(camino::Utf8PathBuf, Vec<std::sync::Arc<types::TestItem>>)>,
    cfg: &config::Config,
    worker_count: usize, // caller computes optimal count
    conftest_paths: &[camino::Utf8PathBuf],
    python_bin: &str,
    rep: &mut dyn reporter::Reporter,
    pool: Option<Vec<PrewarmedWorker>>,
) -> PhaseResult {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    let worker_count = worker_count.max(1).min(groups.len().max(1));
    let total: usize = groups.iter().map(|(_, items)| items.len()).sum();
    // Build node_id → Arc<TestItem> before groups are consumed by the scheduler.
    // Items are already Arc-wrapped from collection — no deep clone needed.
    let item_lookup: ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>> = groups
        .iter()
        .flat_map(|(_, items)| items.iter())
        .map(|item| (item.node_id.clone(), Arc::clone(item)))
        .collect();
    let in_flight: std::sync::Arc<std::sync::Mutex<ahash::AHashSet<String>>> =
        std::sync::Arc::new(std::sync::Mutex::new(ahash::AHashSet::new()));
    let sched = Arc::new(scheduler::Scheduler::new(groups));
    let cancelled = Arc::new(AtomicBool::new(false));
    let conftest_raw: std::sync::Arc<serde_json::value::RawValue> = {
        let paths: Vec<&str> = conftest_paths.iter().map(|p| p.as_str()).collect();
        let json_str = serde_json::to_string(&paths).expect("conftest paths serialize");
        std::sync::Arc::from(
            serde_json::value::RawValue::from_string(json_str).expect("valid JSON"),
        )
    };
    let timeout_secs = cfg.timeout_secs;
    let keep_tmp: Option<Arc<str>> = cfg.keep_tmp.as_ref().map(|m| Arc::from(m.as_str()));
    let show_locals = cfg.show_locals;
    let show_internals = cfg.show_internals;
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

    let mut acc = types::FailureAccumulator::new(cfg.maxfail);
    let mut interrupted = false;
    let mut timings: Vec<types::TestTiming> = Vec::with_capacity(total);

    for result in rx {
        let WorkerResult {
            node_id: node_id_str,
            duration_ms,
            outcome: worker_outcome,
            worker_id,
        } = result;
        // Snapshot concurrent tests (excluding the one that just completed)
        let concurrent_tests: Vec<String> = {
            let mut set = in_flight.lock().unwrap();
            set.remove(&node_id_str);
            set.iter().cloned().collect()
        };

        let parallel_ctx = crate::parallel_context::ParallelContext {
            worker_id: worker_id + 1, // 1-indexed for display
            concurrent_tests,
        };

        let Some(outcome) = handle_worker_result(
            &node_id_str,
            duration_ms,
            worker_outcome,
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
    use crate::config::{self, WorkerCount};
    use crate::worker_result::{WireResult, WorkerOutcome};
    use std::time::Duration;

    #[test]
    fn explicit_workers_bypasses_auto_scale() {
        // Fixed(8) is returned directly — estimated duration is ignored.
        let count = config::compute_optimal_workers(
            Some(WorkerCount::Fixed(8)),
            false,
            16,
            Some(Duration::from_millis(100)),
            250.0,
        );
        assert_eq!(count, 8);
    }

    #[test]
    fn serial_returns_1() {
        // serial=true always returns 1, regardless of other params.
        let count = config::compute_optimal_workers(
            None,
            true,
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
        let count = config::compute_optimal_workers(
            None,
            false,
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
        let count = config::compute_optimal_workers(
            None,
            false,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn cold_cache_returns_cpu_count() {
        // No timing estimate (cold cache) → fall back to cpu_count.
        let count = config::compute_optimal_workers(None, false, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn short_suite_uses_one_worker() {
        let est_ms: f64 = 200.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 8;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)).max(1) = min(8, 1).max(1) = 1
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize).max(1);
        let count = config::compute_optimal_workers(
            None,
            false,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn serial_overrides_explicit_workers() {
        // serial=true takes priority even when workers is explicitly Fixed(4).
        let count =
            config::compute_optimal_workers(Some(WorkerCount::Fixed(4)), true, 8, None, 250.0);
        assert_eq!(count, 1);
    }

    #[test]
    fn auto_warm_cache_scales_to_needed() {
        let est_ms: f64 = 500.0;
        let overhead_ms: f64 = 250.0;
        let cpu_count = 8;
        // Formula: min(cpu_count, ceil(est_ms / overhead_ms)) = min(8, 2) = 2
        let expected = cpu_count.min((est_ms / overhead_ms).ceil() as usize);
        let count = config::compute_optimal_workers(
            Some(WorkerCount::Auto),
            false,
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
        let count = config::compute_optimal_workers(
            Some(WorkerCount::Auto),
            false,
            cpu_count,
            Some(Duration::from_millis(est_ms as u64)),
            overhead_ms,
        );
        assert_eq!(count, expected);
    }

    #[test]
    fn auto_cold_cache_returns_cpu_count() {
        // WorkerCount::Auto + cold cache (None) → fall back to cpu_count.
        let count = config::compute_optimal_workers(Some(WorkerCount::Auto), false, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn worker_result_populates_failed_diagnostic_fields() {
        // Construct WireResult as if deserialized from worker JSON
        let json = r#"{
            "node_id": "test_mod::test_fn",
            "outcome": "failed",
            "duration_ms": 42.0,
            "failure_repr": null,
            "message": null,
            "file": "test_mod.py",
            "lineno": 10,
            "source_line": "assert x == y",
            "no_message_lines": [],
            "left": "1",
            "right": "2",
            "op": "==",
            "strict": false
        }"#;
        let result: WireResult = serde_json::from_str(json).unwrap();
        let (_, _, worker_outcome) = result.into_worker_outcome();
        let outcome = crate::types::TestOutcome::from(worker_outcome);

        match outcome {
            crate::types::TestOutcome::Failed(d) => {
                assert_eq!(d.file, "test_mod.py");
                assert_eq!(d.lineno, crate::types::LineNo::new(10));
                assert_eq!(d.source_line, "assert x == y");
                assert_eq!(d.left, "1");
                assert_eq!(d.right, "2");
                assert_eq!(d.op, "==");
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
        let (_, _, worker_outcome) = result.into_worker_outcome();
        match crate::types::TestOutcome::from(worker_outcome) {
            crate::types::TestOutcome::Error(d) => {
                assert_eq!(d.file, "t.py");
                assert_eq!(d.lineno, crate::types::LineNo::new(5));
                assert_eq!(d.source_line, "import bad");
            }
            other => panic!("Expected Error, got {:?}", other),
        }
    }

    fn make_wire_result(status: &str) -> (String, f64, WorkerOutcome) {
        let wire: WireResult = serde_json::from_str(&format!(
            r#"{{"node_id":"t::f","outcome":"{status}","duration_ms":1.0,"failure_repr":"reason"}}"#
        ))
        .expect("test JSON must be valid");
        wire.into_worker_outcome()
    }

    #[test]
    fn worker_result_to_outcome_maps_all_known_status_strings() {
        use crate::types::TestOutcome;

        assert!(matches!(
            TestOutcome::from(make_wire_result("passed").2),
            TestOutcome::Passed { .. }
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("failed").2),
            TestOutcome::Failed(..)
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("error").2),
            TestOutcome::Error(..)
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("skipped").2),
            TestOutcome::Skipped { .. }
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("xfailed").2),
            TestOutcome::XFailed { .. }
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("xpassed").2),
            TestOutcome::XPassed { .. }
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("warned").2),
            TestOutcome::Warned { .. }
        ));
        assert!(matches!(
            TestOutcome::from(make_wire_result("timeout").2),
            TestOutcome::Timeout { .. }
        ));
    }

    #[test]
    fn worker_result_unknown_status_falls_back_to_error() {
        use crate::types::TestOutcome;
        // Any unrecognised status string must become Error, not panic.
        let (_, _, outcome) = make_wire_result("flaky");
        assert!(
            matches!(TestOutcome::from(outcome), TestOutcome::Error { .. }),
            "unknown status must map to Error"
        );
    }

    #[test]
    fn worker_outcome_timed_out_has_error_outcome_and_preserves_duration() {
        use crate::types::TestOutcome;
        let (wo, dur) = WorkerOutcome::timed_out(std::time::Duration::from_secs(30));
        assert!(dur > 0.0, "timed_out must produce a positive duration");
        match TestOutcome::from(wo) {
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
    fn worker_outcome_crashed_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let wo = WorkerOutcome::crashed();
        match TestOutcome::from(wo) {
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
    fn worker_result_timed_out_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let (wo, _dur) = WorkerOutcome::timed_out(std::time::Duration::from_secs(60));
        assert!(
            matches!(TestOutcome::from(wo), TestOutcome::Error(..)),
            "timed_out WorkerOutcome must map to TestOutcome::Error"
        );
    }

    #[test]
    fn worker_result_crashed_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let wo = WorkerOutcome::crashed();
        assert!(
            matches!(TestOutcome::from(wo), TestOutcome::Error(..)),
            "crashed WorkerOutcome must map to TestOutcome::Error"
        );
    }

    #[test]
    fn worker_result_no_message_lines_filters_negatives_and_zero() {
        use crate::types::TestOutcome;

        let wr: WireResult = serde_json::from_str(
            r#"{"node_id":"t::f","outcome":"passed","duration_ms":1.0,"no_message_lines":[-1,0,5,10]}"#,
        )
        .expect("test JSON must be valid");

        let (_, _, worker_outcome) = wr.into_worker_outcome();
        match TestOutcome::from(worker_outcome) {
            TestOutcome::Passed { no_message_lines } => {
                assert_eq!(
                    no_message_lines,
                    vec![5, 10],
                    "negative and zero values in no_message_lines must be filtered before usize cast"
                );
            }
            other => panic!("expected Passed outcome, got {other:?}"),
        }
    }
}
