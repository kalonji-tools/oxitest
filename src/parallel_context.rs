use std::sync::Arc;

/// Context from parallel execution, attached to test failures.
///
/// Holds a shared reference to the in-flight set rather than a snapshot.
/// The snapshot is taken lazily via [`concurrent_tests()`](Self::concurrent_tests)
/// — only on the failure-reporting path.
#[derive(Debug, Clone)]
pub(crate) struct ParallelContext {
    /// 1-indexed worker ID.
    pub worker_id: usize,
    /// Shared in-flight set — snapshot is deferred to the failure path.
    in_flight: Arc<parking_lot::Mutex<ahash::AHashSet<String>>>,
}

impl ParallelContext {
    /// Create a new context that lazily snapshots the in-flight set.
    pub const fn new(
        worker_id: usize,
        in_flight: Arc<parking_lot::Mutex<ahash::AHashSet<String>>>,
    ) -> Self {
        Self {
            worker_id,
            in_flight,
        }
    }

    /// Snapshot the concurrent tests that were in-flight.
    ///
    /// Takes the lock briefly — fine for failure reporting (rare path) but
    /// would be a problem if called in a hot loop.
    pub fn concurrent_tests(&self) -> Vec<String> {
        self.in_flight.lock().iter().cloned().collect()
    }

    /// Format the parallel context line for failure output.
    ///
    /// Extracts short names (after last `::`) from node IDs, truncates to 3,
    /// and appends `(+N more)` if needed.
    pub fn fmt_line(&self, use_color: bool) -> String {
        let tests = self.concurrent_tests();
        let concurrent = if tests.is_empty() {
            "(none)".to_string()
        } else {
            let short_names: Vec<&str> = tests
                .iter()
                .map(|id| id.rsplit("::").next().unwrap_or(id.as_str()))
                .collect();
            let displayed: Vec<&str> = short_names.iter().take(3).copied().collect();
            let remaining = short_names.len().saturating_sub(3);
            if remaining > 0 {
                format!("{} (+{} more)", displayed.join(", "), remaining)
            } else {
                displayed.join(", ")
            }
        };
        let line = format!("  worker #{} | concurrent: {}", self.worker_id, concurrent);
        if use_color {
            crate::colors::color_dim(&line, use_color)
        } else {
            line
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: build a `ParallelContext` from a slice of node-id strings.
    fn ctx_with(worker_id: usize, ids: &[&str]) -> ParallelContext {
        let set: ahash::AHashSet<String> = ids.iter().map(|s| (*s).to_string()).collect();
        ParallelContext::new(worker_id, Arc::new(parking_lot::Mutex::new(set)))
    }

    #[test]
    fn fmt_line_no_concurrent() {
        let ctx = ctx_with(2, &[]);
        assert_eq!(ctx.fmt_line(false), "  worker #2 | concurrent: (none)");
    }

    #[test]
    fn fmt_line_one_concurrent() {
        let ctx = ctx_with(1, &["tests/test_db.py::test_read_user"]);
        assert_eq!(
            ctx.fmt_line(false),
            "  worker #1 | concurrent: test_read_user"
        );
    }

    #[test]
    fn fmt_line_three_concurrent() {
        let ctx = ctx_with(
            3,
            &[
                "tests/test_db.py::test_read_user",
                "tests/test_db.py::test_delete_session",
                "tests/test_auth.py::test_login",
            ],
        );
        // AHashSet iteration order is non-deterministic, so check parts
        let line = ctx.fmt_line(false);
        assert!(
            line.starts_with("  worker #3 | concurrent: "),
            "unexpected prefix: {line}"
        );
        assert!(line.contains("test_read_user"), "missing test_read_user");
        assert!(
            line.contains("test_delete_session"),
            "missing test_delete_session"
        );
        assert!(line.contains("test_login"), "missing test_login");
    }

    #[test]
    fn fmt_line_more_than_three_concurrent() {
        let ctx = ctx_with(
            2,
            &[
                "tests/test_db.py::test_read_user",
                "tests/test_db.py::test_delete_session",
                "tests/test_auth.py::test_login",
                "tests/test_auth.py::test_logout",
                "tests/test_api.py::test_health",
            ],
        );
        let line = ctx.fmt_line(false);
        assert!(
            line.starts_with("  worker #2 | concurrent: "),
            "unexpected prefix: {line}"
        );
        assert!(
            line.contains("(+2 more)"),
            "should truncate to 3 and show +2 more: {line}"
        );
    }

    #[test]
    fn fmt_line_node_id_without_separator() {
        let ctx = ctx_with(1, &["test_plain"]);
        assert_eq!(ctx.fmt_line(false), "  worker #1 | concurrent: test_plain");
    }

    #[test]
    fn concurrent_tests_returns_snapshot() {
        let ctx = ctx_with(1, &["a::b", "c::d"]);
        let tests = ctx.concurrent_tests();
        assert_eq!(
            tests.len(),
            2,
            "concurrent_tests() should return all in-flight entries"
        );
    }
}
