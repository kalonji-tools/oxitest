//! Deserialization of JSON results from worker subprocesses.
//!
//! Each worker writes one JSON line per test to stdout. This module defines
//! [`WireResult`] (serde-only deserialization target) whose
//! [`into_outcome`](WireResult::into_outcome) method produces a
//! [`TestOutcome`](types::TestOutcome) directly. The serial PyO3 path
//! (`bridge.rs`) likewise produces `TestOutcome` without an intermediate enum.

use camino::Utf8PathBuf;

use crate::types::{self, FailureDiagnostic, FieldDiff, Frame, LineNo, LocalVar};

/// Wire protocol version for the worker ↔ coordinator JSON channel.
///
/// Bump when adding, removing, or changing fields in [`WorkerTask`] or
/// [`WireResult`]. The coordinator warns on version mismatch.
pub(crate) const PROTOCOL_VERSION: u32 = 1;

/// A JSON task sent to a worker subprocess over stdin.
///
/// One task describes a single module group: the module file to import, the
/// list of test items to run, the conftest files to load, and an optional
/// per-test timeout.  The worker deserializes this from a single JSON line.
#[derive(serde::Serialize)]
pub(crate) struct WorkerTask<'a> {
    pub module_path: &'a str,
    pub items: Vec<WorkerTaskItem<'a>>,
    pub conftest_paths: &'a serde_json::value::RawValue,
    pub timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub keep_tmp: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_locals: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_internals: Option<bool>,
}

/// One test item within a [`WorkerTask`].
#[derive(serde::Serialize)]
pub(crate) struct WorkerTaskItem<'a> {
    pub fn_name: &'a str,
    pub param_id: Option<&'a str>,
    pub node_id: &'a str,
    pub markers: &'a [String],
}

/// Unified intermediate frame type used by both the JSON worker path
/// (serde deserialize) and the PyO3 bridge path (FromPyObject impl in bridge.rs).
#[derive(Debug, Clone, serde::Deserialize)]
pub(crate) struct RawFrame {
    pub file: String,
    pub lineno: u64,
    pub name: String,
    pub line: String,
    #[serde(default)]
    pub locals: Vec<LocalVar>,
}

impl From<RawFrame> for Frame {
    fn from(f: RawFrame) -> Self {
        Frame {
            file: Utf8PathBuf::from(f.file),
            lineno: LineNo::new(usize::try_from(f.lineno).unwrap_or(0)),
            name: f.name,
            line: f.line,
            locals: f.locals,
        }
    }
}

/// Deserialized JSON result for a single test, written by a worker subprocess.
///
/// Workers print one `WireResult` line per test to stdout. All optional
/// diagnostic fields use `#[serde(default)]` so compact wire messages (which
/// omit falsy fields) deserialize without error. Use
/// [`into_outcome`](WireResult::into_outcome) to convert into a
/// typed `(node_id, duration_ms, TestOutcome)` tuple.
#[derive(Debug, serde::Deserialize)]
pub(crate) struct WireResult {
    pub node_id: String,
    pub outcome: types::OutcomeKind,
    pub duration_ms: f64,
    #[serde(default)]
    pub protocol_version: u32,
    #[serde(default)]
    pub failure_repr: Option<String>,
    // Structured diagnostic fields from worker JSON
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub file: Option<String>,
    #[serde(default)]
    pub lineno: Option<u64>, // u64 because JSON integers are u64; convert to usize in into_outcome()
    #[serde(default)]
    pub source_line: Option<String>,
    #[serde(default)]
    pub no_message_lines: Vec<i64>, // list[int] from Python — line numbers
    #[serde(default)]
    pub left: Option<String>,
    #[serde(default)]
    pub right: Option<String>,
    #[serde(default)]
    pub op: Option<String>,
    #[serde(default)]
    pub strict: bool,
    #[serde(default)]
    pub frames: Vec<RawFrame>,
    #[serde(default)]
    pub field_diffs: Vec<FieldDiff>,
}

impl WireResult {
    /// Convert the flat wire representation into a typed [`TestOutcome`](types::TestOutcome).
    ///
    /// Consumes self — `WireResult` is a transient deserialization target.
    /// Returns `(node_id, duration_ms, outcome)`.
    pub(crate) fn into_outcome(self) -> (String, f64, types::TestOutcome) {
        let no_message_lines: Vec<usize> = self
            .no_message_lines
            .iter()
            .filter(|&&n| n > 0)
            .map(|&n| usize::try_from(n).unwrap_or(0))
            .collect();

        let frames: Vec<Frame> = self.frames.into_iter().map(Into::into).collect();
        let lineno = LineNo::new(self.lineno.map_or(0, |n| usize::try_from(n).unwrap_or(0)));
        let file = Utf8PathBuf::from(self.file.unwrap_or_default());
        let source_line = self.source_line.unwrap_or_default();

        let outcome = match self.outcome {
            types::OutcomeKind::Passed => types::TestOutcome::Passed { no_message_lines },
            types::OutcomeKind::Failed => types::TestOutcome::Failed(Box::new(FailureDiagnostic {
                message: self.message.unwrap_or_default(),
                file,
                lineno,
                source_line,
                left: self.left.unwrap_or_default(),
                right: self.right.unwrap_or_default(),
                op: self.op.unwrap_or_default(),
                frames,
                field_diffs: self.field_diffs,
            })),
            types::OutcomeKind::Error => {
                types::TestOutcome::Error(Box::new(FailureDiagnostic::error(
                    self.message.unwrap_or_default(),
                    file,
                    lineno,
                    source_line,
                    frames,
                )))
            }
            types::OutcomeKind::Skipped => types::TestOutcome::Skipped {
                reason: self.failure_repr.unwrap_or_default(),
            },
            types::OutcomeKind::XFailed => types::TestOutcome::XFailed {
                reason: self.failure_repr.unwrap_or_default(),
            },
            types::OutcomeKind::XPassed => types::TestOutcome::XPassed {
                strict: self.strict,
            },
            types::OutcomeKind::Warned => types::TestOutcome::Warned {
                reason: self.message.unwrap_or_default(),
                no_message_lines,
            },
            types::OutcomeKind::Timeout => types::TestOutcome::Timeout {
                message: self.failure_repr.unwrap_or_default(),
            },
            types::OutcomeKind::Flaky | types::OutcomeKind::Unknown => {
                if self.outcome == types::OutcomeKind::Unknown {
                    tracing::warn!(
                        outcome = %self.outcome,
                        "Unknown outcome string from worker — treating as error"
                    );
                }
                types::TestOutcome::Error(Box::new(FailureDiagnostic::error(
                    self.message.unwrap_or_default(),
                    file,
                    lineno,
                    source_line,
                    frames,
                )))
            }
        };

        (self.node_id, self.duration_ms, outcome)
    }
}

#[cfg(test)]
mod frame_tests {
    use super::*;
    use crate::types::Frame;

    #[test]
    fn frames_deserialized_from_json() {
        let json = r#"{
            "node_id": "t.py::test_deep",
            "outcome": "failed",
            "duration_ms": 1.5,
            "file": "t.py",
            "lineno": 2,
            "source_line": "assert x > 0",
            "frames": [
                {"file": "t.py", "lineno": 8, "name": "test_deep", "line": "compute(5)"},
                {"file": "t.py", "lineno": 5, "name": "compute", "line": "return helper(x)"},
                {"file": "t.py", "lineno": 2, "name": "helper", "line": "assert x > 0"}
            ]
        }"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        assert_eq!(r.frames.len(), 3);
        assert_eq!(r.frames[0].file, "t.py");
        assert_eq!(r.frames[0].lineno, 8);
        assert_eq!(r.frames[0].name, "test_deep");
        assert_eq!(r.frames[0].line, "compute(5)");
        assert_eq!(r.frames[2].name, "helper");
    }

    #[test]
    fn frames_threaded_to_outcome() {
        let json = r#"{
            "node_id": "t.py::test_f",
            "outcome": "failed",
            "duration_ms": 0.5,
            "file": "t.py",
            "lineno": 3,
            "message": "oops",
            "frames": [
                {"file": "t.py", "lineno": 10, "name": "test_f", "line": "do_thing()"},
                {"file": "t.py", "lineno": 3, "name": "do_thing", "line": "raise ValueError"}
            ]
        }"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Failed(d) => {
                assert_eq!(d.frames.len(), 2);
                assert_eq!(
                    d.frames[0],
                    Frame {
                        file: Utf8PathBuf::from("t.py"),
                        lineno: LineNo::new(10),
                        name: "test_f".to_string(),
                        line: "do_thing()".to_string(),
                        locals: vec![],
                    }
                );
                assert_eq!(
                    d.frames[1],
                    Frame {
                        file: Utf8PathBuf::from("t.py"),
                        lineno: LineNo::new(3),
                        name: "do_thing".to_string(),
                        line: "raise ValueError".to_string(),
                        locals: vec![],
                    }
                );
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn missing_frames_defaults_to_empty() {
        let json = r#"{"node_id":"t","outcome":"failed","duration_ms":0.0}"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        assert!(r.frames.is_empty());
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Failed(d) => assert!(d.frames.is_empty()),
            other => panic!("expected Failed, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod lineno_cast_tests {
    use super::*;

    fn passed_result_with_lineno(lineno: Option<u64>) -> WireResult {
        serde_json::from_str(&format!(
            r#"{{"node_id":"t","outcome":"passed","duration_ms":0.0,"lineno":{}}}"#,
            match lineno {
                Some(n) => n.to_string(),
                None => "null".to_string(),
            }
        ))
        .unwrap()
    }

    #[test]
    fn lineno_none_maps_to_zero() {
        let result = passed_result_with_lineno(None);
        assert_eq!(result.lineno, None);
        // into_outcome() must not panic
        let _ = result.into_outcome();
    }

    #[test]
    fn lineno_small_value_passes_through() {
        let r: WireResult = serde_json::from_str(
            r#"{"node_id":"t","outcome":"failed","duration_ms":0.0,
                "lineno":42,"file":"t.py","source_line":"assert x"}"#,
        )
        .unwrap();
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Failed(d) => {
                assert_eq!(d.lineno, LineNo::new(42));
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn lineno_u32_max_does_not_panic() {
        let result = passed_result_with_lineno(Some(u32::MAX as u64));
        let _ = result.into_outcome();
    }

    #[test]
    fn lineno_u64_max_does_not_panic() {
        let result = passed_result_with_lineno(Some(u64::MAX));
        let _ = result.into_outcome();
    }
}

#[cfg(test)]
mod wire_round_trip_tests {
    use super::*;

    fn deser(json: &str) -> WireResult {
        serde_json::from_str(json).expect("valid JSON")
    }

    #[test]
    fn passed_minimal() {
        let r = deser(r#"{"node_id":"t.py::test_a","outcome":"passed","duration_ms":1.2}"#);
        assert_eq!(r.node_id, "t.py::test_a");
        assert_eq!(r.outcome, types::OutcomeKind::Passed);
        assert!((r.duration_ms - 1.2).abs() < 1e-9);
        assert!(r.failure_repr.is_none());
        assert!(r.message.is_none());
        assert!(r.file.is_none());
        assert!(r.lineno.is_none());
        assert!(r.source_line.is_none());
        assert!(r.no_message_lines.is_empty());
        assert!(r.left.is_none());
        assert!(r.right.is_none());
        assert!(r.op.is_none());
        assert!(!r.strict);
        assert!(r.frames.is_empty());
    }

    #[test]
    fn failed_full() {
        let json = r#"{
            "node_id": "t.py::test_b",
            "outcome": "failed",
            "duration_ms": 5.0,
            "failure_repr": "AssertionError: 1 != 2",
            "message": "assert 1 == 2",
            "file": "t.py",
            "lineno": 10,
            "source_line": "assert x == 2",
            "left": "1",
            "right": "2",
            "op": "=="
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Failed);
        assert_eq!(r.failure_repr.as_deref(), Some("AssertionError: 1 != 2"));
        assert_eq!(r.message.as_deref(), Some("assert 1 == 2"));
        assert_eq!(r.file.as_deref(), Some("t.py"));
        assert_eq!(r.lineno, Some(10));
        assert_eq!(r.source_line.as_deref(), Some("assert x == 2"));
        assert_eq!(r.left.as_deref(), Some("1"));
        assert_eq!(r.right.as_deref(), Some("2"));
        assert_eq!(r.op.as_deref(), Some("=="));
    }

    #[test]
    fn error_full() {
        let json = r#"{
            "node_id": "t.py::test_c",
            "outcome": "error",
            "duration_ms": 2.5,
            "message": "RuntimeError: boom",
            "file": "t.py",
            "lineno": 7,
            "frames": [
                {"file": "t.py", "lineno": 7, "name": "test_c", "line": "raise RuntimeError"}
            ]
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Error);
        assert_eq!(r.message.as_deref(), Some("RuntimeError: boom"));
        assert_eq!(r.file.as_deref(), Some("t.py"));
        assert_eq!(r.lineno, Some(7));
        assert_eq!(r.frames.len(), 1);
        assert_eq!(r.frames[0].name, "test_c");
    }

    #[test]
    fn skipped() {
        let json = r#"{
            "node_id": "t.py::test_d",
            "outcome": "skipped",
            "duration_ms": 0.1,
            "failure_repr": "Skipped: needs network"
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Skipped);
        assert_eq!(r.failure_repr.as_deref(), Some("Skipped: needs network"));
    }

    #[test]
    fn xfailed() {
        let json = r#"{
            "node_id": "t.py::test_e",
            "outcome": "xfailed",
            "duration_ms": 0.3,
            "failure_repr": "known bug #42"
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::XFailed);
        assert_eq!(r.failure_repr.as_deref(), Some("known bug #42"));
    }

    #[test]
    fn xpassed_strict() {
        let json = r#"{
            "node_id": "t.py::test_f",
            "outcome": "xpassed",
            "duration_ms": 0.4,
            "strict": true
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::XPassed);
        assert!(r.strict);
    }

    #[test]
    fn xpassed_lenient() {
        let json = r#"{
            "node_id": "t.py::test_g",
            "outcome": "xpassed",
            "duration_ms": 0.4
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::XPassed);
        assert!(!r.strict);
    }

    #[test]
    fn warned() {
        let json = r#"{
            "node_id": "t.py::test_h",
            "outcome": "warned",
            "duration_ms": 1.1,
            "message": "DeprecationWarning: old_api is deprecated"
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Warned);
        assert_eq!(
            r.message.as_deref(),
            Some("DeprecationWarning: old_api is deprecated")
        );
    }

    #[test]
    fn timeout() {
        let json = r#"{
            "node_id": "t.py::test_i",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "failure_repr": "Test timed out after 5s"
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Timeout);
        assert_eq!(r.failure_repr.as_deref(), Some("Test timed out after 5s"));
    }

    #[test]
    fn unknown_outcome() {
        let json = r#"{
            "node_id": "t.py::test_j",
            "outcome": "completely_made_up",
            "duration_ms": 0.0
        }"#;
        let r = deser(json);
        assert_eq!(r.outcome, types::OutcomeKind::Unknown);
    }
}

#[cfg(test)]
mod compact_and_error_tests {
    use super::*;

    #[test]
    fn compact_passed_only_required_fields() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert!(r.failure_repr.is_none());
        assert!(r.message.is_none());
        assert!(r.file.is_none());
        assert!(r.lineno.is_none());
        assert!(r.source_line.is_none());
        assert!(r.no_message_lines.is_empty());
        assert!(r.left.is_none());
        assert!(r.right.is_none());
        assert!(r.op.is_none());
        assert!(!r.strict);
        assert!(r.frames.is_empty());
    }

    #[test]
    fn no_message_lines_deserializes_as_vec() {
        let json = r#"{
            "node_id": "t",
            "outcome": "passed",
            "duration_ms": 0.0,
            "no_message_lines": [5, 10, 15]
        }"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert_eq!(r.no_message_lines, vec![5_i64, 10_i64, 15_i64]);
    }

    #[test]
    fn missing_node_id_is_error() {
        let json = r#"{"outcome":"passed","duration_ms":0.0}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn missing_outcome_is_error() {
        let json = r#"{"node_id":"t","duration_ms":0.0}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn missing_duration_ms_is_error() {
        let json = r#"{"node_id":"t","outcome":"passed"}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn truncated_json_is_error() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn wrong_type_for_duration_is_error() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":"slow"}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn extra_unknown_fields_are_ignored() {
        let json = r#"{
            "node_id": "t",
            "outcome": "passed",
            "duration_ms": 0.5,
            "future_field": "some_value",
            "another_extra": 42
        }"#;
        let r: WireResult = serde_json::from_str(json).expect("extra fields should be ignored");
        assert_eq!(r.outcome, types::OutcomeKind::Passed);
        assert!((r.duration_ms - 0.5).abs() < 1e-9);
    }

    #[test]
    fn protocol_version_round_trips() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0,"protocol_version":1}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert_eq!(r.protocol_version, PROTOCOL_VERSION);
    }

    #[test]
    fn missing_protocol_version_defaults_to_zero() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert_eq!(r.protocol_version, 0);
    }
}

#[cfg(test)]
mod outcome_conversion_tests {
    use super::*;

    fn make_result(json: &str) -> WireResult {
        serde_json::from_str(json).expect("valid JSON")
    }

    #[test]
    fn passed_to_outcome() {
        let r = make_result(
            r#"{"node_id":"t","outcome":"passed","duration_ms":0.0,"no_message_lines":[3,7]}"#,
        );
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Passed { no_message_lines } => {
                assert_eq!(no_message_lines, vec![3usize, 7usize]);
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn failed_to_outcome_uses_message_not_failure_repr() {
        let json = r#"{
            "node_id": "t",
            "outcome": "failed",
            "duration_ms": 1.0,
            "message": "structured message",
            "failure_repr": "fallback repr"
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Failed(d) => {
                assert_eq!(d.message, "structured message");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn skipped_to_outcome_uses_failure_repr() {
        let json = r#"{
            "node_id": "t",
            "outcome": "skipped",
            "duration_ms": 0.0,
            "failure_repr": "Skipped: no network",
            "message": "should not be used"
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Skipped { reason } => {
                assert_eq!(reason, "Skipped: no network");
            }
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn xfailed_to_outcome_uses_failure_repr() {
        let json = r#"{
            "node_id": "t",
            "outcome": "xfailed",
            "duration_ms": 0.0,
            "failure_repr": "known bug #99",
            "message": "should not be used"
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::XFailed { reason } => {
                assert_eq!(reason, "known bug #99");
            }
            other => panic!("expected XFailed, got {other:?}"),
        }
    }

    #[test]
    fn xpassed_strict_to_outcome() {
        let json = r#"{
            "node_id": "t",
            "outcome": "xpassed",
            "duration_ms": 0.0,
            "strict": true
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::XPassed { strict } => {
                assert!(strict);
            }
            other => panic!("expected XPassed, got {other:?}"),
        }
    }

    #[test]
    fn timeout_to_outcome_uses_failure_repr() {
        let json = r#"{
            "node_id": "t",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "failure_repr": "Test timed out after 5s",
            "message": "should not be used"
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Timeout { message } => {
                assert_eq!(message, "Test timed out after 5s");
            }
            other => panic!("expected Timeout, got {other:?}"),
        }
    }

    #[test]
    fn warned_to_outcome_uses_message() {
        let json = r#"{
            "node_id": "t",
            "outcome": "warned",
            "duration_ms": 0.5,
            "message": "DeprecationWarning: use new_api instead"
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Warned { reason, .. } => {
                assert_eq!(reason, "DeprecationWarning: use new_api instead");
            }
            other => panic!("expected Warned, got {other:?}"),
        }
    }

    #[test]
    fn error_to_outcome_carries_frames() {
        let json = r#"{
            "node_id": "t",
            "outcome": "error",
            "duration_ms": 0.5,
            "message": "ImportError: no module",
            "frames": [
                {"file": "conftest.py", "lineno": 3, "name": "<module>", "line": "import missing"},
                {"file": "conftest.py", "lineno": 1, "name": "setup", "line": "from missing import x"}
            ]
        }"#;
        let r = make_result(json);
        let (_, _, outcome) = r.into_outcome();
        match outcome {
            types::TestOutcome::Error(d) => {
                assert_eq!(d.message, "ImportError: no module");
                assert_eq!(d.frames.len(), 2);
                assert_eq!(d.frames[0].file, "conftest.py");
                assert_eq!(d.frames[0].lineno, LineNo::new(3));
                assert_eq!(d.frames[0].name, "<module>");
                assert_eq!(d.frames[1].name, "setup");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod sentinel_tests {
    use crate::types::{LineNo, TestOutcome};

    #[test]
    fn error_sentinel_builds_error_variant() {
        match TestOutcome::error_sentinel("boom".into()) {
            TestOutcome::Error(d) => {
                assert_eq!(d.message, "boom");
                assert_eq!(d.file, "");
                assert_eq!(d.lineno, LineNo::ZERO);
                assert_eq!(d.source_line, "");
                assert!(d.frames.is_empty());
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn timed_out_sentinel_contains_duration_message() {
        let (outcome, dur) = TestOutcome::timed_out_sentinel(std::time::Duration::from_secs(30));
        match outcome {
            TestOutcome::Error(d) => assert!(d.message.contains("30")),
            other => panic!("expected Error, got {other:?}"),
        }
        assert!((dur - 30_000.0).abs() < 1e-9);
    }

    #[test]
    fn crashed_sentinel_contains_crash_message() {
        match TestOutcome::crashed_sentinel() {
            TestOutcome::Error(d) => {
                assert!(d.message.contains("unexpectedly"));
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod wire_conversion_tests {
    use super::*;
    use crate::types::{LineNo, TestOutcome};

    fn deser(json: &str) -> WireResult {
        serde_json::from_str(json).expect("valid JSON")
    }

    #[test]
    fn wire_failed_converts_to_worker_outcome() {
        let wire = deser(
            r#"{
            "node_id": "t.py::test_b",
            "outcome": "failed",
            "duration_ms": 5.0,
            "message": "assert 1 == 2",
            "file": "t.py",
            "lineno": 10,
            "source_line": "assert x == 2",
            "left": "1",
            "right": "2",
            "op": "=="
        }"#,
        );
        let (node_id, duration_ms, outcome) = wire.into_outcome();
        assert_eq!(node_id, "t.py::test_b");
        assert!((duration_ms - 5.0).abs() < 1e-9);
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.message, "assert 1 == 2");
                assert_eq!(d.file, "t.py");
                assert_eq!(d.lineno, LineNo::new(10));
                assert_eq!(d.left, "1");
                assert_eq!(d.right, "2");
                assert_eq!(d.op, "==");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn wire_passed_converts_with_no_message_lines() {
        let wire = deser(
            r#"{"node_id":"t","outcome":"passed","duration_ms":0.0,"no_message_lines":[3,7]}"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::Passed { no_message_lines } => {
                assert_eq!(no_message_lines, vec![3, 7]);
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn wire_skipped_uses_failure_repr() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "skipped",
            "duration_ms": 0.0,
            "failure_repr": "Skipped: needs network",
            "message": "should not be used"
        }"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::Skipped { reason } => assert_eq!(reason, "Skipped: needs network"),
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn wire_xfailed_uses_failure_repr() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "xfailed",
            "duration_ms": 0.0,
            "failure_repr": "known bug #99"
        }"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::XFailed { reason } => assert_eq!(reason, "known bug #99"),
            other => panic!("expected XFailed, got {other:?}"),
        }
    }

    #[test]
    fn wire_timeout_uses_failure_repr() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "failure_repr": "Test timed out after 5s"
        }"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::Timeout { message } => assert_eq!(message, "Test timed out after 5s"),
            other => panic!("expected Timeout, got {other:?}"),
        }
    }

    #[test]
    fn wire_warned_uses_message() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "warned",
            "duration_ms": 0.5,
            "message": "DeprecationWarning: use new_api"
        }"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::Warned { reason, .. } => {
                assert_eq!(reason, "DeprecationWarning: use new_api");
            }
            other => panic!("expected Warned, got {other:?}"),
        }
    }

    #[test]
    fn wire_error_carries_frames() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "error",
            "duration_ms": 0.5,
            "message": "ImportError: no module",
            "frames": [
                {"file": "conftest.py", "lineno": 3, "name": "<module>", "line": "import missing"}
            ]
        }"#,
        );
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::Error(d) => {
                assert_eq!(d.message, "ImportError: no module");
                assert_eq!(d.frames.len(), 1);
                assert_eq!(d.frames[0].name, "<module>");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn wire_unknown_outcome_maps_to_error() {
        let wire = deser(r#"{"node_id":"t","outcome":"completely_made_up","duration_ms":0.0}"#);
        let (_, _, outcome) = wire.into_outcome();
        assert!(matches!(outcome, TestOutcome::Error(..)));
    }

    #[test]
    fn wire_xpassed_strict() {
        let wire = deser(r#"{"node_id":"t","outcome":"xpassed","duration_ms":0.0,"strict":true}"#);
        let (_, _, outcome) = wire.into_outcome();
        match outcome {
            TestOutcome::XPassed { strict } => assert!(strict),
            other => panic!("expected XPassed, got {other:?}"),
        }
    }
}
