//! Worker session management — subprocess lifecycle and I/O helpers.
//!
//! Contains the low-level plumbing for spawning a worker subprocess, wiring up
//! its stdin/stdout, and the [`WorkerSession`] struct that bundles communication
//! state for a single worker thread.

/// Maximum length of a single worker stdout line (10 MB).
///
/// Normal test results are <10 KB. A line exceeding this limit indicates
/// a misbehaving worker (e.g., printing large data to stdout). The line
/// is truncated and reported as an error.
const MAX_LINE_LENGTH: usize = 10 * 1024 * 1024;

use crate::{
    parallel::{
        DrainContext, DrainOutcome, drain_until_eof, drain_worker_results, handle_drain_outcome,
    },
    scheduler, types,
    worker_result::{WorkerTask, WorkerTaskItem, WorkerTaskModule},
};

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
pub(crate) fn setup_worker_process(
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
    let (line_tx, line_rx) = crossbeam_channel::bounded::<String>(1024);
    // Note: this thread is intentionally detached. It exits naturally when:
    // 1. The worker process exits → stdout closes → read_line() returns Ok(0)
    // 2. The channel receiver is dropped → send() returns Err → loop breaks
    // The thread cannot outlive the worker process or the channel, so no
    // explicit join is needed.
    std::thread::spawn(move || {
        let mut stdout = worker_stdout;
        let mut buf = String::with_capacity(256);
        loop {
            buf.clear();
            match stdout.read_line(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    if buf.len() > MAX_LINE_LENGTH {
                        tracing::warn!(
                            len = buf.len(),
                            "Worker stdout line exceeds {} bytes — truncating",
                            MAX_LINE_LENGTH
                        );
                        buf.truncate(MAX_LINE_LENGTH);
                    }
                    if line_tx.send(buf.clone()).is_err() {
                        break;
                    }
                }
            }
        }
    });

    Some((child, worker_stdin, line_rx))
}

/// Bundles subprocess communication state for a single worker.
///
/// Encapsulates the stdin writer, stdout line receiver, and watchdog duration
/// so that `spawn_worker()` can express the task dispatch loop in terms of
/// `send_task()` / `drain_results()` instead of juggling three locals.
struct WorkerSession {
    /// `None` once closed. Closing stdin is what tells the worker to begin its
    /// teardown, and it must happen without dropping `line_rx` — the tail that
    /// teardown writes is read through it (#1840).
    stdin: Option<std::io::BufWriter<std::process::ChildStdin>>,
    line_rx: crossbeam_channel::Receiver<String>,
    watchdog: std::time::Duration,
}

impl WorkerSession {
    fn new(
        stdin: std::io::BufWriter<std::process::ChildStdin>,
        line_rx: crossbeam_channel::Receiver<String>,
        watchdog: std::time::Duration,
    ) -> Self {
        Self {
            stdin: Some(stdin),
            line_rx,
            watchdog,
        }
    }

    /// Serialize `task` as a compact JSON line and flush it to the worker's stdin.
    ///
    /// The worker blocks on `sys.stdin.readline()`, so the newline terminator and
    /// flush are both required for the message to be delivered immediately.
    fn send_task(&mut self, task: &WorkerTask<'_>) -> std::io::Result<()> {
        use std::io::Write;
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| std::io::Error::other("worker stdin is already closed"))?;
        serde_json::to_writer(&mut *stdin, task).map_err(std::io::Error::other)?;
        writeln!(stdin)?;
        stdin.flush()
    }

    /// Close the worker's stdin, leaving `line_rx` alive.
    ///
    /// Dropping the whole session would drop the receiver too, which ends the
    /// reader thread and discards the very output we are about to read.
    fn close_stdin(&mut self) {
        self.stdin = None;
    }

    /// Read whatever the worker writes between stdin closing and process exit.
    fn drain_tail(
        &self,
        tx: &crossbeam_channel::Sender<crate::parallel::WorkerMessage>,
        worker_id: usize,
    ) {
        drain_until_eof(&self.line_rx, self.watchdog, tx, worker_id);
    }

    /// Drains up to `expected` result lines from the worker, forwarding each to `tx`.
    fn drain_results(
        &self,
        expected: usize,
        tx: &crossbeam_channel::Sender<crate::parallel::WorkerMessage>,
        worker_id: usize,
    ) -> (DrainOutcome, usize) {
        drain_worker_results(&self.line_rx, expected, self.watchdog, tx, worker_id)
    }
}

/// Shared parameters for spawning a worker thread.
///
/// Bundles the 10 fields that are common to both [`spawn_worker`] and
/// [`spawn_worker_with_process`] so call sites build a struct instead of
/// passing a long positional argument list.
pub(crate) struct WorkerParams {
    /// Unique zero-based index identifying this worker.
    pub worker_id: usize,
    /// Shared scheduler that distributes test groups to workers.
    pub sched: std::sync::Arc<scheduler::Scheduler>,
    /// Flag set when the run is cancelled (e.g. maxfail reached).
    pub cancelled: std::sync::Arc<std::sync::atomic::AtomicBool>,
    /// Pre-serialized conftest JSON sent to the worker on each task.
    pub conftest_json: std::sync::Arc<serde_json::value::RawValue>,
    /// `[{"module": ..., "anchor": ...}]` — every `__fixtures__.py` collection
    /// registered serially, so worker sessions match the coordinator (#1732).
    pub fixture_modules_json: std::sync::Arc<serde_json::value::RawValue>,
    /// Per-test timeout in seconds; `None` means no timeout.
    pub timeout_secs: Option<u64>,
    /// How to handle temp directories: "cleanup", "failed", or "always".
    pub keep_tmp: std::sync::Arc<str>,
    /// Project rootdir sent to each worker so test-tree imports resolve (#1780).
    pub rootdir: std::sync::Arc<str>,
    /// Whether to include local variables in failure tracebacks.
    pub show_locals: bool,
    /// Whether to include oxitest-internal frames in tracebacks.
    pub show_internals: bool,
    /// Channel for sending results back to the coordinator.
    pub tx: crossbeam_channel::Sender<crate::parallel::WorkerMessage>,
    /// Set of node IDs currently executing across all workers.
    pub in_flight: std::sync::Arc<parking_lot::Mutex<ahash::AHashSet<String>>>,
}

/// Build the JSON task for one scheduler unit.
///
/// Extracted from [`run_worker_loop`] so it is reachable by `cargo test`: the
/// loop itself needs a live subprocess and a scheduler, so coverage could never
/// enter it — which is why `codecov/patch/rust` failed on #1747.
#[allow(clippy::too_many_arguments)]
fn build_task<'a>(
    group: &'a scheduler::TaskGroup,
    conftest_json: &'a serde_json::value::RawValue,
    fixture_modules_json: &'a serde_json::value::RawValue,
    timeout_secs: Option<u64>,
    keep_tmp: &'a str,
    rootdir: &'a str,
    show_locals: bool,
    show_internals: bool,
) -> WorkerTask<'a> {
    WorkerTask {
        protocol_version: crate::worker_result::PROTOCOL_VERSION,
        modules: group
            .modules
            .iter()
            .map(|m| WorkerTaskModule {
                module_path: m.module_path.as_str(),
                items: m
                    .items
                    .iter()
                    .map(|item| WorkerTaskItem {
                        fn_name: &item.fn_name,
                        param_id: item.param_id.as_deref(),
                        node_id: &item.node_id,
                        markers: item.markers.iter().collect(),
                    })
                    .collect(),
            })
            .collect(),
        conftest_paths: conftest_json,
        fixture_modules: fixture_modules_json,
        timeout_secs,
        keep_tmp,
        rootdir,
        show_locals: if show_locals { Some(true) } else { None },
        show_internals: if show_internals { Some(true) } else { None },
    }
}

/// Runs the task-dispatch loop for a single worker.
///
/// Consumes the already-established subprocess handles (`child`, `stdin`, `line_rx`)
/// together with `params` and drives the worker until the scheduler is drained,
/// cancellation is requested, or the subprocess becomes unresponsive.
///
/// Extracted from the formerly-duplicated bodies of [`spawn_worker`] and
/// [`spawn_worker_with_process`]; both public functions are now thin wrappers
/// that supply the handles and delegate here.
fn run_worker_loop(
    mut child: std::process::Child,
    stdin: std::io::BufWriter<std::process::ChildStdin>,
    line_rx: crossbeam_channel::Receiver<String>,
    params: WorkerParams,
) {
    use std::sync::atomic::Ordering;
    use std::time::Duration;

    let WorkerParams {
        worker_id,
        sched,
        cancelled,
        conftest_json,
        fixture_modules_json,
        timeout_secs,
        keep_tmp,
        rootdir,
        show_locals,
        show_internals,
        tx,
        in_flight,
    } = params;

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

    let mut session = WorkerSession::new(stdin, line_rx, watchdog);
    let mut subprocess_alive = true;

    while subprocess_alive && !cancelled.load(Ordering::Relaxed) {
        let Some(group) = sched.pop() else { break };

        let task = build_task(
            &group,
            &conftest_json,
            &fixture_modules_json,
            timeout_secs,
            keep_tmp.as_ref(),
            &rootdir,
            show_locals,
            show_internals,
        );

        // Flattened once: every site below needs every module's items, and
        // DrainContext borrows the slice for the duration of the drain.
        let group_items: Vec<std::sync::Arc<types::TestItem>> = group.items().cloned().collect();

        if let Err(e) = session.send_task(&task) {
            tracing::warn!(
                module = %group.label(),
                error = %e,
                "failed to send task to worker — emitting error for all group items"
            );
            for item in &group_items {
                let _ = tx.send(crate::parallel::WorkerMessage::Result(
                    crate::parallel::WorkerResult {
                        resolved: types::ResolvedOutcome {
                            node_id: item.node_id.clone(),
                            duration_ms: types::DurationMs::ZERO,
                            outcome: types::TestOutcome::crashed_sentinel(),
                        },
                        worker_id,
                    },
                ));
            }
            break;
        }

        {
            let mut set = in_flight.lock();
            for item in &group_items {
                set.insert(item.node_id.to_string());
            }
        }

        let expected = group.item_count();
        let (drain_outcome, received) = session.drain_results(expected, &tx, worker_id);

        subprocess_alive = handle_drain_outcome(
            drain_outcome,
            received,
            &mut DrainContext {
                child: &mut child,
                items: &group_items,
                watchdog,
                module_path: group.label(),
                tx: &tx,
                worker_id,
            },
        );
    }

    // Only a worker that finished its groups normally has a tail worth reading.
    // A timed-out worker has already been killed and a disconnected one has
    // closed its stdout, so skipping them keeps "no new wait on the kill path"
    // true by construction rather than by waiting for a deadline to expire.
    if subprocess_alive {
        session.close_stdin();
        session.drain_tail(&tx, worker_id);
    }
    drop(session);
    let _ = child.wait();
}

pub(crate) fn spawn_worker(
    python_bin: std::sync::Arc<str>,
    params: WorkerParams,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let (child, stdin, line_rx) = match setup_worker_process(&python_bin) {
            Some(v) => v,
            None => return,
        };
        run_worker_loop(child, stdin, line_rx, params);
    })
}

/// Like [`spawn_worker`] but accepts a pre-spawned `(Child, BufWriter, Receiver)` tuple
/// instead of calling `setup_worker_process` internally. Used by the pre-warming pool
/// so that subprocess startup overlaps with earlier pipeline stages.
pub(crate) fn spawn_worker_with_process(
    prewarmed: (
        std::process::Child,
        std::io::BufWriter<std::process::ChildStdin>,
        crossbeam_channel::Receiver<String>,
    ),
    params: WorkerParams,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let (child, stdin, line_rx) = prewarmed;
        run_worker_loop(child, stdin, line_rx, params);
    })
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
mod worker_session_tests {
    use super::*;
    use std::io::BufRead;
    use std::process::{Command, Stdio};
    use std::time::Duration;

    /// Helper: spawn `cat` with piped stdio and build a `WorkerSession` around it.
    fn cat_session() -> (std::process::Child, WorkerSession) {
        let mut child = Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("`cat` must be available in test environment");

        let (stdin, stdout) =
            take_child_pipes(&mut child).expect("piped child must yield stdin/stdout");

        let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        std::thread::spawn(move || {
            let mut stdout = stdout;
            let mut buf = String::with_capacity(256);
            loop {
                buf.clear();
                match stdout.read_line(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        if line_tx.send(buf.clone()).is_err() {
                            break;
                        }
                    }
                }
            }
        });

        let session = WorkerSession::new(stdin, line_rx, Duration::from_secs(5));
        (child, session)
    }

    /// Helper: build a minimal `WorkerTask` from the given `RawValue` payloads.
    fn minimal_task<'a>(
        conftest: &'a serde_json::value::RawValue,
        fixture_modules: &'a serde_json::value::RawValue,
    ) -> WorkerTask<'a> {
        WorkerTask {
            protocol_version: crate::worker_result::PROTOCOL_VERSION,
            modules: vec![WorkerTaskModule {
                module_path: "tests/test_example.py",
                items: vec![WorkerTaskItem {
                    fn_name: "test_add",
                    param_id: None,
                    node_id: "tests/test_example.py::test_add",
                    markers: vec![],
                }],
            }],
            conftest_paths: conftest,
            fixture_modules,
            timeout_secs: None,
            keep_tmp: "cleanup",
            rootdir: "/rootdir",
            show_locals: None,
            show_internals: None,
        }
    }

    /// A `ModuleGroup` with `count` synthetic items.
    fn task_module(path: &str, count: usize) -> crate::scheduler::ModuleGroup {
        let items = (0..count)
            .map(|i| {
                std::sync::Arc::new(
                    types::TestItem::builder(path, &format!("test_{i}"))
                        .lineno(i)
                        .build(),
                )
            })
            .collect();
        crate::scheduler::ModuleGroup::new(camino::Utf8PathBuf::from(path), items)
    }

    #[test]
    fn close_stdin_really_closes_it() {
        // Arrange
        let (mut child, mut session) = cat_session();

        // Act
        session.close_stdin();

        // Assert
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest, &fixtures);
        assert!(
            session.send_task(&task).is_err(),
            "closing stdin must actually release the pipe — the worker begins its \
             teardown only on EOF, so a close that did not close would leave the \
             tail this reads for permanently unwritten (#1840)"
        );

        let _ = child.kill();
        let _ = child.wait();
    }

    #[test]
    fn the_tail_read_survives_closing_stdin() {
        // Arrange — `cat` echoes stdin, so writing a diagnostic then closing
        // stdin reproduces exactly the shape #1840 loses: output that arrives
        // after the last result line, with no further drain to pick it up.
        let (mut child, mut session) = cat_session();
        let (result_tx, result_rx) =
            crossbeam_channel::unbounded::<crate::parallel::WorkerMessage>();
        {
            use std::io::Write;
            let stdin = session
                .stdin
                .as_mut()
                .expect("session starts with stdin open");
            writeln!(
                stdin,
                r#"{{"type":"diagnostic","severity":"warning","context":"fixture teardown","message":"late"}}"#
            )
            .unwrap();
            stdin.flush().unwrap();
        }

        // Act
        session.close_stdin();
        session.drain_tail(&result_tx, 0);

        // Assert
        assert_eq!(
            result_rx.len(),
            1,
            "a diagnostic written after the final result must still be read; \
             dropping the session instead of closing stdin would drop line_rx \
             with it and discard exactly this (#1840)"
        );

        let _ = child.wait();
    }

    #[test]
    fn build_task_maps_every_module_in_the_group() {
        // Arrange
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let group = crate::scheduler::TaskGroup {
            modules: vec![
                task_module("tests/test_a.py", 2),
                task_module("tests/test_b.py", 1),
            ],
        };

        // Act
        let task = build_task(
            &group, &conftest, &fixtures, None, "cleanup", "/rootdir", false, false,
        );

        // Assert
        assert_eq!(
            task.modules.len(),
            2,
            "every module in the group must reach the worker — dropping one strands \
             its tests, and the coordinator would wait on results never produced"
        );
        assert_eq!(
            task.modules[0].items.len(),
            2,
            "items must stay with their own module; misattributing them would run \
             tests against the wrong module's fixtures"
        );
        assert_eq!(
            task.modules[1].module_path, "tests/test_b.py",
            "module order must match the group, since the worker emits results in \
             task order and the coordinator matches them positionally"
        );
    }

    #[test]
    fn build_task_stamps_the_current_protocol_version() {
        // Arrange
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let group = crate::scheduler::TaskGroup::single(task_module("tests/test_a.py", 1));

        // Act
        let task = build_task(
            &group, &conftest, &fixtures, None, "cleanup", "/rootdir", false, false,
        );

        // Assert — an unstamped task is rejected by every worker, so forgetting
        // this would fail the whole run rather than degrade quietly.
        assert_eq!(
            task.protocol_version,
            crate::worker_result::PROTOCOL_VERSION,
            "the task must carry the version the worker checks against"
        );
    }

    #[test]
    fn send_task_writes_valid_json_line() {
        // Arrange
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let no_fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest, &no_fixtures);

        // Act
        session.send_task(&task).expect("send_task must succeed");

        // Assert — `cat` echoes the line back through the reader thread.
        let line = session
            .line_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("must receive echoed line");

        // Must end with a newline (the worker protocol requirement).
        assert!(line.ends_with('\n'), "echoed line must end with newline");

        // Must be valid JSON.
        let parsed: serde_json::Value =
            serde_json::from_str(line.trim()).expect("line must be valid JSON");
        assert_eq!(
            parsed["protocol_version"],
            crate::worker_result::PROTOCOL_VERSION,
            "a task serialized without the version field is rejected by every \
             worker — the check exists so a stale build fails with an actionable \
             message instead of a KeyError deep inside run()"
        );
        assert_eq!(
            parsed["modules"][0]["module_path"], "tests/test_example.py",
            "modules must nest their own path — flattening it onto items would \
             make item ordering load-bearing for end_module boundaries"
        );
        assert_eq!(
            parsed["modules"][0]["items"][0]["fn_name"], "test_add",
            "items must stay nested under their module so teardown boundaries \
             are structural rather than inferred from ordering"
        );
        assert_eq!(parsed["conftest_paths"], serde_json::json!([]));
        assert!(parsed["timeout_secs"].is_null());
        assert_eq!(
            parsed["rootdir"], "/rootdir",
            "the worker inherits nothing from the coordinator's sys.path — a \
             dropped rootdir field means every parallel test-tree import fails \
             three layers away in a subprocess"
        );

        drop(session);
        let _ = child.wait();
    }

    #[test]
    fn send_task_includes_param_id_when_present() {
        // Arrange
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let no_fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = WorkerTask {
            protocol_version: crate::worker_result::PROTOCOL_VERSION,
            modules: vec![WorkerTaskModule {
                module_path: "tests/test_math.py",
                items: vec![WorkerTaskItem {
                    fn_name: "test_mul",
                    param_id: Some("x=2-y=3"),
                    node_id: "tests/test_math.py::test_mul[x=2-y=3]",
                    markers: vec!["slow"],
                }],
            }],
            conftest_paths: &conftest,
            fixture_modules: &no_fixtures,
            timeout_secs: Some(30),
            keep_tmp: "cleanup",
            rootdir: "/rootdir",
            show_locals: None,
            show_internals: None,
        };

        // Act
        session.send_task(&task).expect("send_task must succeed");

        // Assert
        let line = session
            .line_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("must receive echoed line");
        let parsed: serde_json::Value =
            serde_json::from_str(line.trim()).expect("line must be valid JSON");
        assert_eq!(
            parsed["modules"][0]["items"][0]["param_id"], "x=2-y=3",
            "parametrize case ids travel per item inside their module — losing \
             them would collapse every case of a parametrized test onto one node id"
        );
        assert_eq!(parsed["timeout_secs"], 30);

        drop(session);
        let _ = child.wait();
    }

    #[test]
    fn send_task_echoes_back_through_line_rx() {
        // Arrange — keep the session alive so we can read from line_rx.
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let no_fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest, &no_fixtures);

        // Act
        session.send_task(&task).expect("send_task must succeed");

        // Assert — `cat` echoes the line back; the reader thread should
        // deliver it through line_rx within a reasonable timeout.
        let line = session
            .line_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("must receive echoed line");

        let parsed: serde_json::Value =
            serde_json::from_str(line.trim()).expect("echoed line must be valid JSON");
        assert_eq!(
            parsed["modules"][0]["module_path"], "tests/test_example.py",
            "the round trip must preserve the nested shape — a worker reading a \
             flat module_path would see nothing and run no tests"
        );

        drop(session);
        let _ = child.wait();
    }

    #[test]
    fn send_task_returns_error_on_dead_process() {
        // Arrange — spawn a process that exits immediately, then try to write.
        let mut child = Command::new("true")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("`true` must spawn");

        let (stdin, _stdout) = take_child_pipes(&mut child).unwrap();
        let (_tx, line_rx) = crossbeam_channel::unbounded::<String>();
        let mut session = WorkerSession::new(stdin, line_rx, Duration::from_secs(1));

        // Wait for the child to exit so its stdin pipe is broken.
        let _ = child.wait();

        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let no_fixtures = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest, &no_fixtures);

        // Act — writing to a dead process should eventually error.
        // The first write may succeed (kernel buffer), so send repeatedly.
        let mut errored = false;
        for _ in 0..1000 {
            if session.send_task(&task).is_err() {
                errored = true;
                break;
            }
        }

        // Assert
        assert!(errored, "send_task must return Err after process exits");
    }
}

#[cfg(test)]
mod line_length_tests {
    use super::*;

    // NOTE: The truncation path (buf.len() > MAX_LINE_LENGTH) is embedded in a
    // closure inside setup_worker_process and requires a real subprocess write of
    // >10 MB to trigger. That is impractical in a unit test context. The constant
    // value and channel capacity are verified here; normal operation is covered by
    // the integration tests via `just test`.

    #[test]
    fn max_line_length_is_ten_mebibytes() {
        assert_eq!(
            MAX_LINE_LENGTH,
            10 * 1024 * 1024,
            "MAX_LINE_LENGTH must be 10 MiB — change this value only with a deliberate decision \
             about memory safety for the coordinator process"
        );
    }
}
