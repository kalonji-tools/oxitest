//! Draining worker results and handling drain outcomes.
//!
//! Contains the per-result watchdog drain loop, outcome handling for timeouts
//! and disconnects, and the crash-drain logic for unassigned scheduler groups.

use crate::{reporter, scheduler, types, worker_result::WireResult};

use super::WorkerResult;

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
    worker_id: usize,
) -> (DrainOutcome, usize) {
    use std::time::Instant;

    let mut received = 0usize;
    // Per-result deadline: resets only when a real result line is received.
    // Empty lines do NOT reset it — a subprocess spamming blank output cannot
    // prevent the watchdog from firing.
    let mut result_deadline = Instant::now() + watchdog;
    let mut version_warned = false;

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
                match serde_json::from_str::<WireResult>(trimmed) {
                    Ok(r) => {
                        received += 1;
                        if !version_warned
                            && r.protocol_version() != crate::worker_result::PROTOCOL_VERSION
                        {
                            tracing::warn!(
                                expected = crate::worker_result::PROTOCOL_VERSION,
                                got = r.protocol_version(),
                                "Worker protocol version mismatch — results may be unreliable"
                            );
                            version_warned = true;
                        }
                        // Reset deadline: subprocess is alive and responding.
                        result_deadline = Instant::now() + watchdog;
                        let resolved = r.into_outcome();
                        let _ = tx.send(WorkerResult {
                            resolved,
                            worker_id,
                        });
                    }
                    Err(e) => {
                        received += 1;
                        result_deadline = Instant::now() + watchdog;
                        // Try to salvage node_id + duration_ms for an error sentinel
                        if let Ok(minimal) =
                            serde_json::from_str::<crate::worker_result::WireMinimal>(trimmed)
                        {
                            tracing::warn!(
                                error = %e,
                                node_id = %minimal.node_id,
                                "Unknown outcome from worker — treating as error"
                            );
                            let _ = tx.send(WorkerResult {
                                resolved: types::ResolvedOutcome {
                                    node_id: types::NodeId::from_raw(&minimal.node_id),
                                    duration_ms: types::DurationMs::new(minimal.duration_ms),
                                    outcome: types::TestOutcome::error_sentinel(format!(
                                        "Unknown wire result: {e}"
                                    )),
                                },
                                worker_id,
                            });
                        } else {
                            tracing::warn!(error = %e, output = %trimmed, "bad worker output");
                            let _ = tx.send(WorkerResult {
                                resolved: types::ResolvedOutcome {
                                    node_id: types::NodeId::from_raw("<worker>::malformed_output"),
                                    duration_ms: types::DurationMs::ZERO,
                                    outcome: types::TestOutcome::error_sentinel(format!(
                                        "Malformed worker output (not valid JSON): {e}"
                                    )),
                                },
                                worker_id,
                            });
                        }
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

/// Bundles the mutable and shared state needed by [`handle_drain_outcome`].
pub(crate) struct DrainContext<'a> {
    pub child: &'a mut std::process::Child,
    pub items: &'a [std::sync::Arc<crate::types::TestItem>],
    pub watchdog: std::time::Duration,
    pub module_path: &'a camino::Utf8Path,
    pub tx: &'a crossbeam_channel::Sender<WorkerResult>,
    pub worker_id: usize,
}

/// Handles the result of draining worker output for one group.
/// Returns `false` if the subprocess died and should not receive more tasks.
pub(crate) fn handle_drain_outcome(
    outcome: DrainOutcome,
    received: usize,
    ctx: &mut DrainContext<'_>,
) -> bool {
    match outcome {
        DrainOutcome::Complete => true,
        DrainOutcome::TimedOut => {
            tracing::error!(
                module = %ctx.module_path,
                watchdog_secs = ctx.watchdog.as_secs(),
                "worker subprocess unresponsive; killing"
            );
            let _ = ctx.child.kill();
            for item in ctx.items.iter().skip(received) {
                let (outcome, duration_ms) = types::TestOutcome::timed_out_sentinel(ctx.watchdog);
                let _ = ctx.tx.send(WorkerResult {
                    resolved: types::ResolvedOutcome {
                        node_id: item.node_id.clone(),
                        duration_ms,
                        outcome,
                    },
                    worker_id: ctx.worker_id,
                });
            }
            false
        }
        DrainOutcome::Disconnected => {
            for item in ctx.items.iter().skip(received) {
                let _ = ctx.tx.send(WorkerResult {
                    resolved: types::ResolvedOutcome {
                        node_id: item.node_id.clone(),
                        duration_ms: types::DurationMs::ZERO,
                        outcome: types::TestOutcome::crashed_sentinel(),
                    },
                    worker_id: ctx.worker_id,
                });
            }
            false
        }
    }
}

/// Processes one result received from a worker channel.
///
/// Returns `Some(outcome)` when the `node_id` maps to a scheduled item and the
/// reporter has been notified.  Returns `None` when the `node_id` is not
/// recognised (worker bug or protocol mismatch); the caller should skip the
/// result in that case.
pub(super) fn handle_worker_result(
    resolved: types::ResolvedOutcome,
    item_lookup: &ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>>,
    rep: &mut dyn reporter::Reporter,
    timings: &mut Vec<types::TestTiming>,
    parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
) -> Option<types::TestOutcome> {
    let item = match item_lookup
        .get(resolved.node_id.as_ref())
        .map(std::sync::Arc::clone)
    {
        Some(item) => item,
        None => {
            tracing::warn!(
                node_id = %resolved.node_id,
                "worker sent unknown node_id — skipping result"
            );
            return None;
        }
    };
    rep.test_started(&item);
    rep.test_completed(&item, &resolved.outcome, resolved.duration_ms, parallel_ctx);
    timings.push(types::TestTiming {
        node_id: resolved.node_id,
        duration_ms: resolved.duration_ms,
        outcome: types::OutcomeKind::from(&resolved.outcome),
    });
    Some(resolved.outcome)
}

/// Drain any groups still queued in `sched` after all workers have exited and
/// emit `TestOutcome::crashed_sentinel` for each item via the reporter.
///
/// Called from `run_phase_parallel` after its result channel is exhausted — at
/// that point every worker thread has already returned (they drop their `tx`
/// clone on exit, which is what causes the channel to close), so no live worker
/// will race us for remaining scheduler entries.
pub(super) fn drain_remaining_into_crashed(
    sched: &scheduler::Scheduler,
    item_lookup: &ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>>,
    rep: &mut dyn reporter::Reporter,
    timings: &mut Vec<types::TestTiming>,
) {
    while let Some(group) = sched.pop() {
        for item in &group.items {
            let resolved = types::ResolvedOutcome {
                node_id: item.node_id.clone(),
                duration_ms: types::DurationMs::ZERO,
                outcome: types::TestOutcome::crashed_sentinel(),
            };
            handle_worker_result(resolved, item_lookup, rep, timings, None);
        }
    }
}

#[cfg(test)]
mod drain_tests {
    use super::*;
    use std::time::Duration;

    fn valid_json(node_id: &str) -> String {
        format!(
            r#"{{"node_id":"{node_id}","outcome":"passed","duration_ms":1.0,"protocol_version":{}}}"#,
            crate::worker_result::PROTOCOL_VERSION
        )
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
        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx, 0);
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

        let (outcome, received) = drain_worker_results(&line_rx, 2, watchdog, &result_tx, 0);
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
            drain_worker_results(&line_rx, 1, Duration::from_millis(500), &result_tx, 0);

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
            drain_worker_results(&line_rx, 4, Duration::from_millis(500), &result_tx, 0);

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
            drain_worker_results(&line_rx, 3, Duration::from_millis(500), &result_tx, 0);

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
        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx, 0);
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
        use std::sync::Arc;

        fn make_test_item(path: &str, fn_name: &str) -> Arc<TestItem> {
            Arc::new(TestItem {
                node_id: NodeId::new(path, fn_name, None),
                fn_name: Arc::from(fn_name),
                lineno: crate::types::LineNo::new(1),
                markers: crate::types::MarkerSet::new(),
                param_id: None,
                param_values: vec![],
                is_async: false,
                fixture_names: vec![],
                fixref_names: vec![],
            })
        }

        let all_items = [
            make_test_item("tests/a.py", "test_alpha"),
            make_test_item("tests/a.py", "test_beta"),
            make_test_item("tests/b.py", "test_gamma"),
        ];

        let groups = vec![
            crate::scheduler::ModuleGroup::new(
                camino::Utf8PathBuf::from("tests/a.py"),
                vec![Arc::clone(&all_items[0]), Arc::clone(&all_items[1])],
            ),
            crate::scheduler::ModuleGroup::new(
                camino::Utf8PathBuf::from("tests/b.py"),
                vec![Arc::clone(&all_items[2])],
            ),
        ];

        let sched = Arc::new(crate::scheduler::Scheduler::new(groups));

        let item_lookup: ahash::AHashMap<NodeId, Arc<TestItem>> = all_items
            .iter()
            .map(|i| (i.node_id.clone(), Arc::clone(i)))
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
                _parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
            ) {
                self.completed
                    .push((item.node_id.to_string(), outcome.as_str().to_string()));
            }
            fn finish(
                &mut self,
                _: &[CollectError],
                _: bool,
                _: &crate::reporter::ReporterSession,
            ) -> crate::reporter::ExitVote {
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
        let item_lookup: ahash::AHashMap<types::NodeId, Arc<TestItem>> = ahash::AHashMap::new();

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
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
                panic!("must not be called on empty scheduler");
            }
            fn finish(
                &mut self,
                _: &[CollectError],
                _: bool,
                _: &crate::reporter::ReporterSession,
            ) -> crate::reporter::ExitVote {
                crate::reporter::ExitVote::Abstain
            }
        }

        let mut rep = NullReporter;
        let mut timings: Vec<crate::types::TestTiming> = vec![];
        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings);
        assert!(timings.is_empty());
    }

    // ── Test 9 ──────────────────────────────────────────────────────────────────
    // Unknown outcome strings fail WireResult deserialization. The drain loop
    // must salvage node_id + duration_ms via WireMinimal and emit an Error sentinel.
    #[test]
    fn unknown_outcome_produces_error_sentinel() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded();
        let (result_tx, result_rx) = crossbeam_channel::unbounded();

        line_tx
            .send(r#"{"node_id":"t","outcome":"completely_made_up","duration_ms":0.5}"#.to_string())
            .unwrap();
        drop(line_tx);

        let (status, received) = drain_worker_results(
            &line_rx,
            1,
            std::time::Duration::from_secs(5),
            &result_tx,
            0,
        );
        assert_eq!(received, 1);
        let result = result_rx.try_recv().expect("should have received a result");
        assert_eq!(result.resolved.node_id.as_ref(), "t");
        assert!(matches!(
            result.resolved.outcome,
            crate::types::TestOutcome::Error(..)
        ));
        let _ = status;
    }

    // ── Test 10 ─────────────────────────────────────────────────────────────────
    // A result with a mismatched protocol_version must still be forwarded (not
    // dropped).  The runner warns about the mismatch but the result is valid.
    #[test]
    fn version_mismatch_still_forwards_result() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded();
        let (result_tx, result_rx) = crossbeam_channel::unbounded();
        // Send a result with protocol_version 99 (mismatch)
        line_tx
            .send(
                r#"{"node_id":"t","outcome":"passed","duration_ms":1.0,"protocol_version":99}"#
                    .to_string(),
            )
            .unwrap();
        drop(line_tx);

        let (outcome, count) =
            drain_worker_results(&line_rx, 1, Duration::from_secs(5), &result_tx, 0);
        assert_eq!(outcome, DrainOutcome::Complete);
        assert_eq!(count, 1);
        // Result must still be forwarded despite version mismatch
        let _r = result_rx.try_recv().expect("result should be forwarded");
    }

    // ── Test 11 ─────────────────────────────────────────────────────────────────
    // Completely invalid JSON (not salvageable via WireMinimal) must still emit
    // an error sentinel so the test is not silently dropped.
    #[test]
    fn completely_malformed_json_emits_error_sentinel() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerResult>();

        line_tx.send("not json at all\n".to_string()).unwrap();
        drop(line_tx);

        let (outcome, received) =
            drain_worker_results(&line_rx, 1, Duration::from_secs(5), &result_tx, 0);

        assert_eq!(
            outcome,
            DrainOutcome::Complete,
            "drain must return Complete when expected count is satisfied by the sentinel"
        );
        assert_eq!(
            received, 1,
            "the malformed line must be counted as received"
        );
        let result = result_rx
            .try_recv()
            .expect("an error sentinel must be sent for completely malformed JSON");
        assert!(
            matches!(
                result.resolved.outcome,
                crate::types::TestOutcome::Error(..)
            ),
            "outcome must be an Error sentinel, got: {:?}",
            result.resolved.outcome
        );
    }
}

#[cfg(test)]
mod result_handler_tests {
    use super::*;
    use crate::worker_result::WireResult;
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
            _: Option<&crate::parallel_context::ParallelContext>,
        ) {
            self.completed += 1;
        }
        fn finish(
            &mut self,
            _: &[types::CollectError],
            _: bool,
            _: &reporter::ReporterSession,
        ) -> reporter::ExitVote {
            reporter::ExitVote::Abstain
        }
    }

    fn make_resolved(node_id: &str) -> types::ResolvedOutcome {
        let json = format!(r#"{{"node_id":"{node_id}","outcome":"passed","duration_ms":0.0}}"#);
        serde_json::from_str::<WireResult>(&json)
            .unwrap()
            .into_outcome()
    }

    fn make_item(node_id: &str) -> Arc<types::TestItem> {
        Arc::new(
            types::TestItem::builder_raw(node_id)
                .module_path("tests/test_mod.py")
                .lineno(1)
                .build(),
        )
    }

    #[test]
    fn unknown_node_id_returns_none_and_skips_reporter() {
        let lookup: ahash::AHashMap<types::NodeId, Arc<types::TestItem>> = ahash::AHashMap::new();
        let mut rep = CountingReporter {
            started: 0,
            completed: 0,
        };
        let mut timings: Vec<types::TestTiming> = vec![];

        let resolved = make_resolved("unknown::test_fn");
        let outcome = handle_worker_result(resolved, &lookup, &mut rep, &mut timings, None);

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
        let mut lookup: ahash::AHashMap<types::NodeId, Arc<types::TestItem>> =
            ahash::AHashMap::new();
        lookup.insert(
            types::NodeId::from_raw("my_mod::test_fn"),
            make_item("my_mod::test_fn"),
        );
        let mut rep = CountingReporter {
            started: 0,
            completed: 0,
        };
        let mut timings: Vec<types::TestTiming> = vec![];

        let resolved = make_resolved("my_mod::test_fn");
        let outcome = handle_worker_result(resolved, &lookup, &mut rep, &mut timings, None);

        assert!(outcome.is_some(), "should return Some for known node_id");
        assert_eq!(rep.started, 1, "reporter.test_started must be called once");
        assert_eq!(
            rep.completed, 1,
            "reporter.test_completed must be called once"
        );
        assert_eq!(timings.len(), 1, "one timing entry must be recorded");
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

        let (outcome, received) = drain_worker_results(&line_rx, 1, watchdog, &result_tx, 0);

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
