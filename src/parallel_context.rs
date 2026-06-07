/// Context from parallel execution, attached to test failures.
#[derive(Debug, Clone)]
pub(crate) struct ParallelContext {
    /// 1-indexed worker ID.
    pub worker_id: usize,
    /// Node IDs of tests that were in-flight when this result arrived.
    pub concurrent_tests: Vec<String>,
}

impl ParallelContext {
    /// Format the parallel context line for failure output.
    ///
    /// Extracts short names (after last `::`) from node IDs, truncates to 3,
    /// and appends `(+N more)` if needed.
    pub fn fmt_line(&self, use_color: bool) -> String {
        let concurrent = if self.concurrent_tests.is_empty() {
            "(none)".to_string()
        } else {
            let short_names: Vec<&str> = self
                .concurrent_tests
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

    #[test]
    fn fmt_line_no_concurrent() {
        let ctx = ParallelContext {
            worker_id: 2,
            concurrent_tests: vec![],
        };
        assert_eq!(ctx.fmt_line(false), "  worker #2 | concurrent: (none)");
    }

    #[test]
    fn fmt_line_one_concurrent() {
        let ctx = ParallelContext {
            worker_id: 1,
            concurrent_tests: vec!["tests/test_db.py::test_read_user".into()],
        };
        assert_eq!(
            ctx.fmt_line(false),
            "  worker #1 | concurrent: test_read_user"
        );
    }

    #[test]
    fn fmt_line_three_concurrent() {
        let ctx = ParallelContext {
            worker_id: 3,
            concurrent_tests: vec![
                "tests/test_db.py::test_read_user".into(),
                "tests/test_db.py::test_delete_session".into(),
                "tests/test_auth.py::test_login".into(),
            ],
        };
        assert_eq!(
            ctx.fmt_line(false),
            "  worker #3 | concurrent: test_read_user, test_delete_session, test_login"
        );
    }

    #[test]
    fn fmt_line_more_than_three_concurrent() {
        let ctx = ParallelContext {
            worker_id: 2,
            concurrent_tests: vec![
                "tests/test_db.py::test_read_user".into(),
                "tests/test_db.py::test_delete_session".into(),
                "tests/test_auth.py::test_login".into(),
                "tests/test_auth.py::test_logout".into(),
                "tests/test_api.py::test_health".into(),
            ],
        };
        assert_eq!(
            ctx.fmt_line(false),
            "  worker #2 | concurrent: test_read_user, test_delete_session, test_login (+2 more)"
        );
    }

    #[test]
    fn fmt_line_node_id_without_separator() {
        let ctx = ParallelContext {
            worker_id: 1,
            concurrent_tests: vec!["test_plain".into()],
        };
        assert_eq!(ctx.fmt_line(false), "  worker #1 | concurrent: test_plain");
    }
}
