//! Worker session management — subprocess lifecycle and I/O helpers.
//!
//! Contains the low-level plumbing for spawning a worker subprocess, wiring up
//! its stdin/stdout, and the [`WorkerSession`] struct that bundles communication
//! state for a single worker thread.

use crate::{
    parallel::{drain_worker_results, handle_drain_outcome, DrainContext, DrainOutcome},
    scheduler, types,
    worker_result::{WorkerTask, WorkerTaskItem},
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
    let (line_tx, line_rx) = crossbeam_channel::unbounded::<String>();
    let _reader = std::thread::spawn(move || {
        let mut stdout = worker_stdout;
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

    Some((child, worker_stdin, line_rx))
}

/// Bundles subprocess communication state for a single worker.
///
/// Encapsulates the stdin writer, stdout line receiver, and watchdog duration
/// so that `spawn_worker()` can express the task dispatch loop in terms of
/// `send_task()` / `drain_results()` instead of juggling three locals.
struct WorkerSession {
    stdin: std::io::BufWriter<std::process::ChildStdin>,
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
            stdin,
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
        serde_json::to_writer(&mut self.stdin, task).map_err(std::io::Error::other)?;
        writeln!(self.stdin)?;
        self.stdin.flush()
    }

    /// Drains up to `expected` result lines from the worker, forwarding each to `tx`.
    fn drain_results(
        &self,
        expected: usize,
        tx: &crossbeam_channel::Sender<crate::parallel::WorkerResult>,
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
    /// Per-test timeout in seconds; `None` means no timeout.
    pub timeout_secs: Option<u64>,
    /// Directory to preserve temp files in; `None` = clean up after run.
    pub keep_tmp: Option<std::sync::Arc<str>>,
    /// Whether to include local variables in failure tracebacks.
    pub show_locals: bool,
    /// Whether to include oxitest-internal frames in tracebacks.
    pub show_internals: bool,
    /// Channel for sending results back to the coordinator.
    pub tx: crossbeam_channel::Sender<crate::parallel::WorkerResult>,
    /// Set of node IDs currently executing across all workers.
    pub in_flight: std::sync::Arc<std::sync::Mutex<ahash::AHashSet<String>>>,
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
        timeout_secs,
        keep_tmp,
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

        let task = WorkerTask {
            module_path: group.module_path.as_str(),
            items: group
                .items
                .iter()
                .map(|item| WorkerTaskItem {
                    fn_name: &item.fn_name,
                    param_id: item.param_id.as_deref(),
                    node_id: &item.node_id,
                    markers: &item.markers,
                })
                .collect(),
            conftest_paths: &conftest_json,
            timeout_secs,
            keep_tmp: keep_tmp.as_deref(),
            show_locals: if show_locals { Some(true) } else { None },
            show_internals: if show_internals { Some(true) } else { None },
        };

        if let Err(e) = session.send_task(&task) {
            tracing::warn!(
                module = %group.module_path,
                error = %e,
                "failed to send task to worker — emitting error for all group items"
            );
            for item in &group.items {
                let _ = tx.send(crate::parallel::WorkerResult {
                    node_id: item.node_id.to_string(),
                    duration_ms: 0.0,
                    outcome: types::TestOutcome::crashed_sentinel(),
                    worker_id,
                });
            }
            break;
        }

        {
            let mut set = in_flight.lock().unwrap();
            for item in &group.items {
                set.insert(item.node_id.to_string());
            }
        }

        let expected = group.items.len();
        let (drain_outcome, received) = session.drain_results(expected, &tx, worker_id);

        subprocess_alive = handle_drain_outcome(
            drain_outcome,
            received,
            &mut DrainContext {
                child: &mut child,
                items: &group.items,
                watchdog,
                module_path: &group.module_path,
                tx: &tx,
                worker_id,
            },
        );
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

    /// Helper: build a minimal `WorkerTask` using the given `RawValue`.
    fn minimal_task(conftest: &serde_json::value::RawValue) -> WorkerTask<'_> {
        WorkerTask {
            module_path: "tests/test_example.py",
            items: vec![WorkerTaskItem {
                fn_name: "test_add",
                param_id: None,
                node_id: "tests/test_example.py::test_add",
                markers: &[],
            }],
            conftest_paths: conftest,
            timeout_secs: None,
            keep_tmp: None,
            show_locals: None,
            show_internals: None,
        }
    }

    #[test]
    fn send_task_writes_valid_json_line() {
        // Arrange
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest);

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
        assert_eq!(parsed["module_path"], "tests/test_example.py");
        assert_eq!(parsed["items"][0]["fn_name"], "test_add");
        assert_eq!(parsed["conftest_paths"], serde_json::json!([]));
        assert!(parsed["timeout_secs"].is_null());

        drop(session);
        let _ = child.wait();
    }

    #[test]
    fn send_task_includes_param_id_when_present() {
        // Arrange
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let markers = vec!["slow".to_string()];
        let task = WorkerTask {
            module_path: "tests/test_math.py",
            items: vec![WorkerTaskItem {
                fn_name: "test_mul",
                param_id: Some("x=2-y=3"),
                node_id: "tests/test_math.py::test_mul[x=2-y=3]",
                markers: &markers,
            }],
            conftest_paths: &conftest,
            timeout_secs: Some(30),
            keep_tmp: None,
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
        assert_eq!(parsed["items"][0]["param_id"], "x=2-y=3");
        assert_eq!(parsed["timeout_secs"], 30);

        drop(session);
        let _ = child.wait();
    }

    #[test]
    fn send_task_echoes_back_through_line_rx() {
        // Arrange — keep the session alive so we can read from line_rx.
        let (mut child, mut session) = cat_session();
        let conftest = serde_json::value::RawValue::from_string("[]".to_string()).unwrap();
        let task = minimal_task(&conftest);

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
        assert_eq!(parsed["module_path"], "tests/test_example.py");

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
        let task = minimal_task(&conftest);

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
