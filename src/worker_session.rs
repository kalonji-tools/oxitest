//! Worker session management — subprocess lifecycle and I/O helpers.
//!
//! Contains the low-level plumbing for spawning a worker subprocess, wiring up
//! its stdin/stdout, and the [`WorkerSession`] struct that bundles communication
//! state for a single worker thread.

use crate::{
    parallel::{drain_worker_results, handle_drain_outcome, DrainOutcome},
    scheduler,
    worker_result::{WorkerResult, WorkerTask, WorkerTaskItem},
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
        tx: &crossbeam_channel::Sender<WorkerResult>,
    ) -> (DrainOutcome, usize) {
        drain_worker_results(&self.line_rx, expected, self.watchdog, tx)
    }
}

pub(crate) fn spawn_worker(
    python_bin: String,
    sched: std::sync::Arc<scheduler::Scheduler>,
    cancelled: std::sync::Arc<std::sync::atomic::AtomicBool>,
    conftest_json: std::sync::Arc<serde_json::value::RawValue>,
    timeout_secs: Option<u64>,
    tx: crossbeam_channel::Sender<WorkerResult>,
) -> std::thread::JoinHandle<()> {
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
        let (mut child, worker_stdin, line_rx) = match setup_worker_process(&python_bin) {
            Some(v) => v,
            None => return,
        };

        let mut session = WorkerSession::new(worker_stdin, line_rx, watchdog);
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

            if let Err(e) = session.send_task(&task) {
                tracing::warn!(
                    module = %group.module_path,
                    error = %e,
                    "failed to send task to worker — emitting error for all group items"
                );
                for item in &group.items {
                    let _ = tx.send(WorkerResult::crashed(item.node_id.to_string()));
                }
                break;
            }

            let expected = group.items.len();
            let (drain_outcome, received) = session.drain_results(expected, &tx);

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

        drop(session);
        let _ = child.wait();
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
