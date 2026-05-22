//! Subprocess worker pool for parallel test execution.
//!
//! Spawns `python -m oxitest._bridge.worker` subprocesses, one per worker slot.
//! Each worker is persistent within a run — it receives JSON tasks over stdin
//! (one module group per task) and streams JSON result lines back over stdout.
//!
//! Includes a per-result watchdog timeout: if a worker stalls, it is killed and
//! remaining tests are marked as timed out. Worker crashes produce sentinel
//! error results so the pipeline never hangs.

use crate::{
    config, reporter, scheduler, types,
    worker_result::{WorkerResult, WorkerTask, WorkerTaskItem},
};

#[derive(Debug, PartialEq)]
pub(crate) enum DrainOutcome {
    /// All `expected` results received successfully.
    Complete,
    /// Watchdog deadline elapsed before all results arrived; subprocess should be killed.
    TimedOut,
    /// Channel disconnected (subprocess closed stdout) before all results arrived.
    Disconnected,
}

/// Reads up to `expected` result lines from `line_rx`.
///
/// The watchdog deadline is per-result: it starts when a group is dispatched and
/// resets only when a real (non-empty, parseable or not) result line is received.
/// Empty lines do NOT reset the deadline — a subprocess spamming blank output
/// cannot prevent the watchdog from firing.
pub(crate) fn drain_worker_results(
    line_rx: &crossbeam_channel::Receiver<String>,
    expected: usize,
    watchdog: std::time::Duration,
    tx: &crossbeam_channel::Sender<WorkerResult>,
) -> (DrainOutcome, usize) {
    use std::time::Instant;

    let mut received = 0usize;
    // Per-result deadline: resets only when a real result line is received.
    // Empty lines do NOT reset it — a subprocess spamming blank output cannot
    // prevent the watchdog from firing.
    let mut result_deadline = Instant::now() + watchdog;

    loop {
        if received >= expected {
            return (DrainOutcome::Complete, received);
        }

        let remaining = result_deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return (DrainOutcome::TimedOut, received);
        }

        match line_rx.recv_timeout(remaining) {
            Ok(line) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    // Empty line: skip without resetting the deadline.
                    continue;
                }
                match serde_json::from_str::<WorkerResult>(trimmed) {
                    Ok(r) => {
                        received += 1;
                        // Reset deadline: subprocess is alive and responding.
                        result_deadline = Instant::now() + watchdog;
                        let _ = tx.send(r);
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, output = %trimmed, "bad worker output");
                        received += 1;
                        result_deadline = Instant::now() + watchdog;
                    }
                }
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                return (DrainOutcome::TimedOut, received);
            }
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                return (DrainOutcome::Disconnected, received);
            }
        }
    }
}

pub(crate) fn compute_optimal_workers(
    explicit_workers: Option<crate::config::WorkerCount>,
    serial: bool,
    cpu_count: usize,
    estimated: Option<std::time::Duration>,
    spawn_overhead_ms: f64,
) -> usize {
    use crate::config::WorkerCount;

    if serial {
        return 1;
    }
    match explicit_workers {
        Some(WorkerCount::Fixed(n)) => return n,
        Some(WorkerCount::Auto) | None => {}
    }
    if let Some(est) = estimated {
        let est_ms = est.as_millis() as f64;
        let needed = (est_ms / spawn_overhead_ms).ceil() as usize;
        cpu_count.min(needed).max(1)
    } else {
        cpu_count
    }
}

/// Takes stdin and stdout pipes from a child spawned with `Stdio::piped()`.
/// Returns `None` if either pipe is unexpectedly absent (OS-level failure).
fn take_child_pipes(
    child: &mut std::process::Child,
) -> Option<(
    std::io::BufWriter<std::process::ChildStdin>,
    std::io::BufReader<std::process::ChildStdout>,
)> {
    let stdin = child.stdin.take()?;
    let stdout = child.stdout.take()?;
    Some((
        std::io::BufWriter::new(stdin),
        std::io::BufReader::new(stdout),
    ))
}

/// Spawns a worker subprocess and returns its stdin, a line receiver for stdout,
/// and the child handle. Returns `None` on spawn or pipe failure.
fn setup_worker_process(
    python_bin: &str,
) -> Option<(
    std::process::Child,
    std::io::BufWriter<std::process::ChildStdin>,
    crossbeam_channel::Receiver<String>,
)> {
    use std::io::BufRead;
    use std::process::{Command, Stdio};

    let mut child = match Command::new(python_bin)
        .args(["-m", "oxitest._bridge.worker"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("failed to spawn worker: {e}");
            return None;
        }
    };

    let (worker_stdin, worker_stdout) = match take_child_pipes(&mut child) {
        Some(pipes) => pipes,
        None => {
            tracing::error!("worker stdin/stdout unexpectedly None — OS failed to pipe stdio");
            let _ = child.kill();
            return None;
        }
    };

    // Offload blocking reads into a dedicated thread so the worker loop
    // can use recv_timeout() and remain responsive to the watchdog deadline.
    let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
    let _reader = std::thread::spawn(move || {
        let mut stdout = worker_stdout;
        loop {
            let mut line = String::new();
            match stdout.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    if line_tx.send(line).is_err() {
                        break;
                    }
                }
            }
        }
    });

    Some((child, worker_stdin, line_rx))
}

/// Handles the result of draining worker output for one group.
/// Returns `false` if the subprocess died and should not receive more tasks.
fn handle_drain_outcome(
    outcome: DrainOutcome,
    child: &mut std::process::Child,
    items: &[crate::types::TestItem],
    received: usize,
    watchdog: std::time::Duration,
    module_path: &camino::Utf8Path,
    tx: &crossbeam_channel::Sender<WorkerResult>,
) -> bool {
    match outcome {
        DrainOutcome::Complete => true,
        DrainOutcome::TimedOut => {
            tracing::error!(
                module = %module_path,
                watchdog_secs = watchdog.as_secs(),
                "worker subprocess unresponsive; killing"
            );
            let _ = child.kill();
            for item in items.iter().skip(received) {
                let _ = tx.send(WorkerResult::timed_out(item.node_id.to_string(), watchdog));
            }
            false
        }
        DrainOutcome::Disconnected => {
            for item in items.iter().skip(received) {
                let _ = tx.send(WorkerResult::crashed(item.node_id.to_string()));
            }
            false
        }
    }
}

pub(crate) fn spawn_worker(
    python_bin: String,
    sched: std::sync::Arc<scheduler::Scheduler>,
    cancelled: std::sync::Arc<std::sync::atomic::AtomicBool>,
    conftest_json: std::sync::Arc<Vec<String>>,
    timeout_secs: Option<u64>,
    tx: crossbeam_channel::Sender<WorkerResult>,
) -> std::thread::JoinHandle<()> {
    use std::io::Write;
    use std::sync::atomic::Ordering;
    use std::time::Duration;

    // Per-result watchdog: how long to wait for one test result line before
    // declaring the subprocess unresponsive and killing it.
    //
    // When a per-test timeout is configured the Python subprocess will emit a
    // "timeout" result within timeout_secs; we add 30 s of grace for subprocess
    // startup and teardown overhead.  Without a configured timeout we use a
    // 10-minute cap so an unresponsive subprocess never hangs the run forever.
    let watchdog: Duration = timeout_secs
        .map(|t| Duration::from_secs(t.saturating_add(30)))
        .unwrap_or(Duration::from_secs(600));

    std::thread::spawn(move || {
        let (mut child, mut worker_stdin, line_rx) = match setup_worker_process(&python_bin) {
            Some(v) => v,
            None => return,
        };

        let mut subprocess_alive = true;

        while subprocess_alive && !cancelled.load(Ordering::Relaxed) {
            let Some(group) = sched.pop() else { break };

            let task = WorkerTask {
                module_path: group.module_path.as_str(),
                items: group
                    .items
                    .iter()
                    .map(|item| WorkerTaskItem {
                        fn_name: &item.fn_name,
                        param_id: item.param_id.as_deref(),
                    })
                    .collect(),
                conftest_paths: &conftest_json,
                timeout_secs,
            };
            let task_json = match serde_json::to_string(&task) {
                Ok(j) => j,
                Err(e) => {
                    tracing::error!(
                        module = %group.module_path,
                        error = %e,
                        "failed to serialize WorkerTask — emitting error for all group items"
                    );
                    for item in &group.items {
                        let _ = tx.send(WorkerResult::crashed(item.node_id.to_string()));
                    }
                    break;
                }
            };

            if writeln!(worker_stdin, "{task_json}").is_err() || worker_stdin.flush().is_err() {
                tracing::warn!(
                    module = %group.module_path,
                    "failed to send task to worker — emitting error for all group items"
                );
                for item in &group.items {
                    let _ = tx.send(WorkerResult::crashed(item.node_id.to_string()));
                }
                break;
            }

            let expected = group.items.len();
            let (drain_outcome, received) = drain_worker_results(&line_rx, expected, watchdog, &tx);

            subprocess_alive = handle_drain_outcome(
                drain_outcome,
                &mut child,
                &group.items,
                received,
                watchdog,
                &group.module_path,
                &tx,
            );
        }

        drop(worker_stdin);
        let _ = child.wait();
    })
}

/// Processes one result received from a worker channel.
///
/// Returns `Some(outcome)` when the `node_id` maps to a scheduled item and the
/// reporter has been notified.  Returns `None` when the `node_id` is not
/// recognised (worker bug or protocol mismatch); the caller should skip the
/// result in that case.
fn handle_worker_result(
    result: &WorkerResult,
    item_lookup: &ahash::AHashMap<String, std::sync::Arc<types::TestItem>>,
    rep: &mut dyn reporter::Reporter,
    timings: &mut Vec<types::TestTiming>,
) -> Option<types::TestOutcome> {
    let item = match item_lookup.get(&result.node_id).map(std::sync::Arc::clone) {
        Some(item) => item,
        None => {
            tracing::warn!(
                node_id = %result.node_id,
                "worker sent unknown node_id — skipping result"
            );
            return None;
        }
    };
    let node_id = types::NodeId::from_raw(&result.node_id);
    let outcome = result.to_outcome();
    let duration_ms = types::DurationMs::new(result.duration_ms);
    rep.test_started(&item);
    rep.test_completed(&item, &outcome, duration_ms);
    timings.push(types::TestTiming {
        node_id,
        duration_ms,
        outcome: types::OutcomeKind::from(&outcome),
    });
    Some(outcome)
}

/// Drain any groups still queued in `sched` after all workers have exited and
/// emit `WorkerResult::crashed` for each item via the reporter.
///
/// Called from `run_phase_parallel` after its result channel is exhausted — at
/// that point every worker thread has already returned (they drop their `tx`
/// clone on exit, which is what causes the channel to close), so no live worker
/// will race us for remaining scheduler entries.
fn drain_remaining_into_crashed(
    sched: &scheduler::Scheduler,
    item_lookup: &ahash::AHashMap<String, std::sync::Arc<types::TestItem>>,
    rep: &mut dyn reporter::Reporter,
    timings: &mut Vec<types::TestTiming>,
) {
    while let Some(group) = sched.pop() {
        for item in &group.items {
            let result = WorkerResult::crashed(item.node_id.to_string());
            handle_worker_result(&result, item_lookup, rep, timings);
        }
    }
}

/// Result of a test execution phase (serial or parallel).
pub(crate) struct PhaseResult {
    /// Whether execution was interrupted (e.g., by maxfail).
    pub interrupted: bool,
    /// Per-test timing data.
    pub timings: Vec<types::TestTiming>,
}

pub(crate) fn run_phase_parallel(
    groups: Vec<(camino::Utf8PathBuf, Vec<types::TestItem>)>,
    cfg: &config::Config,
    worker_count: usize, // caller computes optimal count
    conftest_paths: &[camino::Utf8PathBuf],
    rep: &mut dyn reporter::Reporter,
) -> PhaseResult {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    let worker_count = worker_count.max(1).min(groups.len().max(1));
    // Build node_id → Arc<TestItem> before groups are consumed by the scheduler.
    // Arc avoids a full TestItem clone on every result received from workers.
    let item_lookup: ahash::AHashMap<String, std::sync::Arc<types::TestItem>> = groups
        .iter()
        .flat_map(|(_, items)| items.iter())
        .map(|item| (item.node_id.to_string(), std::sync::Arc::new(item.clone())))
        .collect();
    let sched = Arc::new(scheduler::Scheduler::new(groups));
    let cancelled = Arc::new(AtomicBool::new(false));
    let conftest_json: std::sync::Arc<Vec<String>> =
        std::sync::Arc::new(conftest_paths.iter().map(|p| p.to_string()).collect());
    let timeout_secs = cfg.timeout_secs;
    let python_bin = std::env::var("PYO3_PYTHON").unwrap_or_else(|_| "python3".to_string());

    let (tx, rx) = crossbeam_channel::unbounded::<WorkerResult>();

    let handles: Vec<_> = (0..worker_count)
        .map(|_| {
            spawn_worker(
                python_bin.clone(),
                Arc::clone(&sched),
                Arc::clone(&cancelled),
                std::sync::Arc::clone(&conftest_json),
                timeout_secs,
                tx.clone(),
            )
        })
        .collect();

    drop(tx);

    let mut acc = types::FailureAccumulator::new(cfg.maxfail);
    let mut interrupted = false;
    let mut timings: Vec<types::TestTiming> = Vec::new();

    for result in rx {
        let Some(outcome) = handle_worker_result(&result, &item_lookup, rep, &mut timings) else {
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
    use super::*;
    use crate::config::WorkerCount;
    use std::time::Duration;

    #[test]
    fn explicit_workers_bypasses_auto_scale() {
        let count = compute_optimal_workers(
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
        let count =
            compute_optimal_workers(None, true, 8, Some(Duration::from_millis(5000)), 250.0);
        assert_eq!(count, 1);
    }

    #[test]
    fn auto_scale_caps_to_needed_workers() {
        // 500ms suite / 250ms per worker = 2 workers needed; cpu_count = 8 → use 2
        let count =
            compute_optimal_workers(None, false, 8, Some(Duration::from_millis(500)), 250.0);
        assert_eq!(count, 2);
    }

    #[test]
    fn auto_scale_caps_to_cpu_count() {
        // 10_000ms / 250ms = 40 workers needed; cpu_count = 4 → use 4
        let count =
            compute_optimal_workers(None, false, 4, Some(Duration::from_millis(10_000)), 250.0);
        assert_eq!(count, 4);
    }

    #[test]
    fn cold_cache_returns_cpu_count() {
        let count = compute_optimal_workers(None, false, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn short_suite_uses_one_worker() {
        // 200ms suite / 250ms per worker < 1 → ceil = 1
        let count =
            compute_optimal_workers(None, false, 8, Some(Duration::from_millis(200)), 250.0);
        assert_eq!(count, 1);
    }

    #[test]
    fn serial_overrides_explicit_workers() {
        // Serial must be absolute even when workers is explicitly set
        let count = compute_optimal_workers(Some(WorkerCount::Fixed(4)), true, 8, None, 250.0);
        assert_eq!(count, 1);
    }

    #[test]
    fn auto_warm_cache_scales_to_needed() {
        // 500ms suite / 250ms overhead = 2 workers needed; cpu = 8 → use 2
        let count = compute_optimal_workers(
            Some(WorkerCount::Auto),
            false,
            8,
            Some(Duration::from_millis(500)),
            250.0,
        );
        assert_eq!(count, 2);
    }

    #[test]
    fn auto_warm_cache_caps_to_cpu() {
        // 10_000ms / 250ms = 40 needed; cpu = 4 → use 4
        let count = compute_optimal_workers(
            Some(WorkerCount::Auto),
            false,
            4,
            Some(Duration::from_millis(10_000)),
            250.0,
        );
        assert_eq!(count, 4);
    }

    #[test]
    fn auto_cold_cache_returns_cpu_count() {
        let count = compute_optimal_workers(Some(WorkerCount::Auto), false, 6, None, 250.0);
        assert_eq!(count, 6);
    }

    #[test]
    fn worker_result_populates_failed_diagnostic_fields() {
        // Construct WorkerResult as if deserialized from worker JSON
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
        let result: WorkerResult = serde_json::from_str(json).unwrap();
        let outcome = result.to_outcome();

        match outcome {
            crate::types::TestOutcome::Failed {
                file,
                lineno,
                source_line,
                left,
                right,
                op,
                ..
            } => {
                assert_eq!(file, "test_mod.py");
                assert_eq!(lineno, 10usize);
                assert_eq!(source_line, "assert x == y");
                assert_eq!(left, "1");
                assert_eq!(right, "2");
                assert_eq!(op, "==");
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
        let result: WorkerResult = serde_json::from_str(json).unwrap();
        match result.to_outcome() {
            crate::types::TestOutcome::Error {
                file,
                lineno,
                source_line,
                ..
            } => {
                assert_eq!(file, "t.py");
                assert_eq!(lineno, 5usize);
                assert_eq!(source_line, "import bad");
            }
            other => panic!("Expected Error, got {:?}", other),
        }
    }

    fn make_worker_result(status: &str) -> WorkerResult {
        serde_json::from_str(&format!(
            r#"{{"node_id":"t::f","outcome":"{status}","duration_ms":1.0,"failure_repr":"reason"}}"#
        ))
        .expect("test JSON must be valid")
    }

    #[test]
    fn worker_result_to_outcome_maps_all_known_status_strings() {
        use crate::types::TestOutcome;

        assert!(matches!(
            make_worker_result("passed").to_outcome(),
            TestOutcome::Passed { .. }
        ));
        assert!(matches!(
            make_worker_result("failed").to_outcome(),
            TestOutcome::Failed { .. }
        ));
        assert!(matches!(
            make_worker_result("error").to_outcome(),
            TestOutcome::Error { .. }
        ));
        assert!(matches!(
            make_worker_result("skipped").to_outcome(),
            TestOutcome::Skipped { .. }
        ));
        assert!(matches!(
            make_worker_result("xfailed").to_outcome(),
            TestOutcome::XFailed { .. }
        ));
        assert!(matches!(
            make_worker_result("xpassed").to_outcome(),
            TestOutcome::XPassed { .. }
        ));
        assert!(matches!(
            make_worker_result("warned").to_outcome(),
            TestOutcome::Warned { .. }
        ));
        assert!(matches!(
            make_worker_result("timeout").to_outcome(),
            TestOutcome::Timeout { .. }
        ));
    }

    #[test]
    fn worker_result_unknown_status_falls_back_to_error() {
        use crate::types::TestOutcome;
        // Any unrecognised status string must become Error, not panic.
        let result = make_worker_result("flaky");
        assert!(
            matches!(result.to_outcome(), TestOutcome::Error { .. }),
            "unknown status must map to Error"
        );
    }

    #[test]
    fn worker_result_timed_out_has_error_outcome_and_preserves_node_id() {
        let r = WorkerResult::timed_out(
            "tests/test_foo.py::test_slow".to_string(),
            std::time::Duration::from_secs(30),
        );
        assert_eq!(
            r.outcome,
            types::OutcomeKind::Error,
            "timed_out must produce outcome=Error"
        );
        assert_eq!(r.node_id, "tests/test_foo.py::test_slow");
        let msg = r.message.as_deref().unwrap_or("");
        assert!(
            msg.contains("30"),
            "message should mention the watchdog duration"
        );
    }

    #[test]
    fn worker_result_crashed_has_error_outcome_and_preserves_node_id() {
        let r = WorkerResult::crashed("tests/test_foo.py::test_gone".to_string());
        assert_eq!(
            r.outcome,
            types::OutcomeKind::Error,
            "crashed must produce outcome=Error"
        );
        assert_eq!(r.node_id, "tests/test_foo.py::test_gone");
        assert!(
            r.message.is_some(),
            "crashed result must include a message explaining the failure"
        );
    }

    #[test]
    fn worker_result_timed_out_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let r = WorkerResult::timed_out("t::f".to_string(), std::time::Duration::from_secs(60));
        assert!(
            matches!(r.to_outcome(), TestOutcome::Error { .. }),
            "timed_out WorkerResult must map to TestOutcome::Error"
        );
    }

    #[test]
    fn worker_result_crashed_converts_to_error_outcome() {
        use crate::types::TestOutcome;
        let r = WorkerResult::crashed("t::f".to_string());
        assert!(
            matches!(r.to_outcome(), TestOutcome::Error { .. }),
            "crashed WorkerResult must map to TestOutcome::Error"
        );
    }

    #[test]
    fn worker_result_no_message_lines_filters_negatives_and_zero() {
        use crate::types::TestOutcome;

        let wr: WorkerResult = serde_json::from_str(
            r#"{"node_id":"t::f","outcome":"passed","duration_ms":1.0,"no_message_lines":[-1,0,5,10]}"#,
        )
        .expect("test JSON must be valid");

        match wr.to_outcome() {
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

#[cfg(test)]
mod repro_tests {
    use super::*;
    use std::time::{Duration, Instant};

    /// Regression test for bug #44: a subprocess spamming empty lines must not
    /// prevent the watchdog from firing.
    ///
    /// The old inline loop called recv_timeout(watchdog) fresh on every iteration,
    /// so continuous empty lines reset the full timer indefinitely.
    ///
    /// drain_worker_results() fixes this by tracking a result_deadline that is
    /// only reset when a real result line is received.
    ///
    /// Expected (bug present):  loop spins for ~300ms (spammer duration).
    /// Expected (bug fixed):    drain_worker_results exits via TimedOut within ~50ms.
    #[test]
    fn bug44_empty_lines_spin_without_progress() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        // Thread that sends empty lines for 300ms — simulates a panicked
        // Python subprocess continuously flushing its stdout buffer.
        std::thread::spawn(move || {
            let stop = Instant::now() + Duration::from_millis(300);
            while Instant::now() < stop {
                if line_tx.send("\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let watchdog = Duration::from_millis(50);
        let start = Instant::now();

        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx);

        let elapsed = start.elapsed();
        assert_eq!(received, 0, "no real results were sent");
        assert!(
            matches!(outcome, DrainOutcome::TimedOut),
            "expected TimedOut, got {outcome:?}"
        );
        // Fails with the bug (elapsed ≈ 300ms); passes after the fix (elapsed ≈ 50ms).
        assert!(
            elapsed <= Duration::from_millis(100),
            "BUG #44: watchdog did not fire within 100ms; elapsed={elapsed:?}. \
             Empty lines are resetting the recv_timeout timer."
        );
    }
}

#[cfg(test)]
mod drain_tests {
    use super::*;
    use std::time::Duration;

    fn valid_json(node_id: &str) -> String {
        format!(r#"{{"node_id":"{node_id}","outcome":"passed","duration_ms":1.0}}"#)
    }

    // ── Test 1 ──────────────────────────────────────────────────────────────
    // Empty lines must NOT reset the deadline.
    // Setup: send 50 empty lines then close the sender (disconnect).
    // Watchdog = 50ms. The disconnect must arrive within ~50ms of the first
    // empty line, not 50ms × 50 = 2500ms.
    #[test]
    fn empty_lines_do_not_reset_deadline() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        // Send 50 empty lines then close the channel.
        for _ in 0..50 {
            line_tx.send("\n".to_string()).unwrap();
        }
        drop(line_tx); // disconnect

        let watchdog = Duration::from_millis(50);
        let start = std::time::Instant::now();
        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx);
        let elapsed = start.elapsed();

        // Should disconnect quickly (50 sends are instant), not hang for 50 × 50ms.
        assert!(
            elapsed < Duration::from_millis(200),
            "drain took {elapsed:?}; empty lines must not inflate total wait time"
        );
        assert_eq!(received, 0, "no real results were sent");
        assert!(
            matches!(outcome, DrainOutcome::Disconnected),
            "expected Disconnected, got {outcome:?}"
        );
    }

    // ── Test 2 ──────────────────────────────────────────────────────────────
    // Valid results reset the deadline so a slow-but-alive worker isn't killed.
    #[test]
    fn valid_result_resets_deadline() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        let watchdog = Duration::from_millis(100);

        // Spawn a thread that sends two results with 60ms between them.
        // Each result resets the 100ms deadline, so neither should time out.
        let handle = std::thread::spawn(move || {
            line_tx.send(valid_json("t::a")).unwrap();
            std::thread::sleep(Duration::from_millis(60));
            line_tx.send(valid_json("t::b")).unwrap();
        });

        let (outcome, received) = drain_worker_results(&line_rx, 2, watchdog, &result_tx);
        handle.join().unwrap();

        assert_eq!(outcome, DrainOutcome::Complete, "expected Complete");
        assert_eq!(received, 2);
        assert_eq!(result_rx.len(), 2, "both results must have been forwarded");
    }

    // ── Test 3 ──────────────────────────────────────────────────────────────
    // Empty lines mixed with a valid result: complete successfully.
    #[test]
    fn empty_lines_before_valid_result_still_complete() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        // 10 empty lines, then the real result.
        for _ in 0..10 {
            line_tx.send("   \n".to_string()).unwrap();
        }
        line_tx.send(valid_json("t::f")).unwrap();
        drop(line_tx);

        let (outcome, received) =
            drain_worker_results(&line_rx, 1, Duration::from_millis(500), &result_tx);

        assert_eq!(outcome, DrainOutcome::Complete);
        assert_eq!(received, 1);
        assert_eq!(result_rx.len(), 1);
    }

    // ── Test 4 ──────────────────────────────────────────────────────────────
    // Disconnect mid-group: received count reflects how many results arrived.
    #[test]
    fn disconnected_mid_group_returns_received_count() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        line_tx.send(valid_json("t::a")).unwrap();
        line_tx.send(valid_json("t::b")).unwrap();
        drop(line_tx); // disconnect before all 4 expected

        let (outcome, received) =
            drain_worker_results(&line_rx, 4, Duration::from_millis(500), &result_tx);

        assert!(
            matches!(outcome, DrainOutcome::Disconnected),
            "expected Disconnected, got {outcome:?}"
        );
        assert_eq!(received, 2);
        assert_eq!(result_rx.len(), 2);
    }

    // ── Test 5 ──────────────────────────────────────────────────────────────
    // Exactly expected results received: Complete, no timeout.
    #[test]
    fn exact_expected_results_returns_complete() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        for i in 0..3 {
            line_tx.send(valid_json(&format!("t::{i}"))).unwrap();
        }
        drop(line_tx);

        let (outcome, received) =
            drain_worker_results(&line_rx, 3, Duration::from_millis(500), &result_tx);

        assert_eq!(outcome, DrainOutcome::Complete);
        assert_eq!(received, 3);
        assert_eq!(result_rx.len(), 3);
    }

    // ── Test 6 ──────────────────────────────────────────────────────────────
    // Watchdog fires when channel is silent (no lines at all).
    #[test]
    fn silent_channel_triggers_timeout() {
        let (_line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        let watchdog = Duration::from_millis(30);
        let start = std::time::Instant::now();
        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx);
        let elapsed = start.elapsed();

        assert!(
            matches!(outcome, DrainOutcome::TimedOut),
            "expected TimedOut, got {outcome:?}"
        );
        assert_eq!(received, 0);
        // Should fire close to watchdog duration, not sooner or much later.
        assert!(elapsed >= watchdog, "fired too early: {elapsed:?}");
        assert!(
            elapsed < watchdog * 5,
            "fired too late ({elapsed:?}); watchdog is {watchdog:?}"
        );
    }

    // ── Test 7 ──────────────────────────────────────────────────────────────────
    // After all workers crash, groups never popped from the scheduler must appear
    // as crashed ERROR results rather than being silently dropped.
    //
    // BUG: before the fix, drain_remaining_into_crashed does not exist — this test
    // fails to compile.  After the fix all 3 items must be reported as "error".
    #[test]
    fn drain_remaining_into_crashed_emits_error_for_every_item() {
        use crate::types::{CollectError, NodeId, TestItem, TestOutcome};
        use camino::Utf8PathBuf;
        use std::sync::Arc;

        fn make_test_item(path: &str, fn_name: &str) -> TestItem {
            TestItem {
                node_id: NodeId::new(path, fn_name, None),
                module_path: Utf8PathBuf::from(path),
                fn_name: fn_name.to_string(),
                lineno: 1,
                markers: vec![],
                param_id: None,
                param_values: vec![],
                is_async: false,
            }
        }

        let all_items = vec![
            make_test_item("tests/a.py", "test_alpha"),
            make_test_item("tests/a.py", "test_beta"),
            make_test_item("tests/b.py", "test_gamma"),
        ];

        let groups: Vec<(camino::Utf8PathBuf, Vec<TestItem>)> = vec![
            (
                camino::Utf8PathBuf::from("tests/a.py"),
                vec![all_items[0].clone(), all_items[1].clone()],
            ),
            (
                camino::Utf8PathBuf::from("tests/b.py"),
                vec![all_items[2].clone()],
            ),
        ];

        let sched = Arc::new(crate::scheduler::Scheduler::new(groups));

        let item_lookup: ahash::AHashMap<String, Arc<TestItem>> = all_items
            .iter()
            .map(|i| (i.node_id.to_string(), Arc::new(i.clone())))
            .collect();

        struct CrashCollector {
            started: Vec<String>,
            completed: Vec<(String, String)>,
        }
        impl crate::reporter::Reporter for CrashCollector {
            fn test_started(&mut self, item: &TestItem) {
                self.started.push(item.node_id.to_string());
            }
            fn test_completed(
                &mut self,
                item: &TestItem,
                outcome: &TestOutcome,
                _ms: types::DurationMs,
            ) {
                self.completed
                    .push((item.node_id.to_string(), outcome.as_str().to_string()));
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> crate::reporter::ExitVote {
                crate::reporter::ExitVote::Abstain
            }
        }

        let mut rep = CrashCollector {
            started: vec![],
            completed: vec![],
        };
        let mut timings: Vec<crate::types::TestTiming> = vec![];

        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings);

        assert_eq!(rep.started.len(), 3, "all 3 items must be started");
        assert_eq!(rep.completed.len(), 3, "all 3 items must be completed");
        for (_, outcome) in &rep.completed {
            assert_eq!(outcome, "error", "every drained item must be outcome=error");
        }
        assert_eq!(timings.len(), 3, "timings must record each drained item");
    }

    // ── Test 8 ──────────────────────────────────────────────────────────────────
    // drain_remaining_into_crashed must be a noop on an already-empty scheduler.
    #[test]
    fn drain_remaining_into_crashed_is_noop_on_empty_scheduler() {
        use crate::types::{CollectError, TestItem};
        use std::sync::Arc;

        let sched = Arc::new(crate::scheduler::Scheduler::new(vec![]));
        let item_lookup: ahash::AHashMap<String, Arc<TestItem>> = ahash::AHashMap::new();

        struct NullReporter;
        impl crate::reporter::Reporter for NullReporter {
            fn test_started(&mut self, _: &TestItem) {
                panic!("must not be called on empty scheduler");
            }
            fn test_completed(
                &mut self,
                _: &TestItem,
                _: &crate::types::TestOutcome,
                _: types::DurationMs,
            ) {
                panic!("must not be called on empty scheduler");
            }
            fn finish(&mut self, _: &[CollectError], _: bool) -> crate::reporter::ExitVote {
                crate::reporter::ExitVote::Abstain
            }
        }

        let mut rep = NullReporter;
        let mut timings: Vec<crate::types::TestTiming> = vec![];
        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings);
        assert!(timings.is_empty());
    }
}

#[cfg(test)]
mod pipe_tests {
    use super::*;
    use std::process::{Command, Stdio};

    #[test]
    fn take_child_pipes_returns_some_for_piped_child() {
        let mut child = Command::new("true")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("test helper process must spawn");
        assert!(take_child_pipes(&mut child).is_some());
        let _ = child.wait();
    }

    #[test]
    fn take_child_pipes_returns_none_when_stdin_already_taken() {
        let mut child = Command::new("true")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("test helper process must spawn");
        // Drain stdin manually — simulates OS failing to provide it.
        let _ = child.stdin.take();
        assert!(take_child_pipes(&mut child).is_none());
        let _ = child.wait();
    }
}

#[cfg(test)]
mod result_handler_tests {
    use super::*;
    use std::sync::Arc;

    struct CountingReporter {
        started: usize,
        completed: usize,
    }
    impl reporter::Reporter for CountingReporter {
        fn test_started(&mut self, _: &types::TestItem) {
            self.started += 1;
        }
        fn test_completed(
            &mut self,
            _: &types::TestItem,
            _: &types::TestOutcome,
            _: types::DurationMs,
        ) {
            self.completed += 1;
        }
        fn finish(&mut self, _: &[types::CollectError], _: bool) -> reporter::ExitVote {
            reporter::ExitVote::Abstain
        }
    }

    fn make_worker_result(node_id: &str) -> WorkerResult {
        serde_json::from_str(&format!(
            r#"{{"node_id": "{node_id}", "outcome": "passed", "duration_ms": 1.0}}"#
        ))
        .unwrap()
    }

    fn make_item(node_id: &str) -> Arc<types::TestItem> {
        Arc::new(types::TestItem {
            node_id: types::NodeId::from_raw(node_id),
            module_path: camino::Utf8PathBuf::from("tests/test_mod.py"),
            fn_name: "test_fn".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        })
    }

    #[test]
    fn unknown_node_id_returns_none_and_skips_reporter() {
        let lookup: ahash::AHashMap<String, Arc<types::TestItem>> = ahash::AHashMap::new();
        let mut rep = CountingReporter {
            started: 0,
            completed: 0,
        };
        let mut timings: Vec<types::TestTiming> = vec![];

        let result = make_worker_result("unknown::test_fn");
        let outcome = handle_worker_result(&result, &lookup, &mut rep, &mut timings);

        assert!(outcome.is_none(), "should return None for unknown node_id");
        assert_eq!(rep.started, 0, "reporter.test_started must not be called");
        assert_eq!(
            rep.completed, 0,
            "reporter.test_completed must not be called"
        );
        assert!(timings.is_empty(), "timings must not be recorded");
    }

    #[test]
    fn known_node_id_returns_outcome_and_notifies_reporter() {
        let mut lookup: ahash::AHashMap<String, Arc<types::TestItem>> = ahash::AHashMap::new();
        lookup.insert("my_mod::test_fn".to_string(), make_item("my_mod::test_fn"));
        let mut rep = CountingReporter {
            started: 0,
            completed: 0,
        };
        let mut timings: Vec<types::TestTiming> = vec![];

        let result = make_worker_result("my_mod::test_fn");
        let outcome = handle_worker_result(&result, &lookup, &mut rep, &mut timings);

        assert!(outcome.is_some(), "should return Some for known node_id");
        assert_eq!(rep.started, 1, "reporter.test_started must be called once");
        assert_eq!(
            rep.completed, 1,
            "reporter.test_completed must be called once"
        );
        assert_eq!(timings.len(), 1, "one timing entry must be recorded");
    }
}
