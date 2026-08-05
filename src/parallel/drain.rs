//! Draining worker results and handling drain outcomes.
//!
//! Contains the per-result watchdog drain loop, outcome handling for timeouts
//! and disconnects, and the crash-drain logic for unassigned scheduler groups.

use crate::{reporter, scheduler, types, worker_result::WireResult};

use super::{WorkerMessage, WorkerResult};

/// Most lines [`drain_until_eof`] will read from one worker's tail.
///
/// Teardown output is a handful of lines in practice. The cap exists so a
/// worker stuck in a loop that keeps printing cannot hold the coordinator open
/// or grow the reporter's diagnostic bag without bound (#1840).
const MAX_TAIL_LINES: usize = 1024;

/// Forward one test outcome to the coordinator's consumer loop.
///
/// Exists so the call sites read as sending a result rather than as wrapping
/// one in a channel envelope — the envelope is a transport detail (#1840).
fn send_result(tx: &crossbeam_channel::Sender<WorkerMessage>, result: WorkerResult) {
    let _ = tx.send(WorkerMessage::Result(result));
}

/// Parse a wire diagnostic and hand it to the coordinator for reporting.
///
/// Silently drops a line that will not parse: a malformed diagnostic must not
/// fail a run that is otherwise fine, and the result path already has its own
/// salvage-and-report handling for the lines that decide an outcome.
fn forward_diagnostic(trimmed: &str, tx: &crossbeam_channel::Sender<WorkerMessage>) {
    let Ok(wd) = serde_json::from_str::<crate::worker_result::WireDiagnostic>(trimmed) else {
        return;
    };
    let _ = tx.send(WorkerMessage::Diagnostic(
        crate::reporter::stats::DiagnosticEntry::from_wire(
            &wd.severity,
            &wd.context,
            wd.message,
            wd.file,
            wd.lineno,
        ),
    ));
}

/// Handle one worker line that is not a test result.
///
/// Returns `true` when the line was consumed, `false` when the caller should
/// treat it as a result. Shared by both drain loops so the set of recognised
/// message types cannot drift between them (#1840).
fn dispatch_non_result(trimmed: &str, tx: &crossbeam_channel::Sender<WorkerMessage>) -> bool {
    let msg_type = match serde_json::from_str::<crate::worker_result::WireEnvelope>(trimmed) {
        Ok(env) => env.msg_type,
        Err(_) => "result".to_string(), // fallback: assume result
    };
    match msg_type.as_str() {
        "diagnostic" => {
            forward_diagnostic(trimmed, tx);
            true
        }
        "trace" => {
            if let Ok(wt) = serde_json::from_str::<crate::worker_result::WireTrace>(trimmed) {
                match wt.level.as_str() {
                    "debug" => tracing::debug!(module = %wt.module, "{}", wt.message),
                    "info" => tracing::info!(module = %wt.module, "{}", wt.message),
                    "warn" => tracing::warn!(module = %wt.module, "{}", wt.message),
                    "error" => tracing::error!(module = %wt.module, "{}", wt.message),
                    _ => tracing::trace!(module = %wt.module, "{}", wt.message),
                }
            }
            true
        }
        "result" => false,
        other => {
            tracing::warn!(msg_type = %other, "unknown worker message type — skipping");
            true
        }
    }
}

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
    tx: &crossbeam_channel::Sender<WorkerMessage>,
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
                // Dispatch non-result message types first; they don't count
                // toward `received` and are handled inline.
                if dispatch_non_result(trimmed, tx) {
                    result_deadline = Instant::now() + watchdog;
                    continue;
                }

                // Result handling — unchanged from pre-v3 protocol.
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
                        result_deadline = Instant::now() + watchdog;
                        let resolved = r.into_outcome();
                        send_result(
                            tx,
                            WorkerResult {
                                resolved,
                                worker_id,
                            },
                        );
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
                            send_result(
                                tx,
                                WorkerResult {
                                    resolved: types::ResolvedOutcome {
                                        node_id: types::NodeId::from_raw(&minimal.node_id),
                                        duration_ms: types::DurationMs::new(minimal.duration_ms),
                                        outcome: types::TestOutcome::error_sentinel(format!(
                                            "Unknown wire result: {e}"
                                        )),
                                    },
                                    worker_id,
                                },
                            );
                        } else {
                            tracing::warn!(error = %e, output = %trimmed, "bad worker output");
                            send_result(
                                tx,
                                WorkerResult {
                                    resolved: types::ResolvedOutcome {
                                        node_id: types::NodeId::from_raw(
                                            "<worker>::malformed_output",
                                        ),
                                        duration_ms: types::DurationMs::ZERO,
                                        outcome: types::TestOutcome::error_sentinel(format!(
                                            "Malformed worker output (not valid JSON): {e}"
                                        )),
                                    },
                                    worker_id,
                                },
                            );
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

/// Reads a worker's output after its last task group, until stdout closes.
///
/// [`drain_worker_results`] returns the moment it has the results it expects,
/// so anything written after a group's final result line is read only at the
/// head of the *next* group's drain. A worker's final group has no next drain,
/// so its teardown diagnostics and `--keep-tmp` notices were discarded (#1840).
///
/// The deadline is per-line and resets on every non-empty line, matching
/// [`drain_worker_results`]: a silent wedged worker is abandoned one watchdog
/// after it goes quiet, while a worker still emitting is allowed to finish.
/// Empty lines deliberately do not reset it.
///
/// Because the deadline resets per line, a worker looping while emitting valid
/// output would otherwise be read forever — and every diagnostic it produced
/// would accumulate in the reporter. [`MAX_TAIL_LINES`] bounds that; the
/// dispatch loop proper has no such need because `expected` bounds it.
pub(crate) fn drain_until_eof(
    line_rx: &crossbeam_channel::Receiver<String>,
    watchdog: std::time::Duration,
    tx: &crossbeam_channel::Sender<WorkerMessage>,
    worker_id: usize,
) {
    use std::time::Instant;

    let mut deadline = Instant::now() + watchdog;
    let mut lines_read = 0usize;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            tracing::warn!(
                worker_id,
                "worker did not close stdout within the watchdog; abandoning its remaining output"
            );
            return;
        }
        match line_rx.recv_timeout(remaining) {
            Ok(line) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                lines_read += 1;
                if lines_read > MAX_TAIL_LINES {
                    tracing::warn!(
                        worker_id,
                        max = MAX_TAIL_LINES,
                        "worker emitted more than the tail cap after its final task group; \
                         abandoning the rest"
                    );
                    return;
                }
                deadline = Instant::now() + watchdog;
                // A result here belongs to no group the coordinator is still
                // tracking: every group was drained to its expected count
                // before we got here, so counting it would corrupt the tally
                // rather than rescue a test.
                if !dispatch_non_result(trimmed, tx) {
                    tracing::warn!("unexpected result after the final task group — skipping");
                }
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => return,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => return,
        }
    }
}

/// Bundles the mutable and shared state needed by [`handle_drain_outcome`].
pub(crate) struct DrainContext<'a> {
    pub child: &'a mut std::process::Child,
    pub items: &'a [std::sync::Arc<crate::types::TestItem>],
    pub watchdog: std::time::Duration,
    pub module_path: &'a camino::Utf8Path,
    pub tx: &'a crossbeam_channel::Sender<WorkerMessage>,
    pub worker_id: usize,
    /// Names of `lifetime="process"` fixtures, run-constant and empty for every
    /// suite that does not use the tier (#1777). Used only to say what a killed
    /// worker never tore down.
    pub process_fixtures: &'a [String],
}

/// Warn that a worker died holding process-lifetime fixtures.
///
/// A worker owns its process tier alone: no other process will ever run those
/// teardowns, so killing it drops them permanently. That is accepted — decision
/// 3 rejected a graceful SIGTERM as unsound, since a C-level block never
/// reaches the bytecode boundary where the signal becomes a Python exception —
/// but it is not allowed to be silent.
///
/// Emits nothing when the suite declares no such fixtures, which is the common
/// case, and adds no wait to the kill path: it reads a list the coordinator
/// computed before the run began.
///
/// The names are the ones *declared*, not the ones that worker actually built.
/// Only the worker knows which it resolved, and it is dead — asking would mean
/// a round-trip the kill path is not allowed to pay for. The message says so
/// rather than asserting a set it cannot verify.
fn warn_skipped_process_teardowns(ctx: &DrainContext<'_>, cause: &str) {
    let Some(message) = skipped_teardown_message(ctx.worker_id, cause, ctx.process_fixtures) else {
        return;
    };
    let _ = ctx.tx.send(WorkerMessage::Diagnostic(
        crate::reporter::stats::DiagnosticEntry::from_wire(
            "warning",
            "process-lifetime teardown",
            message,
            String::new(),
            0,
        ),
    ));
}

/// The warning text, or `None` when the suite declares no process-lifetime tier.
///
/// Split out of [`warn_skipped_process_teardowns`] so both the decision and the
/// wording are reachable from `cargo test`. The caller needs a live
/// `DrainContext`, which borrows a `std::process::Child` and exists only inside
/// a real worker thread — the same reason `build_task` was lifted out of
/// `run_worker_loop`.
///
/// Returning `None` rather than an empty string keeps "say nothing" a decision
/// this function owns, instead of one the caller has to remember to make.
fn skipped_teardown_message(worker_id: usize, cause: &str, names: &[String]) -> Option<String> {
    if names.is_empty() {
        return None;
    }
    let names = names.join(", ");
    Some(format!(
        "worker {worker_id} was {cause}, so any process-lifetime fixture it \
         had built was never torn down. No other process runs those teardowns. \
         Declared in this suite: {names} — this worker may have built only some \
         of them. Anything they release outside the process, such as a temp \
         directory, a database or a lock, is still held."
    ))
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
            warn_skipped_process_teardowns(ctx, "killed as unresponsive");
            for item in ctx.items.iter().skip(received) {
                let (outcome, duration_ms) = types::TestOutcome::timed_out_sentinel(ctx.watchdog);
                send_result(
                    ctx.tx,
                    WorkerResult {
                        resolved: types::ResolvedOutcome {
                            node_id: item.node_id.clone(),
                            duration_ms,
                            outcome,
                        },
                        worker_id: ctx.worker_id,
                    },
                );
            }
            false
        }
        DrainOutcome::Disconnected => {
            warn_skipped_process_teardowns(ctx, "lost before it finished");
            for item in ctx.items.iter().skip(received) {
                send_result(
                    ctx.tx,
                    WorkerResult {
                        resolved: types::ResolvedOutcome {
                            node_id: item.node_id.clone(),
                            duration_ms: types::DurationMs::ZERO,
                            outcome: types::TestOutcome::crashed_sentinel(),
                        },
                        worker_id: ctx.worker_id,
                    },
                );
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
        for item in group.items() {
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
mod skipped_teardown_message_tests {
    use super::skipped_teardown_message;

    fn names(entries: &[&str]) -> Vec<String> {
        entries.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn a_suite_without_the_tier_says_nothing() {
        let message = skipped_teardown_message(0, "killed as unresponsive", &[]);

        assert!(
            message.is_none(),
            "a suite declaring no process-lifetime fixtures must produce no \
             warning at all — an unconditional message would tell every run \
             that ever loses a worker about a tier it does not use, got {message:?}"
        );
    }

    #[test]
    fn the_message_names_the_worker_and_every_declared_fixture() {
        let message = skipped_teardown_message(
            3,
            "lost before it finished",
            &names(&["cachedir", "dbpool"]),
        )
        .expect("a suite declaring the tier must produce a warning");

        assert!(
            message.contains("worker 3"),
            "the warning must identify which worker died, or a run with eight \
             of them gives the user nowhere to look: {message}"
        );
        for fixture in ["cachedir", "dbpool"] {
            assert!(
                message.contains(fixture),
                "the warning must name {fixture}, since 'some fixtures leaked' \
                 is not something a user can act on: {message}"
            );
        }
        assert!(
            message.contains("lost before it finished"),
            "the cause distinguishes a watchdog kill from a crash, and the two \
             call for different investigations: {message}"
        );
    }

    #[test]
    fn the_message_does_not_claim_the_worker_built_them_all() {
        let message = skipped_teardown_message(1, "killed as unresponsive", &names(&["pool"]))
            .expect("a suite declaring the tier must produce a warning");

        assert!(
            message.contains("may have built only some of them"),
            "the list is what the suite *declares*; only the worker knows what \
             it resolved and it is dead, so asking would cost the round-trip \
             the kill path may not pay for. A message asserting the worker \
             built all of them states something nothing verified: {message}"
        );
    }
}

#[cfg(test)]
mod drain_tests {
    use super::*;
    use std::time::Duration;

    /// Unwrap a channel message the test expects to be a result.
    ///
    /// Panics on a diagnostic rather than silently skipping it: a test that
    /// asserts on an outcome and instead receives a diagnostic has found a
    /// real dispatch bug, and swallowing it would hide exactly that.
    fn expect_result(message: WorkerMessage) -> WorkerResult {
        match message {
            WorkerMessage::Result(result) => result,
            WorkerMessage::Diagnostic(entry) => {
                panic!("expected a result message, got a diagnostic: {entry:?}")
            }
        }
    }

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
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
                fixture_deps: vec![],
                fixref_deps: vec![],
                arranged: vec![],
            })
        }

        let all_items = [
            make_test_item("tests/a.py", "test_alpha"),
            make_test_item("tests/a.py", "test_beta"),
            make_test_item("tests/b.py", "test_gamma"),
        ];

        let groups = vec![
            crate::scheduler::TaskGroup::single(crate::scheduler::ModuleGroup::new(
                camino::Utf8PathBuf::from("tests/a.py"),
                vec![Arc::clone(&all_items[0]), Arc::clone(&all_items[1])],
            )),
            crate::scheduler::TaskGroup::single(crate::scheduler::ModuleGroup::new(
                camino::Utf8PathBuf::from("tests/b.py"),
                vec![Arc::clone(&all_items[2])],
            )),
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
        let result = expect_result(result_rx.try_recv().expect("should have received a result"));
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

    // ── Test 12 ─────────────────────────────────────────────────────────────────
    // The tail reader must stop when the worker closes stdout, not hang.
    #[test]
    fn drain_until_eof_returns_on_disconnect() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        line_tx
            .send(
                r#"{"type":"diagnostic","severity":"warning","context":"fixture teardown","message":"boom"}"#
                    .to_string(),
            )
            .unwrap();
        drop(line_tx); // worker exited → stdout closed

        let start = std::time::Instant::now();
        drain_until_eof(&line_rx, Duration::from_secs(30), &result_tx, 0);

        assert!(
            start.elapsed() < Duration::from_secs(1),
            "EOF is a real termination signal; waiting for the watchdog instead \
             would add the full deadline to every clean shutdown"
        );
        assert_eq!(
            result_rx.len(),
            1,
            "the diagnostic emitted after the final result must be forwarded — \
             that is the entire defect #1840 describes"
        );
    }

    // ── Test 13 ─────────────────────────────────────────────────────────────────
    // A wedged worker that never closes stdout must not hang the run.
    #[test]
    fn drain_until_eof_gives_up_after_the_deadline() {
        let (_line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        let watchdog = Duration::from_millis(50);
        let start = std::time::Instant::now();
        drain_until_eof(&line_rx, watchdog, &result_tx, 0);
        let elapsed = start.elapsed();

        assert!(
            elapsed >= watchdog,
            "giving up before the deadline would truncate a slow teardown"
        );
        assert!(
            elapsed < watchdog * 5,
            "a worker holding stdout open must not hold the whole run open"
        );
    }

    // ── Test 14 ─────────────────────────────────────────────────────────────────
    // Empty lines must not hold the tail read open indefinitely.
    #[test]
    fn drain_until_eof_empty_lines_do_not_reset_the_deadline() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        std::thread::spawn(move || {
            let stop = std::time::Instant::now() + Duration::from_millis(400);
            while std::time::Instant::now() < stop {
                if line_tx.send("\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let start = std::time::Instant::now();
        drain_until_eof(&line_rx, Duration::from_millis(50), &result_tx, 0);

        assert!(
            start.elapsed() <= Duration::from_millis(200),
            "blank output must not reset the deadline, or a spamming worker \
             keeps the coordinator reading forever"
        );
    }

    // ── Test 15 ─────────────────────────────────────────────────────────────────
    // A worker that keeps emitting valid output must not be read forever. Each
    // non-empty line resets the deadline, so without a cap the coordinator would
    // never return and every diagnostic would accumulate in the reporter.
    #[test]
    fn drain_until_eof_stops_at_the_line_cap() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        // A worker stuck in a loop: never closes stdout, never goes quiet.
        std::thread::spawn(move || {
            let line =
                r#"{"type":"diagnostic","severity":"notice","context":"spam","message":"x"}"#;
            while line_tx.send(format!("{line}\n")).is_ok() {}
        });

        drain_until_eof(&line_rx, Duration::from_secs(30), &result_tx, 0);

        assert!(
            result_rx.len() <= MAX_TAIL_LINES,
            "the tail read must stop at the cap; an unbounded reader would grow \
             the diagnostic bag until the run ran out of memory, and would never \
             return because every line resets the deadline"
        );
    }

    // ── Test 11 ─────────────────────────────────────────────────────────────────
    // Completely invalid JSON (not salvageable via WireMinimal) must still emit
    // an error sentinel so the test is not silently dropped.
    #[test]
    fn completely_malformed_json_emits_error_sentinel() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
        let result = expect_result(
            result_rx
                .try_recv()
                .expect("an error sentinel must be sent for completely malformed JSON"),
        );
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
    /// The old inline loop called `recv_timeout(watchdog)` fresh on every iteration,
    /// so continuous empty lines reset the full timer indefinitely.
    ///
    /// `drain_worker_results()` fixes this by tracking a `result_deadline` that is
    /// only reset when a real result line is received.
    ///
    /// Expected (bug present):  loop spins for ~300ms (spammer duration).
    /// Expected (bug fixed):    `drain_worker_results` exits via `TimedOut` within ~50ms.
    #[test]
    fn bug44_empty_lines_spin_without_progress() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

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
