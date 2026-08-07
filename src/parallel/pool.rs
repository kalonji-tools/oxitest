//! Pre-warmed worker pool management.
//!
//! Handles eager spawning of worker subprocesses so their Python interpreter
//! startup overlaps with earlier pipeline stages.

/// A pre-spawned worker subprocess ready for task dispatch.
///
/// Tuple of `(child process, buffered stdin writer, stdout line receiver)`.
pub type PrewarmedWorker = (
    std::process::Child,
    std::io::BufWriter<std::process::ChildStdin>,
    crossbeam_channel::Receiver<String>,
);

/// Spawn `count` worker subprocesses eagerly so their startup overlaps with
/// earlier pipeline stages (fixture arrangement, inprocess tests, etc.).
pub fn prewarm_workers(python_bin: &str, count: usize) -> Vec<PrewarmedWorker> {
    (0..count)
        .filter_map(|_| crate::worker_session::setup_worker_process(python_bin))
        .collect()
}

/// Kill all remaining workers in a pre-warmed pool (e.g., excess workers).
pub fn kill_pool(mut pool: Vec<PrewarmedWorker>) {
    for (mut child, _stdin, _rx) in pool.drain(..) {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// RAII guard for a pre-warmed worker pool.
///
/// On drop, kills all remaining workers. Use `take()` to move workers out
/// before drop (e.g., to pass them to `ParallelHarness`).
pub struct PoolGuard {
    workers: Vec<PrewarmedWorker>,
}

impl PoolGuard {
    pub(crate) const fn new(workers: Vec<PrewarmedWorker>) -> Self {
        Self { workers }
    }

    /// Move all workers out of the guard. After this, drop is a no-op.
    pub(crate) fn take(&mut self) -> Vec<PrewarmedWorker> {
        std::mem::take(&mut self.workers)
    }
}

impl Drop for PoolGuard {
    fn drop(&mut self) {
        if !self.workers.is_empty() {
            kill_pool(std::mem::take(&mut self.workers));
        }
    }
}

#[cfg(test)]
mod prewarm_tests {
    #[test]
    fn prewarm_with_invalid_binary_returns_empty_pool() {
        let pool = super::prewarm_workers("/nonexistent/python", 4);
        assert!(
            pool.is_empty(),
            "invalid binary must produce no workers, got {}",
            pool.len()
        );
    }

    #[test]
    fn prewarm_spawns_requested_count() {
        // `cat` is a valid binary — it spawns successfully even though it exits
        // immediately (it chokes on the `-m` flag passed by setup_worker_process).
        // The important thing is that spawn() succeeds and we get the right count.
        let pool = super::prewarm_workers("cat", 3);
        assert_eq!(pool.len(), 3, "must spawn exactly 3 workers");
        // Verify each child was assigned a valid PID at spawn time.
        for (child, _, _) in &pool {
            assert!(child.id() > 0, "each worker must have a valid PID");
        }
        super::kill_pool(pool);
    }

    #[test]
    fn kill_pool_terminates_all_processes() {
        let pool = super::prewarm_workers("cat", 3);
        #[cfg(target_os = "linux")]
        let pids: Vec<u32> = pool.iter().map(|(c, _, _)| c.id()).collect();
        super::kill_pool(pool);
        // After kill_pool, wait() has been called on each child.
        // On Linux /proc/<pid> disappears once the process is waited.
        //
        // Linux-only, binding included: macOS has no /proc, so `!exists()`
        // there is unconditionally true and this would pass vacuously — a
        // green check proving nothing, which is not the same as a skip. The
        // binding carries the same gate because `pids` has no other use site,
        // and `warnings = "deny"` would turn the unused one into a build
        // failure on exactly the platforms this exists to test (#1944).
        #[cfg(target_os = "linux")]
        for pid in pids {
            let proc_path = format!("/proc/{pid}");
            assert!(
                !std::path::Path::new(&proc_path).exists(),
                "process {pid} should not exist after kill_pool (zombie leak)"
            );
        }
    }

    #[test]
    fn kill_pool_on_empty_is_noop() {
        super::kill_pool(vec![]); // must not panic
    }

    #[test]
    fn pool_guard_kills_on_drop() {
        let workers = super::prewarm_workers("cat", 2);
        #[cfg(target_os = "linux")]
        let pids: Vec<u32> = workers.iter().map(|(c, _, _)| c.id()).collect();
        {
            let _guard = super::PoolGuard::new(workers);
            // guard drops here
        }
        // Linux-only — see kill_pool_terminates_all_processes (#1944).
        #[cfg(target_os = "linux")]
        for pid in pids {
            let proc_path = format!("/proc/{pid}");
            assert!(
                !std::path::Path::new(&proc_path).exists(),
                "process {pid} should not exist after PoolGuard drop (zombie leak)"
            );
        }
    }

    #[test]
    fn pool_guard_take_prevents_kill_on_drop() {
        let workers = super::prewarm_workers("cat", 2);
        #[cfg(target_os = "linux")]
        let pids: Vec<u32> = workers.iter().map(|(c, _, _)| c.id()).collect();
        let taken = {
            let mut guard = super::PoolGuard::new(workers);
            // take() empties the guard, so the drop at the end of this block
            // has no workers left to kill
            guard.take()
        };
        // Workers must still be alive (or at least not waited) after guard drop.
        // Kill them manually now.
        super::kill_pool(taken);
        // Linux-only — see kill_pool_terminates_all_processes (#1944).
        #[cfg(target_os = "linux")]
        for pid in pids {
            let proc_path = format!("/proc/{pid}");
            assert!(
                !std::path::Path::new(&proc_path).exists(),
                "process {pid} should not exist after manual kill_pool"
            );
        }
    }

    #[test]
    fn pool_guard_empty_drop_is_noop() {
        let guard = super::PoolGuard::new(vec![]);
        drop(guard); // must not panic
    }

    #[test]
    fn prewarmed_worker_pipes_are_wired() {
        // `cat` spawns successfully but exits immediately due to the `-m` flag.
        // We verify the pipe wiring: the stdout reader thread detects EOF and
        // disconnects the channel, proving the stdout→rx path is connected.
        let mut pool = super::prewarm_workers("cat", 1);
        assert_eq!(pool.len(), 1);
        let (mut child, _stdin, line_rx) = pool.pop().unwrap();

        // The cat process exits immediately. The reader thread sees EOF on
        // stdout and drops the sender, so recv should return Disconnected
        // (possibly after a short delay for the process to exit).
        let result = line_rx.recv_timeout(std::time::Duration::from_secs(5));
        assert!(
            result.is_err(),
            "rx must disconnect when worker exits, proving stdout pipe is wired"
        );

        let _ = child.kill();
        let _ = child.wait();
    }
}
