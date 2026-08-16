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

/// Slack allowed above a watchdog before a test calls it "never fired".
///
/// Absolute rather than a multiple of the watchdog: a multiple shrinks the
/// tolerance precisely when the watchdog is small, so the faster the test the
/// tighter its margin. That is backwards, and it is what turned the required
/// jobs red with no code change — 5x of a 30ms watchdog is 150ms, against
/// 163.5ms actually observed on a loaded runner (#1962).
#[cfg(test)]
const JITTER_SLACK: std::time::Duration = std::time::Duration::from_secs(2);

/// How long a simulated wedged worker keeps spamming.
///
/// Must exceed any ceiling asserted against it, or the test cannot tell "the
/// watchdog fired" from "the spammer ran out". Costs nothing on a passing run:
/// the sender breaks as soon as the receiver drops at end of test (#1962).
#[cfg(test)]
const SPAM_WINDOW: std::time::Duration = std::time::Duration::from_secs(10);

/// The watchdog every spammer test runs under.
///
/// A constant rather than a local per test, because it is one of the two terms
/// of the ceiling those tests assert — `SPAMMER_WATCHDOG + JITTER_SLACK` — and the
/// invariant below cannot see a local. A test that raises its own watchdog past
/// the spam window would otherwise pass vacuously, which is the exact defect
/// #1962 fixed and this constant stops from returning (#2112).
///
/// Only the spammer tests use it. The other watchdogs in this file are locals
/// on purpose: they assert no ceiling against `SPAM_WINDOW`, so they do not
/// depend on the invariant below and must not be bound to it.
#[cfg(test)]
const SPAMMER_WATCHDOG: std::time::Duration = std::time::Duration::from_millis(50);

// The relationship above is what makes the spammer tests mean anything: if the
// window ever drops below the ceiling they assert, they pass because the
// spammer ran out rather than because the watchdog fired, and nothing says so.
// A test that silently stops testing its own name is the defect #1962 exists to
// fix, so this invariant is checked rather than described.
//
// The ceiling is `SPAMMER_WATCHDOG + JITTER_SLACK`, not `JITTER_SLACK` alone: the
// watchdog has to fire before the test can measure the slack after it. Checking
// only the slack term made the message's "any ceiling" a claim the check did
// not cover, and the gap grew with every millisecond added to the watchdog
// (#2112).
#[cfg(test)]
const _: () = assert!(
    SPAM_WINDOW.as_millis() > SPAMMER_WATCHDOG.as_millis() + JITTER_SLACK.as_millis(),
    "SPAM_WINDOW must outlast any ceiling asserted against it, or the spammer \
     tests pass vacuously"
);

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

/// What one line on a worker's stdout is.
///
/// The `"type"` discriminator is the wire protocol's membership test (#2143).
/// A line without it is not the worker answering, whatever else it is.
enum LineKind {
    /// Handled here — a diagnostic, a trace, or a type this coordinator does
    /// not know. Counts toward no result slot.
    Consumed,
    /// Protocol traffic that claims to be a test result. The caller parses it.
    Result,
    /// Not protocol traffic: a test, a C extension, or an uncaptured child
    /// writing to fd 1, which is the same pipe. Logged and dropped.
    NonProtocol,
}

/// Classify one worker line.
///
/// Shared by both drain loops so the set of recognised message types cannot
/// drift between them (#1840), and so one function decides what protocol
/// traffic is (#2143). It also logs the `NonProtocol` verdict, so the two
/// callers hold only the control flow that differs between them.
fn classify_line(trimmed: &str, tx: &crossbeam_channel::Sender<WorkerMessage>) -> LineKind {
    let Ok(env) = serde_json::from_str::<crate::worker_result::WireEnvelope>(trimmed) else {
        tracing::warn!(
            output = %trimmed,
            "ignoring non-protocol line on worker stdout"
        );
        return LineKind::NonProtocol;
    };
    match env.msg_type.as_str() {
        "diagnostic" => {
            forward_diagnostic(trimmed, tx);
            LineKind::Consumed
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
            LineKind::Consumed
        }
        "result" => LineKind::Result,
        other => {
            tracing::warn!(msg_type = %other, "unknown worker message type — skipping");
            LineKind::Consumed
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum DrainOutcome {
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
pub fn drain_worker_results(
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
                // Classify first. Only a line that claims to be a result can
                // count toward `received`.
                match classify_line(trimmed, tx) {
                    LineKind::Consumed => {
                        result_deadline = Instant::now() + watchdog;
                        continue;
                    }
                    // Not the worker answering, so it counts toward nothing and
                    // it does not prove liveness. A test looping on `print(1)`
                    // would otherwise hold the drain open — the same shape
                    // `non_protocol_lines_spin_without_progress` pins for text
                    // (#2010, #2143). `docs/internals/src/worker-protocol.md`
                    // carries the full classification and what this costs.
                    // `classify_line` has already logged it.
                    LineKind::NonProtocol => continue,
                    LineKind::Result => {}
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
                        // The line said `"type":"result"` and does not parse as
                        // one, so it is a worker defect rather than stray
                        // output. `classify_line` already dropped every line
                        // that is not protocol traffic (#2010, #2143), so this
                        // arm counts unconditionally: refusing the slot would
                        // leave the drain waiting for an answer that never
                        // comes, until the watchdog killed a healthy worker.
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
                                            "Malformed worker result (JSON, but not a result): {e}"
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
pub fn drain_until_eof(
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
                match classify_line(trimmed, tx) {
                    // Both are already handled: a consumed line by
                    // `classify_line`, a non-protocol line by its log there.
                    LineKind::Consumed | LineKind::NonProtocol => {}
                    LineKind::Result => {
                        tracing::warn!("unexpected result after the final task group — skipping");
                    }
                }
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => return,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => return,
        }
    }
}

/// Bundles the mutable and shared state needed by [`handle_drain_outcome`].
pub struct DrainContext<'a> {
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
pub fn handle_drain_outcome(
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

/// Emit `TestOutcome::crashed_sentinel` for every item still queued in `sched`,
/// **unless the coordinator stopped the phase on purpose**.
///
/// `interrupted` is why `run_phase_parallel` stopped reading results, and it
/// selects between two states that are otherwise indistinguishable here:
///
/// * `false` — the result channel closed, so every worker thread has already
///   returned (they drop their `tx` clone on exit, which is what causes the
///   channel to close). A group still in the scheduler was never popped because
///   no worker was left alive to pop it, and every item in it is a real loss.
/// * `true` — `--maxfail` or `-x` broke the loop. The workers are still live,
///   `cancelled` stops them a moment later, and a group still in the scheduler
///   simply did not run. A test that did not run has no outcome, and the serial
///   path reports nothing for it (#2142).
///
/// The decision lives here rather than at the call site because
/// `run_phase_parallel` spawns real subprocesses, so no unit test can reach it.
/// At the call site the branch that preserves the sentinels would be
/// unobservable, and a mutant deleting it would survive the whole suite.
pub(super) fn drain_remaining_into_crashed(
    sched: &scheduler::Scheduler,
    item_lookup: &ahash::AHashMap<types::NodeId, std::sync::Arc<types::TestItem>>,
    rep: &mut dyn reporter::Reporter,
    timings: &mut Vec<types::TestTiming>,
    interrupted: bool,
) {
    if interrupted {
        return;
    }
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
            r#"{{"type":"result","node_id":"{node_id}","outcome":"passed","duration_ms":1.0,"protocol_version":{}}}"#,
            crate::worker_result::PROTOCOL_VERSION
        )
    }

    // ── Test 1 ──────────────────────────────────────────────────────────────
    // Empty lines are consumed without counting as results, and a disconnect
    // after them is reported as a disconnect.
    //
    // This test does NOT pin "empty lines do not reset the deadline", despite
    // the name it used to carry. Every line is queued and the sender dropped
    // before the call, so `recv_timeout` never waits and the drain ends in
    // ~0ms whether the deadline resets or not — the old comment's "50ms × 50 =
    // 2500ms" hang was not reachable through a pre-queued channel.
    //
    // Measured, not reasoned: mutating the empty-line branch to reset the
    // deadline leaves this test green and fails exactly one test,
    // `bug44_empty_lines_spin_without_progress`, which is the real pin for
    // that rule (#1962).
    #[test]
    fn empty_lines_are_skipped_and_disconnect_is_reported() {
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

        // A hang guard, not a deadline assertion: with every line queued up
        // front and the sender already dropped, this completes in ~0ms, so the
        // ceiling only catches a drain that stops making progress altogether.
        assert!(
            elapsed < JITTER_SLACK,
            "drain took {elapsed:?}; a queued, already-disconnected channel \
             must never block"
        );
        assert_eq!(received, 0, "no real results were sent");
        assert!(
            matches!(outcome, DrainOutcome::Disconnected),
            "expected Disconnected, got {outcome:?}"
        );
    }

    // ── Test 2 ──────────────────────────────────────────────────────────────
    // Valid results reset the deadline so a slow-but-alive worker isn't killed.
    //
    // Both properties this test needs come from its shape (#1962):
    //
    //   pass — every gap (400ms) is under the watchdog (800ms), leaving 400ms
    //          of slack for runner jitter;
    //   kill — the run outlives a deadline that never resets, because the last
    //          result lands at 1200ms against a fixed 800ms deadline.
    //
    // One gap cannot give both: "every gap < watchdog" and "total > watchdog"
    // are contradictory at n=1, which is why the earlier two-result version of
    // this test still passed with the reset deleted.
    #[test]
    fn valid_result_resets_deadline() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        let watchdog = Duration::from_millis(800);
        let gap = watchdog / 2;

        // The gaps go between sends, never after the last one: a trailing
        // sleep would add `gap` to the join below and prove nothing.
        let handle = std::thread::spawn(move || {
            line_tx.send(valid_json("t::a")).unwrap();
            for node_id in ["t::b", "t::c", "t::d"] {
                std::thread::sleep(gap);
                line_tx.send(valid_json(node_id)).unwrap();
            }
        });

        let (outcome, received) = drain_worker_results(&line_rx, 4, watchdog, &result_tx, 0);
        handle.join().unwrap();

        assert_eq!(
            outcome,
            DrainOutcome::Complete,
            "every gap is under the watchdog, so each result must reset the \
             deadline and the worker must never be declared timed out"
        );
        assert_eq!(
            received, 4,
            "all four results were sent before any deadline"
        );
        assert_eq!(
            result_rx.len(),
            4,
            "all four results must have been forwarded"
        );
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
            elapsed < watchdog + JITTER_SLACK,
            "fired too late ({elapsed:?}); watchdog is {watchdog:?}"
        );
    }

    /// One `TestItem` with nothing but an identity.
    ///
    /// Shared by the scheduler-drain tests, which care only about which items
    /// reach the reporter and never about a marker, a parameter or a fixture.
    fn make_test_item(path: &str, fn_name: &str) -> std::sync::Arc<types::TestItem> {
        std::sync::Arc::new(types::TestItem {
            node_id: types::NodeId::new(path, fn_name, None),
            fn_name: std::sync::Arc::from(fn_name),
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

        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings, false);

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
        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings, false);
        assert!(timings.is_empty());
    }

    // ── #2142 ───────────────────────────────────────────────────────────────────
    // Two states used to share one code path and one message: every worker died,
    // and the coordinator stopped on purpose. On the `--maxfail` break the
    // workers are still live and the scheduler still holds every group nobody
    // popped. Those tests did not run, and a test that did not run has no
    // outcome — the serial path reports nothing for them.
    //
    // Measured before the fix on a 60-item suite at `-n 4`, 5 of 5 runs:
    // `1 failed · 36 errors`, every error reading "Worker subprocess exited
    // unexpectedly", against `1 failed · 18 passed` for the same suite serially.
    //
    // The scheduler here is NOT empty, which is what separates this test from
    // `drain_remaining_into_crashed_is_noop_on_empty_scheduler`: that one passes
    // whatever `interrupted` does, because there is nothing to drain either way.
    #[test]
    fn an_interrupted_phase_reports_nothing_for_an_undispatched_item() {
        use crate::types::{CollectError, NodeId, TestItem};
        use std::sync::Arc;

        let item = make_test_item("tests/a.py", "test_never_dispatched");
        let sched = Arc::new(crate::scheduler::Scheduler::new(vec![
            crate::scheduler::TaskGroup::single(crate::scheduler::ModuleGroup::new(
                camino::Utf8PathBuf::from("tests/a.py"),
                vec![Arc::clone(&item)],
            )),
        ]));
        let item_lookup: ahash::AHashMap<NodeId, Arc<TestItem>> =
            std::iter::once((item.node_id.clone(), Arc::clone(&item))).collect();

        struct RefusingReporter;
        impl crate::reporter::Reporter for RefusingReporter {
            fn test_started(&mut self, item: &TestItem) {
                panic!(
                    "the coordinator stopped on purpose, so {} never ran and must \
                     reach no reporter",
                    item.node_id
                );
            }
            fn test_completed(
                &mut self,
                item: &TestItem,
                outcome: &crate::types::TestOutcome,
                _: types::DurationMs,
                _: Option<&crate::parallel_context::ParallelContext>,
            ) {
                panic!(
                    "{} was never dispatched, so reporting it as {} says a worker \
                     died when none did",
                    item.node_id,
                    outcome.as_str()
                );
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

        let mut rep = RefusingReporter;
        let mut timings: Vec<crate::types::TestTiming> = vec![];

        drain_remaining_into_crashed(&sched, &item_lookup, &mut rep, &mut timings, true);

        assert!(
            timings.is_empty(),
            "a test the coordinator never dispatched did not run, so it has no \
             timing; a row here is the phantom error #2142 reports, and it also \
             reaches the CTRF report"
        );
    }

    // ── Test 9 ──────────────────────────────────────────────────────────────────
    // Unknown outcome strings fail WireResult deserialization. The drain loop
    // must salvage node_id + duration_ms via WireMinimal and emit an Error sentinel.
    #[test]
    fn unknown_outcome_produces_error_sentinel() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded();
        let (result_tx, result_rx) = crossbeam_channel::unbounded();

        line_tx
            .send(
                r#"{"type":"result","node_id":"t","outcome":"completely_made_up","duration_ms":0.5}"#
                    .to_string(),
            )
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
                r#"{"type":"result","node_id":"t","outcome":"passed","duration_ms":1.0,"protocol_version":99}"#
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
            elapsed < watchdog + JITTER_SLACK,
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
            let stop = std::time::Instant::now() + SPAM_WINDOW;
            while std::time::Instant::now() < stop {
                if line_tx.send("\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let start = std::time::Instant::now();
        drain_until_eof(&line_rx, SPAMMER_WATCHDOG, &result_tx, 0);

        assert!(
            start.elapsed() <= SPAMMER_WATCHDOG + JITTER_SLACK,
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
    // A line that claims `"type":"result"` and is not salvageable via
    // WireMinimal must still emit an error sentinel, so the test is not
    // silently dropped.
    //
    // This test used to send `"not json at all"` and assert the same thing.
    // That was the contract #2010 removed: a line which is not JSON at all is
    // not the worker answering, so counting it deleted the answer. It then sent
    // `{"unexpected": true}`, which #2143 removed for the same reason — JSON
    // without the discriminator is not the worker answering either. The salvage
    // path is kept for the case it was written for: a worker that emitted
    // something structured and wrong, which is what the discriminator now
    // identifies.
    #[test]
    fn json_that_is_not_a_result_emits_error_sentinel() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        line_tx
            .send("{\"type\":\"result\",\"unexpected\":true}\n".to_string())
            .unwrap();
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
            "a malformed result is still the worker's answer for that test; \
             refusing the slot would leave the drain waiting for a second answer \
             that never comes, until the watchdog kills a healthy worker"
        );
        let result = expect_result(
            result_rx
                .try_recv()
                .expect("an error sentinel must be sent for JSON that is not a result"),
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

    // ── #2010 ───────────────────────────────────────────────────────────────────
    // A worker's stdout is the protocol pipe, and a test can write to fd 1.
    // A line that is not protocol traffic must not consume a result slot: it
    // used to, so the real result arrived after the drain had already returned
    // Complete and was discarded as "unexpected result after the final task
    // group". One stray print() deleted one passing test, and the run exited 0.
    #[test]
    fn non_protocol_line_does_not_consume_a_result_slot() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        line_tx.send("hello from a test\n".to_string()).unwrap();
        line_tx.send(valid_json("t::a")).unwrap();
        drop(line_tx);

        let (outcome, received) =
            drain_worker_results(&line_rx, 1, Duration::from_secs(5), &result_tx, 0);

        assert_eq!(
            outcome,
            DrainOutcome::Complete,
            "the worker sent the one result it owed, so the drain must complete \
             rather than spend the slot on the stray line and time out"
        );
        assert_eq!(
            received, 1,
            "only the real result counts — counting the stray line too is what \
             discarded the real one"
        );
        let messages: Vec<WorkerMessage> = result_rx.try_iter().collect();
        assert_eq!(
            messages.len(),
            1,
            "the stray line must produce no result of its own, or one passing \
             test is reported twice — once real, once as malformed output"
        );
        // Asserting the node id, not just the count. Counting the stray line
        // also yields exactly one message — the `<worker>::malformed_output`
        // sentinel — so every assertion above passes with the fix reverted.
        // Measured: a mutant that restores the old arm left this test green and
        // was caught only by the end-to-end suite.
        let result = expect_result(messages.into_iter().next().expect("one message"));
        assert_eq!(
            result.resolved.node_id.to_string(),
            "t::a",
            "the surviving result must be the worker's real one. A \
             `<worker>::malformed_output` sentinel here means the stray line \
             was counted and the real result was discarded behind it"
        );
    }

    // ── #2143 ───────────────────────────────────────────────────────────────
    // A Test Item writes to the same pipe the worker writes results to. A line
    // that carries no `"type"` field is not the worker answering, whatever else
    // it is. It used to count, so `print(json.dumps(...))` in one Test Item
    // deleted another Test Item's result and the run exited 0.
    //
    // Two shapes at least, because a one-sided fix passes with one of them.
    // `42` is not a JSON object, so `WireEnvelope` deserialization fails and
    // the fallback decided. `{}` IS a JSON object, so the field default
    // decided. Both must be dropped.
    #[test]
    fn json_without_a_type_field_does_not_consume_a_result_slot() {
        for stray in ["42", "{}", "[1, 2]", r#"{"user": 1}"#, "true"] {
            let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
            let (result_tx, result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

            line_tx.send(format!("{stray}\n")).unwrap();
            line_tx.send(valid_json("t::a")).unwrap();
            drop(line_tx);

            let (outcome, received) =
                drain_worker_results(&line_rx, 1, Duration::from_secs(5), &result_tx, 0);

            assert_eq!(
                outcome,
                DrainOutcome::Complete,
                "the worker sent the one result it owed, so the drain must \
                 complete; `{stray}` filled the slot instead"
            );
            assert_eq!(
                received, 1,
                "only the real result counts — counting `{stray}` too is what \
                 discarded the real one"
            );
            let messages: Vec<WorkerMessage> = result_rx.try_iter().collect();
            let result = expect_result(
                messages
                    .into_iter()
                    .next()
                    .expect("the real result must survive"),
            );
            assert_eq!(
                result.resolved.node_id.to_string(),
                "t::a",
                "the surviving result must be the worker's real one. A \
                 `<worker>::malformed_output` sentinel here means `{stray}` was \
                 counted and the real result was discarded behind it"
            );
        }
    }

    // Once a JSON line stopped counting toward `expected`, nothing else bounds
    // this loop. A Test Item looping on `print(1)` is the same shape as one
    // looping on `print("x")`, so one rule must cover both — see
    // `non_protocol_lines_spin_without_progress`, which pins the text half.
    #[test]
    fn json_without_a_type_field_does_not_reset_the_deadline() {
        const SPAM_WINDOW: Duration = Duration::from_millis(600);
        const WATCHDOG: Duration = Duration::from_millis(200);
        const JITTER_SLACK: Duration = Duration::from_millis(400);

        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        std::thread::spawn(move || {
            let stop = std::time::Instant::now() + SPAM_WINDOW;
            while std::time::Instant::now() < stop {
                if line_tx.send("42\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let start = std::time::Instant::now();
        let (outcome, received) = drain_worker_results(&line_rx, 1, WATCHDOG, &result_tx, 0);

        assert_eq!(
            outcome,
            DrainOutcome::TimedOut,
            "a worker that answers nothing must time out, whatever else it writes"
        );
        assert_eq!(
            received, 0,
            "a line with no type discriminator is not an answer, so it counts \
             toward nothing"
        );
        assert!(
            start.elapsed() <= WATCHDOG + JITTER_SLACK,
            "a JSON line must not prove liveness, or a spamming test keeps the \
             coordinator reading forever"
        );
    }

    // Once a non-protocol line stopped counting toward `expected`, nothing else
    // bounded this loop: `drain_until_eof` carries MAX_TAIL_LINES for exactly
    // this shape, and its doc comment says the dispatch loop "has no such need
    // because `expected` bounds it" — which #2010 made false. A test looping on
    // print() held the drain open past 45s and 4.79M lines before the deadline
    // stopped being reset for these lines.
    //
    // Same shape as bug44_empty_lines_spin_without_progress, and for the same
    // reason: a line that is not the worker answering must not prove liveness.
    #[test]
    fn non_protocol_lines_spin_without_progress() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        let handle = std::thread::spawn(move || {
            let stop = std::time::Instant::now() + SPAM_WINDOW;
            while std::time::Instant::now() < stop {
                if line_tx.send("spam\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let start = std::time::Instant::now();

        let (outcome, received) =
            drain_worker_results(&line_rx, 1, SPAMMER_WATCHDOG, &result_tx, 0);

        let elapsed = start.elapsed();
        assert_eq!(received, 0, "no real result was ever sent");
        assert!(
            matches!(outcome, DrainOutcome::TimedOut),
            "a worker emitting only non-protocol lines has answered nothing, so \
             the watchdog must fire. Got {outcome:?}"
        );
        assert!(
            elapsed <= SPAMMER_WATCHDOG + JITTER_SLACK,
            "the watchdog did not fire within {:?}; elapsed={elapsed:?}. \
             Non-protocol lines are resetting the deadline, which holds the \
             drain open for as long as a test keeps printing.",
            SPAMMER_WATCHDOG + JITTER_SLACK
        );
        drop(handle);
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
    use std::time::Instant;

    /// Regression test for bug #44: a subprocess spamming empty lines must not
    /// prevent the watchdog from firing.
    ///
    /// The old inline loop called `recv_timeout(watchdog)` fresh on every iteration,
    /// so continuous empty lines reset the full timer indefinitely.
    ///
    /// `drain_worker_results()` fixes this by tracking a `result_deadline` that is
    /// only reset when a real result line is received.
    ///
    /// Expected (bug present):  loop spins for the whole spam window.
    /// Expected (bug fixed):    `drain_worker_results` exits via `TimedOut` within ~50ms.
    #[test]
    fn bug44_empty_lines_spin_without_progress() {
        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let (result_tx, _result_rx) = crossbeam_channel::unbounded::<WorkerMessage>();

        // Thread that sends empty lines — simulates a panicked Python
        // subprocess continuously flushing its stdout buffer. It stops on its
        // own when the receiver drops at end of test.
        std::thread::spawn(move || {
            let stop = Instant::now() + SPAM_WINDOW;
            while Instant::now() < stop {
                if line_tx.send("\n".to_string()).is_err() {
                    break;
                }
            }
        });

        let start = Instant::now();

        let (outcome, received) =
            drain_worker_results(&line_rx, 1, SPAMMER_WATCHDOG, &result_tx, 0);

        let elapsed = start.elapsed();
        assert_eq!(received, 0, "no real results were sent");
        assert!(
            matches!(outcome, DrainOutcome::TimedOut),
            "expected TimedOut, got {outcome:?}"
        );
        // Fails with the bug (elapsed ≈ the spam window); passes after the fix
        // (elapsed ≈ the watchdog).
        assert!(
            elapsed <= SPAMMER_WATCHDOG + JITTER_SLACK,
            "BUG #44: watchdog did not fire within {:?}; elapsed={elapsed:?}. \
             Empty lines are resetting the recv_timeout timer.",
            SPAMMER_WATCHDOG + JITTER_SLACK
        );
    }
}
