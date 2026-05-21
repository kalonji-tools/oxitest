//! Core data types shared across the runner.
//!
//! Defines [`NodeId`] (stable test identifier), [`TestItem`] (collected test metadata),
//! [`TestOutcome`] (the eight possible results of running a test), [`CollectError`],
//! and [`TestTiming`].

use camino::Utf8PathBuf;

/// Stable test identifier used throughout the runner.
/// Format: `module_path::fn_name` or `module_path::fn_name[param_id]`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct NodeId(String);

impl NodeId {
    pub fn new(module_path: &str, fn_name: &str, param_id: Option<&str>) -> Self {
        let base = format!("{}::{}", module_path, fn_name);
        match param_id {
            Some(id) => NodeId(format!("{}[{}]", base, id)),
            None => NodeId(base),
        }
    }

    /// Create a NodeId from an already-formatted string (e.g. received from a worker subprocess).
    pub fn from_raw(s: &str) -> Self {
        NodeId(s.to_string())
    }
}

impl std::fmt::Display for NodeId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// Exposes the full rendered node-id string (`"module_path::fn_name"` or
/// `"module_path::fn_name[param_id]"`), giving access to all `str` methods.
/// Use `str::contains` to match against the complete identifier including the path.
impl std::ops::Deref for NodeId {
    type Target = str;
    fn deref(&self) -> &str {
        &self.0
    }
}

impl AsRef<str> for NodeId {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone)]
pub struct TestItem {
    pub node_id: NodeId,
    pub module_path: Utf8PathBuf,
    pub fn_name: String,
    pub lineno: usize,
    pub markers: Vec<String>,
    pub param_id: Option<String>,
    pub param_values: Vec<(String, String)>,
    pub is_async: bool,
}

/// Single traceback frame from a test failure or error.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub file: String,
    pub lineno: usize,
    pub name: String,
    pub line: String,
}

#[derive(Debug, Clone)]
pub enum TestOutcome {
    Passed {
        no_message_lines: Vec<usize>,
    },
    Failed {
        message: String,
        file: String,
        lineno: usize,
        source_line: String,
        left: String,
        right: String,
        op: String,
        frames: Vec<Frame>,
    },
    Error {
        message: String,
        file: String,
        lineno: usize,
        source_line: String,
        frames: Vec<Frame>,
    },
    Skipped {
        reason: String,
    },
    Warned {
        reason: String,
        no_message_lines: Vec<usize>,
    },
    XFailed {
        reason: String,
    },
    XPassed {
        strict: bool,
    },
    Timeout {
        message: String,
    },
}

impl TestOutcome {
    /// True for outcomes that increment the failure counter and produce exit code 1.
    pub fn is_hard_failure(&self) -> bool {
        matches!(
            self,
            TestOutcome::Failed { .. }
                | TestOutcome::Error { .. }
                | TestOutcome::Timeout { .. }
                | TestOutcome::XPassed { strict: true }
        )
    }

    /// Single character for CI dot-progress output.
    pub fn dot_char(&self) -> char {
        match self {
            Self::Passed { no_message_lines } if no_message_lines.is_empty() => '.',
            Self::Passed { .. } => '\u{00B7}', // middot — bare assert passed
            Self::Failed { .. } => 'F',
            Self::Error { .. } => 'E',
            Self::Skipped { .. } => 's',
            Self::Warned { .. } => '.', // same as clean pass — warning shown in summary
            Self::XFailed { .. } => 'x',
            Self::XPassed { .. } => 'X',
            Self::Timeout { .. } => 'T',
        }
    }

    /// Short display label for TTY output. Empty string for passing tests.
    pub fn label(&self) -> &'static str {
        match self {
            Self::Failed { .. } => "FAIL ",
            Self::Error { .. } => "ERROR",
            Self::Skipped { .. } => "SKIP ",
            Self::Warned { .. } => "WARN ",
            Self::XFailed { .. } => "XFAIL",
            Self::XPassed { .. } => "XPASS",
            Self::Timeout { .. } => "TIME ",
            Self::Passed { .. } => "",
        }
    }

    /// Canonical lowercase status string. Matches the strings sent by worker subprocesses.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Passed { .. } => "passed",
            Self::Failed { .. } => "failed",
            Self::Error { .. } => "error",
            Self::Skipped { .. } => "skipped",
            Self::Warned { .. } => "warned",
            Self::XFailed { .. } => "xfailed",
            Self::XPassed { .. } => "xpassed",
            Self::Timeout { .. } => "timeout",
        }
    }
}

#[derive(thiserror::Error, Debug)]
pub enum CollectError {
    #[error("collection error in {path}:\n{message}")]
    ImportError { path: Utf8PathBuf, message: String },
    #[error("{0}")]
    PyError(String),
}

/// Lightweight tag for the kind of test outcome — no payload, just the label.
///
/// Used in [`TestTiming`] and [`CacheEntry`](crate::cache) to avoid stringly-typed
/// comparisons. The `#[serde(rename_all = "snake_case")]` attribute ensures round-trip
/// compatibility with the JSON cache and worker protocol.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutcomeKind {
    Passed,
    Failed,
    Error,
    Skipped,
    Warned,
    #[serde(rename = "xfailed")]
    XFailed,
    #[serde(rename = "xpassed")]
    XPassed,
    Timeout,
    /// Catch-all for unrecognised outcome strings from workers.
    #[serde(other)]
    Unknown,
}

impl OutcomeKind {
    /// True for outcomes that represent a definitive test failure.
    pub fn is_failure(&self) -> bool {
        matches!(self, Self::Failed | Self::Error | Self::Timeout)
    }

    /// Canonical lowercase status string matching [`TestOutcome::as_str()`].
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Passed => "passed",
            Self::Failed => "failed",
            Self::Error => "error",
            Self::Skipped => "skipped",
            Self::Warned => "warned",
            Self::XFailed => "xfailed",
            Self::XPassed => "xpassed",
            Self::Timeout => "timeout",
            Self::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for OutcomeKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl From<&TestOutcome> for OutcomeKind {
    fn from(outcome: &TestOutcome) -> Self {
        match outcome {
            TestOutcome::Passed { .. } => Self::Passed,
            TestOutcome::Failed { .. } => Self::Failed,
            TestOutcome::Error { .. } => Self::Error,
            TestOutcome::Skipped { .. } => Self::Skipped,
            TestOutcome::Warned { .. } => Self::Warned,
            TestOutcome::XFailed { .. } => Self::XFailed,
            TestOutcome::XPassed { .. } => Self::XPassed,
            TestOutcome::Timeout { .. } => Self::Timeout,
        }
    }
}

/// Result record for a single test execution, returned from the run phases.
#[derive(Debug, Clone)]
pub struct TestTiming {
    pub node_id: NodeId,
    pub duration_ms: f64,
    pub outcome: OutcomeKind,
}

/// Tracks hard failures and determines when to stop (maxfail).
pub(crate) struct FailureAccumulator {
    count: usize,
    max: usize,
}

impl FailureAccumulator {
    pub(crate) fn new(maxfail: usize) -> Self {
        Self {
            count: 0,
            max: maxfail,
        }
    }

    /// Record an outcome. Returns `true` if execution should stop (maxfail reached).
    #[must_use = "caller must check whether maxfail was reached"]
    pub(crate) fn record(&mut self, outcome: &TestOutcome) -> bool {
        if outcome.is_hard_failure() {
            self.count += 1;
        }
        self.max > 0 && self.count >= self.max
    }
}

/// Normalised inputs for constructing a `TestOutcome` from either the serial
/// (`bridge.rs`) or parallel (`parallel.rs`) execution path.
/// Each caller is responsible for mapping its own field types (Option<String>,
/// i64, etc.) into this normalised form before calling `from_raw`.
pub struct RawOutcome<'a> {
    pub status: &'a str,
    pub message: &'a str,
    pub file: &'a str,
    pub lineno: usize,
    pub source_line: &'a str,
    pub no_message_lines: &'a [usize],
    pub left: &'a str,
    pub right: &'a str,
    pub op: &'a str,
    pub strict: bool,
    pub frames: &'a [Frame],
}

impl TestOutcome {
    /// Build a `TestOutcome` from normalised raw fields.
    ///
    /// The `_` arm maps any unrecognised status to `Error`. Callers that want
    /// to log a warning for unknown statuses should do so before calling this.
    pub fn from_raw(r: RawOutcome<'_>) -> Self {
        match r.status {
            "passed" => TestOutcome::Passed {
                no_message_lines: r.no_message_lines.to_vec(),
            },
            "warned" => TestOutcome::Warned {
                reason: r.message.to_owned(),
                no_message_lines: r.no_message_lines.to_vec(),
            },
            "failed" => TestOutcome::Failed {
                message: r.message.to_owned(),
                file: r.file.to_owned(),
                lineno: r.lineno,
                source_line: r.source_line.to_owned(),
                left: r.left.to_owned(),
                right: r.right.to_owned(),
                op: r.op.to_owned(),
                frames: r.frames.to_vec(),
            },
            "skipped" => TestOutcome::Skipped {
                reason: r.message.to_owned(),
            },
            "xfailed" => TestOutcome::XFailed {
                reason: r.message.to_owned(),
            },
            "xpassed" => TestOutcome::XPassed { strict: r.strict },
            "timeout" => TestOutcome::Timeout {
                message: r.message.to_owned(),
            },
            _ => TestOutcome::Error {
                message: r.message.to_owned(),
                file: r.file.to_owned(),
                lineno: r.lineno,
                source_line: r.source_line.to_owned(),
                frames: r.frames.to_vec(),
            },
        }
    }
}

#[cfg(test)]
mod failure_accumulator_tests {
    use super::*;

    #[test]
    fn test_no_maxfail_never_stops() {
        let mut acc = FailureAccumulator::new(0);
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert!(!acc.record(&outcome));
        assert!(!acc.record(&outcome));
    }

    #[test]
    fn test_maxfail_stops_at_threshold() {
        let mut acc = FailureAccumulator::new(2);
        let fail = TestOutcome::Failed {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        let pass = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert!(!acc.record(&pass));
        assert!(!acc.record(&fail));
        assert!(acc.record(&fail)); // 2nd failure = stop
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_outcome_passed_carries_no_message_lines() {
        let o = TestOutcome::Passed {
            no_message_lines: vec![5, 10],
        };
        if let TestOutcome::Passed { no_message_lines } = o {
            assert_eq!(no_message_lines, vec![5, 10]);
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_failed_carries_location() {
        let o = TestOutcome::Failed {
            message: "msg".to_string(),
            file: "test_foo.py".to_string(),
            lineno: 7,
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        if let TestOutcome::Failed {
            lineno,
            left,
            right,
            op,
            ..
        } = o
        {
            assert_eq!(lineno, 7);
            assert_eq!(left, "0");
            assert_eq!(right, "1");
            assert_eq!(op, "==");
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_warned_carries_reason() {
        let o = TestOutcome::Warned {
            reason: "DeprecationWarning: old api".to_string(),
            no_message_lines: vec![3],
        };
        if let TestOutcome::Warned {
            reason,
            no_message_lines,
        } = o
        {
            assert!(reason.contains("DeprecationWarning"));
            assert_eq!(no_message_lines, vec![3]);
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_xfailed_stores_reason() {
        let outcome = TestOutcome::XFailed {
            reason: "known bug".to_string(),
        };
        match outcome {
            TestOutcome::XFailed { reason } => assert_eq!(reason, "known bug"),
            _ => panic!("unexpected variant"),
        }
    }

    #[test]
    fn test_outcome_xpassed_stores_strict() {
        let strict_outcome = TestOutcome::XPassed { strict: true };
        let lenient_outcome = TestOutcome::XPassed { strict: false };
        match strict_outcome {
            TestOutcome::XPassed { strict } => assert!(strict),
            _ => panic!("unexpected variant"),
        }
        match lenient_outcome {
            TestOutcome::XPassed { strict } => assert!(!strict),
            _ => panic!("unexpected variant"),
        }
    }

    #[test]
    fn test_item_has_param_fields() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_add", Some("basic")),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_add".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![("x".to_string(), "1".to_string())],
            is_async: false,
        };
        assert_eq!(item.param_id, Some("basic".to_string()));
        assert_eq!(item.param_values.len(), 1);
    }

    #[test]
    fn test_is_hard_failure_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert!(o.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            frames: vec![],
        };
        assert!(o.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_xpassed_strict() {
        assert!(TestOutcome::XPassed { strict: true }.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_xpassed_lenient() {
        assert!(!TestOutcome::XPassed { strict: false }.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_passed() {
        assert!(!TestOutcome::Passed {
            no_message_lines: vec![]
        }
        .is_hard_failure());
    }

    #[test]
    fn test_item_non_parametrize_has_none_param_id() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_foo", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_foo".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        };
        assert!(item.param_id.is_none());
        assert!(item.param_values.is_empty());
    }

    #[test]
    fn test_item_has_is_async_field() {
        let sync_item = TestItem {
            node_id: NodeId::new("test.py", "test_sync", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_sync".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        };
        assert!(!sync_item.is_async);

        let async_item = TestItem {
            node_id: NodeId::new("test.py", "test_async", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_async".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: true,
        };
        assert!(async_item.is_async);
    }

    #[test]
    fn test_node_id_new_without_param() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert_eq!(id.to_string(), "tests/test_foo.py::test_add");
    }

    #[test]
    fn test_node_id_new_with_param() {
        let id = NodeId::new("tests/test_foo.py", "test_add", Some("basic"));
        assert_eq!(id.to_string(), "tests/test_foo.py::test_add[basic]");
    }

    #[test]
    fn test_node_id_deref_contains() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert!(id.contains("test_add"));
    }

    #[test]
    fn test_node_id_clone_equals_original() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert_eq!(id, id.clone());
    }

    #[test]
    fn test_node_id_from_raw_preserves_string() {
        let id = NodeId::from_raw("tests/test_foo.py::test_bar[p0]");
        assert_eq!(id.to_string(), "tests/test_foo.py::test_bar[p0]");
    }

    #[test]
    fn test_outcome_timeout_is_hard_failure() {
        let o = TestOutcome::Timeout {
            message: "Timed out after 5s".to_string(),
        };
        assert!(o.is_hard_failure());
    }

    #[test]
    fn test_outcome_timeout_stores_message() {
        let o = TestOutcome::Timeout {
            message: "Timed out after 3s".to_string(),
        };
        match o {
            TestOutcome::Timeout { message } => assert!(message.contains("3s")),
            _ => panic!("wrong variant"),
        }
    }

    // ── dot_char ─────────────────────────────────────────────────────────────

    #[test]
    fn test_dot_char_passed_no_bare_assert() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .dot_char(),
            '.'
        );
    }

    #[test]
    fn test_dot_char_passed_bare_assert() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![5]
            }
            .dot_char(),
            '\u{00B7}'
        );
    }

    #[test]
    fn test_dot_char_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.dot_char(), 'F');
    }

    #[test]
    fn test_dot_char_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.dot_char(), 'E');
    }

    #[test]
    fn test_dot_char_skipped() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .dot_char(),
            's'
        );
    }

    #[test]
    fn test_dot_char_warned() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .dot_char(),
            '.'
        );
    }

    #[test]
    fn test_dot_char_xfailed() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .dot_char(),
            'x'
        );
    }

    #[test]
    fn test_dot_char_xpassed() {
        assert_eq!(TestOutcome::XPassed { strict: true }.dot_char(), 'X');
        assert_eq!(TestOutcome::XPassed { strict: false }.dot_char(), 'X');
    }

    #[test]
    fn test_dot_char_timeout() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .dot_char(),
            'T'
        );
    }

    // ── label ────────────────────────────────────────────────────────────────

    #[test]
    fn test_label_passed_is_empty() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .label(),
            ""
        );
    }

    #[test]
    fn test_label_passed_bare_assert_is_empty() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![5]
            }
            .label(),
            ""
        );
    }

    #[test]
    fn test_label_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.label(), "FAIL ");
    }

    #[test]
    fn test_label_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: String::new(),
            lineno: 0,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.label(), "ERROR");
    }

    #[test]
    fn test_label_skipped() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .label(),
            "SKIP "
        );
    }

    #[test]
    fn test_label_warned() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .label(),
            "WARN "
        );
    }

    #[test]
    fn test_label_xfailed() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .label(),
            "XFAIL"
        );
    }

    #[test]
    fn test_label_xpassed() {
        // Both strict variants return same text; color differs in tty.rs
        assert_eq!(TestOutcome::XPassed { strict: true }.label(), "XPASS");
        assert_eq!(TestOutcome::XPassed { strict: false }.label(), "XPASS");
    }

    #[test]
    fn test_label_timeout() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .label(),
            "TIME "
        );
    }

    // ── as_str ───────────────────────────────────────────────────────────────

    #[test]
    fn test_as_str_all_variants() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .as_str(),
            "passed"
        );
        assert_eq!(
            TestOutcome::Failed {
                message: String::new(),
                file: String::new(),
                lineno: 0,
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
                frames: vec![],
            }
            .as_str(),
            "failed"
        );
        assert_eq!(
            TestOutcome::Error {
                message: String::new(),
                file: String::new(),
                lineno: 0,
                source_line: String::new(),
                frames: vec![],
            }
            .as_str(),
            "error"
        );
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .as_str(),
            "skipped"
        );
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .as_str(),
            "warned"
        );
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .as_str(),
            "xfailed"
        );
        assert_eq!(TestOutcome::XPassed { strict: false }.as_str(), "xpassed");
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .as_str(),
            "timeout"
        );
    }

    #[test]
    fn test_collect_error_import_display_shows_path_and_traceback() {
        use camino::Utf8PathBuf;
        let err = CollectError::ImportError {
            path: Utf8PathBuf::from("tests/bad.py"),
            message: "Traceback (most recent call last):\n  File \"tests/bad.py\", line 1\nModuleNotFoundError: No module named 'foo'".to_string(),
        };
        let s = format!("{}", err);
        assert!(s.contains("tests/bad.py"), "path must appear in output");
        assert!(s.contains("Traceback"), "traceback must appear in output");
        assert!(s.contains("ModuleNotFoundError"), "error type must appear");
        assert!(
            !s.contains("TestResult("),
            "must not contain TestResult repr"
        );
        assert!(!s.contains("ImportError in"), "old format must be gone");
        // newline between path label and traceback body
        assert!(s.contains('\n'), "output must contain real newlines");
    }

    #[test]
    fn test_collect_error_pyerror_display_shows_message_directly() {
        let err = CollectError::PyError("Failed to load conftest: SyntaxError".to_string());
        let s = format!("{}", err);
        assert_eq!(s, "Failed to load conftest: SyntaxError");
        assert!(!s.starts_with("PyError:"), "PyError prefix must be gone");
    }

    #[test]
    fn test_timing_fields() {
        let t = TestTiming {
            node_id: NodeId::from_raw("tests/test_foo.py::test_a"),
            duration_ms: 42.5,
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(t.outcome, OutcomeKind::Passed);
        assert!((t.duration_ms - 42.5).abs() < 0.01);
        assert_eq!(t.node_id.to_string(), "tests/test_foo.py::test_a");
    }

    #[test]
    fn test_timing_outcome_is_enum() {
        let timing = TestTiming {
            node_id: NodeId::from_raw("test_mod::test_fn"),
            duration_ms: 42.0,
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(timing.outcome, OutcomeKind::Passed);
    }

    fn raw(status: &str) -> RawOutcome<'_> {
        RawOutcome {
            status,
            message: "msg",
            file: "test.py",
            lineno: 5,
            source_line: "assert x",
            no_message_lines: &[],
            left: "0",
            right: "1",
            op: "==",
            strict: false,
            frames: &[],
        }
    }

    #[test]
    fn from_raw_passed_returns_passed_variant() {
        assert!(matches!(
            TestOutcome::from_raw(raw("passed")),
            TestOutcome::Passed { .. }
        ));
    }

    #[test]
    fn from_raw_failed_carries_fields() {
        let o = TestOutcome::from_raw(RawOutcome {
            status: "failed",
            message: "oops",
            file: "test.py",
            lineno: 7,
            source_line: "assert x == 1",
            no_message_lines: &[],
            left: "0",
            right: "1",
            op: "==",
            strict: false,
            frames: &[],
        });
        match o {
            TestOutcome::Failed {
                message,
                lineno,
                left,
                right,
                op,
                ..
            } => {
                assert_eq!(message, "oops");
                assert_eq!(lineno, 7);
                assert_eq!(left, "0");
                assert_eq!(right, "1");
                assert_eq!(op, "==");
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn from_raw_unknown_status_becomes_error() {
        assert!(matches!(
            TestOutcome::from_raw(raw("nonsense")),
            TestOutcome::Error { .. }
        ));
    }

    #[test]
    fn from_raw_xpassed_strict_true() {
        let o = TestOutcome::from_raw(RawOutcome {
            status: "xpassed",
            strict: true,
            ..raw("xpassed")
        });
        match o {
            TestOutcome::XPassed { strict } => assert!(strict),
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn from_raw_all_known_statuses_do_not_return_error() {
        for status in &[
            "passed", "warned", "failed", "skipped", "xfailed", "xpassed", "timeout",
        ] {
            let o = TestOutcome::from_raw(raw(status));
            assert!(
                !matches!(o, TestOutcome::Error { .. }),
                "status '{}' should not map to Error",
                status
            );
        }
    }

    #[test]
    fn worker_status_strings_match_test_outcome_as_str() {
        use std::collections::HashSet;
        // These are the literal strings emitted as result.status by
        // python/oxitest/_bridge/executor.py and python/oxitest/_bridge/marks.py,
        // then forwarded by python/oxitest/_bridge/worker.py via the "outcome" JSON key.
        let worker_strings: HashSet<&str> = [
            "passed", "failed", "error", "skipped", "warned", "xfailed", "xpassed", "timeout",
        ]
        .into_iter()
        .collect();

        let outcome_strings: HashSet<&str> = [
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            TestOutcome::Failed {
                message: String::new(),
                file: String::new(),
                lineno: 0,
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
                frames: vec![],
            },
            TestOutcome::Error {
                message: String::new(),
                file: String::new(),
                lineno: 0,
                source_line: String::new(),
                frames: vec![],
            },
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![],
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::XPassed { strict: false },
            TestOutcome::Timeout {
                message: String::new(),
            },
        ]
        .iter()
        .map(TestOutcome::as_str)
        .collect();

        assert_eq!(
            worker_strings, outcome_strings,
            "Worker status strings must exactly match TestOutcome::as_str() values.\n\
             If you add a new outcome, update both worker.py and this test."
        );
    }
}
