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

pub(crate) use drain::{
    DrainContext, DrainOutcome, drain_until_eof, drain_worker_results, handle_drain_outcome,
};
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

/// One message from a worker thread to the coordinator's consumer loop.
///
/// Diagnostics ride the same channel as results rather than a side bag: the
/// consumer loop breaks early on maxfail, so anything drained only after the
/// loop would be silently skipped on exactly that path — which is the
/// missable-drain failure #1840 exists to remove, one layer down.
pub(crate) enum WorkerMessage {
    Result(WorkerResult),
    Diagnostic(crate::reporter::stats::DiagnosticEntry),
}

/// Result of a test execution phase (serial or parallel).
pub(crate) struct PhaseResult {
    /// Whether execution was interrupted (e.g., by maxfail).
    pub interrupted: bool,
    /// Per-test timing data.
    pub timings: Vec<types::TestTiming>,
}

/// Everything a worker needs to rebuild the coordinator's fixture registry.
///
/// Both halves are collected once and sent with every task: a worker has its
/// own `FixtureSession`, and anything missing here is simply invisible to it.
#[derive(Clone, Copy)]
pub(crate) struct SessionInputs<'a> {
    pub conftest_paths: &'a [camino::Utf8PathBuf],
    pub fixture_modules: &'a [types::FixtureModule],
    /// Names of `lifetime="process"` fixtures. Not sent to workers — used by
    /// the coordinator to name what a killed worker never tore down (#1777).
    pub process_fixture_names: &'a [String],
}

/// Pre-serialize the fixture-module list, once for the whole run.
///
/// Every task carries the identical list, so serializing per task would repeat
/// the work for no gain — the same reason `conftest_paths` is prepared once.
///
/// A free function rather than a block inside `run_phase_parallel`, because
/// that function needs a live scheduler and real subprocesses: nothing in
/// `cargo test` can enter it, so anything inlined there is untestable.
fn serialize_fixture_modules(
    fixture_modules: &[types::FixtureModule],
) -> std::sync::Arc<serde_json::value::RawValue> {
    let json_str = serde_json::to_string(fixture_modules).expect("fixture modules serialize");
    std::sync::Arc::from(serde_json::value::RawValue::from_string(json_str).expect("valid JSON"))
}

/// Pre-serialize the plugin activation inputs, once for the whole run.
///
/// Workers rebuild their own `FixtureSession` and inherit nothing from the
/// coordinator, so without these a worker has no plugins: both
/// `FixtureProvider` fixtures and plugin `__fixtures__.py` declarations are
/// invisible under `-n` while passing serially. That was true of the shipped
/// provider path too, measured on `main` before this change (#1717).
///
/// A free function for the same reason as `serialize_fixture_modules`:
/// `run_phase_parallel` needs live subprocesses, so nothing inlined there is
/// reachable from `cargo test`.
fn serialize_plugin_inputs(
    plugins: &[String],
    plugin_settings: &std::collections::HashMap<String, toml::Value>,
) -> std::sync::Arc<serde_json::value::RawValue> {
    let payload = serde_json::json!({
        "modules": plugins,
        "settings": plugin_settings,
    });
    let json_str = serde_json::to_string(&payload).expect("plugin inputs serialize");
    std::sync::Arc::from(serde_json::value::RawValue::from_string(json_str).expect("valid JSON"))
}

pub(crate) fn run_phase_parallel(
    groups: Vec<scheduler::TaskGroup>,
    cfg: &config::Config,
    worker_count: usize, // caller computes optimal count
    session_inputs: SessionInputs<'_>,
    python_bin: &str,
    rep: &mut dyn reporter::Reporter,
    pool: Option<Vec<PrewarmedWorker>>,
) -> PhaseResult {
    let SessionInputs {
        conftest_paths,
        fixture_modules,
        process_fixture_names: session_process_fixtures,
    } = session_inputs;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    let worker_count = worker_count.max(1).min(groups.len().max(1));
    let total: usize = groups.iter().map(|g| g.item_count()).sum();
    // Build node_id → Arc<TestItem> before groups are consumed by the scheduler.
    // Items are already Arc-wrapped from collection — no deep clone needed.
    let item_lookup: ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>> = groups
        .iter()
        .flat_map(|g| g.items())
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
    let fixture_modules_raw = serialize_fixture_modules(fixture_modules);
    let plugins_raw = serialize_plugin_inputs(&cfg.features.plugins, &cfg.features.plugin_settings);
    let timeout_secs = cfg.exec.timeout_secs;
    let keep_tmp: Arc<str> = Arc::from(cfg.output.keep_tmp.as_str());
    let rootdir: Arc<str> = Arc::from(cfg.rootdir.as_str());
    let show_locals = cfg.output.show_locals;
    let show_internals = cfg.output.show_internals;
    let python_bin: Arc<str> = Arc::from(python_bin);
    // Run-constant, read once: the registry is fully populated before any test
    // runs, and this only decorates a warning about an already-dead worker
    // (#1777). Empty for every suite that does not declare the tier.
    let process_fixtures: Arc<[String]> = Arc::from(session_process_fixtures);

    let (tx, rx) = crossbeam_channel::unbounded::<WorkerMessage>();

    // Use pre-warmed workers first, fall back to spawning fresh ones.
    let mut prewarmed = pool.unwrap_or_default();
    let handles: Vec<_> = (0..worker_count)
        .map(|i| {
            let worker_params = WorkerParams {
                worker_id: i,
                sched: Arc::clone(&sched),
                cancelled: Arc::clone(&cancelled),
                conftest_json: std::sync::Arc::clone(&conftest_raw),
                fixture_modules_json: std::sync::Arc::clone(&fixture_modules_raw),
                plugins_json: std::sync::Arc::clone(&plugins_raw),
                timeout_secs,
                keep_tmp: keep_tmp.clone(),
                rootdir: rootdir.clone(),
                show_locals,
                show_internals,
                tx: tx.clone(),
                in_flight: std::sync::Arc::clone(&in_flight),
                process_fixtures: Arc::clone(&process_fixtures),
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

    for message in rx {
        let WorkerResult {
            resolved,
            worker_id,
        } = match message {
            WorkerMessage::Diagnostic(entry) => {
                rep.record_diagnostics(vec![entry]);
                continue;
            }
            WorkerMessage::Result(result) => result,
        };
        let node_id = resolved.node_id.clone(); // Arc refcount bump — cheap
        {
            in_flight.lock().remove(node_id.as_ref());
        }

        let parallel_ctx = crate::parallel_context::ParallelContext::new(
            worker_id + 1, // 1-indexed for display
            Arc::clone(&in_flight),
        );

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
mod fixture_module_payload_tests {
    use super::*;

    /// The worker iterates this list and indexes each entry by name, so both
    /// the array shape and the key names are a cross-language contract.
    #[test]
    fn serializes_to_the_shape_the_worker_reads() {
        // declares_package is set but must not appear below: it drives
        // coordinator-side scheduling only, and adding a key to this payload
        // would change a cross-language contract for no reason the worker cares
        // about.
        let modules = vec![types::FixtureModule {
            module: camino::Utf8PathBuf::from("pkg/__fixtures__.py"),
            anchor: camino::Utf8PathBuf::from("pkg"),
            package_declarations: vec![],
        }];

        let raw = serialize_fixture_modules(&modules);

        assert_eq!(
            raw.get(),
            r#"[{"module":"pkg/__fixtures__.py","anchor":"pkg"}]"#,
            "worker.py reads entry['module'] and entry['anchor'] — a rename or \
             a switch to positional arrays is a silent KeyError over there"
        );
    }

    /// Most projects have no `__fixtures__.py`; the worker must receive an
    /// empty array it can iterate, not `null`.
    #[test]
    fn empty_input_serializes_to_an_empty_array() {
        let raw = serialize_fixture_modules(&[]);

        assert_eq!(
            raw.get(),
            "[]",
            "null would make the worker's `for entry in ...` raise instead of \
             skipping"
        );
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
            other => panic!("Expected Failed, got {other:?}"),
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
            other => panic!("Expected Error, got {other:?}"),
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
