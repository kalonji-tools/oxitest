//! Deserialization of JSON results from worker subprocesses.
//!
//! Each worker writes one JSON line per test to stdout. This module defines
//! [`WireResult`] (serde-only deserialization target) whose
//! [`into_outcome`](WireResult::into_outcome) method produces a
//! [`TestOutcome`](types::TestOutcome) directly. The serial PyO3 path
//! (`bridge.rs`) likewise produces `TestOutcome` without an intermediate enum.

use camino::Utf8PathBuf;

use crate::types::{self, ComparisonDetail, FailureDiagnostic, FieldDiff, Frame, LineNo, LocalVar};

/// Wire protocol version for the worker ↔ coordinator JSON channel.
///
/// Bump when adding, removing, or changing fields in [`WorkerTask`] or
/// [`WireResult`]. The coordinator warns on version mismatch.
pub(crate) const PROTOCOL_VERSION: u32 = 2;

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
    pub markers: Vec<&'a str>,
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
/// Workers print one `WireResult` line per test to stdout. The `outcome` field
/// drives serde variant selection via `#[serde(tag = "outcome")]`. Each variant
/// carries only the fields relevant to that outcome kind.
///
/// Use [`into_outcome`](WireResult::into_outcome) to convert into a
/// typed [`ResolvedOutcome`](types::ResolvedOutcome).
#[derive(Debug, serde::Deserialize)]
#[serde(tag = "outcome")]
pub(crate) enum WireResult {
    #[serde(rename = "passed")]
    Passed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        no_message_lines: Vec<i64>,
    },
    #[serde(rename = "failed")]
    Failed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        file: String,
        #[serde(default)]
        lineno: Option<u64>,
        #[serde(default)]
        source_line: String,
        #[serde(default)]
        frames: Vec<RawFrame>,
        #[serde(default)]
        left: String,
        #[serde(default)]
        right: String,
        #[serde(default)]
        op: String,
        #[serde(default)]
        field_diffs: Vec<FieldDiff>,
    },
    #[serde(rename = "error")]
    Error {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        file: String,
        #[serde(default)]
        lineno: Option<u64>,
        #[serde(default)]
        source_line: String,
        #[serde(default)]
        frames: Vec<RawFrame>,
    },
    #[serde(rename = "skipped")]
    Skipped {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
    #[serde(rename = "warned")]
    Warned {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        no_message_lines: Vec<i64>,
    },
    #[serde(rename = "xfailed")]
    XFailed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
    #[serde(rename = "xpassed")]
    XPassed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        strict: bool,
    },
    #[serde(rename = "timeout")]
    Timeout {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
}

impl WireResult {
    /// Extract the protocol version from any variant.
    pub(crate) fn protocol_version(&self) -> u32 {
        match self {
            Self::Passed {
                protocol_version, ..
            }
            | Self::Failed {
                protocol_version, ..
            }
            | Self::Error {
                protocol_version, ..
            }
            | Self::Skipped {
                protocol_version, ..
            }
            | Self::Warned {
                protocol_version, ..
            }
            | Self::XFailed {
                protocol_version, ..
            }
            | Self::XPassed {
                protocol_version, ..
            }
            | Self::Timeout {
                protocol_version, ..
            } => *protocol_version,
        }
    }

    /// Convert the typed wire representation into a [`ResolvedOutcome`](types::ResolvedOutcome).
    ///
    /// Consumes self — `WireResult` is a transient deserialization target.
    pub(crate) fn into_outcome(self) -> types::ResolvedOutcome {
        match self {
            Self::Passed {
                node_id,
                duration_ms,
                no_message_lines,
                ..
            } => {
                let tips = filter_tips(no_message_lines);
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: types::TestOutcome::Passed { tips },
                }
            }
            Self::Failed {
                node_id,
                duration_ms,
                message,
                file,
                lineno,
                source_line,
                frames,
                left,
                right,
                op,
                field_diffs,
                ..
            } => {
                let comparison = Some(ComparisonDetail {
                    left,
                    right,
                    op,
                    field_diffs,
                });
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: types::TestOutcome::Failed(Box::new(build_diagnostic(
                        message,
                        Utf8PathBuf::from(file),
                        LineNo::new(lineno.map_or(0, |n| usize::try_from(n).unwrap_or(0))),
                        source_line,
                        frames.into_iter().map(Into::into).collect(),
                        comparison,
                    ))),
                }
            }
            Self::Error {
                node_id,
                duration_ms,
                message,
                file,
                lineno,
                source_line,
                frames,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::Error(Box::new(build_diagnostic(
                    message,
                    Utf8PathBuf::from(file),
                    LineNo::new(lineno.map_or(0, |n| usize::try_from(n).unwrap_or(0))),
                    source_line,
                    frames.into_iter().map(Into::into).collect(),
                    None,
                ))),
            },
            Self::Skipped {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::Skipped { reason: message },
            },
            Self::Warned {
                node_id,
                duration_ms,
                message,
                no_message_lines,
                ..
            } => {
                let tips = filter_tips(no_message_lines);
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: types::TestOutcome::Warned {
                        reason: message,
                        tips,
                    },
                }
            }
            Self::XFailed {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::XFailed { reason: message },
            },
            Self::XPassed {
                node_id,
                duration_ms,
                strict,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::XPassed { strict },
            },
            Self::Timeout {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::Timeout { message },
            },
        }
    }
}

/// Filter no_message_lines into tips (positive values only).
fn filter_tips(lines: Vec<i64>) -> Option<Box<[usize]>> {
    let filtered: Vec<usize> = lines
        .iter()
        .filter(|&&n| n > 0)
        .map(|&n| usize::try_from(n).unwrap_or(0))
        .collect();
    if filtered.is_empty() {
        None
    } else {
        Some(filtered.into_boxed_slice())
    }
}

/// Shared construction of `FailureDiagnostic` from raw fields.
///
/// Used by both the JSON worker path (`into_outcome`) and the PyO3 bridge
/// path (`convert_test_result`) to prevent drift between the two.
pub(crate) fn build_diagnostic(
    message: String,
    file: Utf8PathBuf,
    lineno: LineNo,
    source_line: String,
    frames: Vec<Frame>,
    comparison: Option<ComparisonDetail>,
) -> FailureDiagnostic {
    FailureDiagnostic {
        message,
        file,
        lineno,
        source_line,
        frames,
        comparison,
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
        match &r {
            WireResult::Failed { frames, .. } => {
                assert_eq!(frames.len(), 3);
                assert_eq!(frames[0].file, "t.py");
                assert_eq!(frames[0].lineno, 8);
                assert_eq!(frames[0].name, "test_deep");
                assert_eq!(frames[0].line, "compute(5)");
                assert_eq!(frames[2].name, "helper");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
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
        let outcome = r.into_outcome().outcome;
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
        match &r {
            WireResult::Failed { frames, .. } => assert!(frames.is_empty()),
            other => panic!("expected Failed, got {other:?}"),
        }
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Failed(d) => assert!(d.frames.is_empty()),
            other => panic!("expected Failed, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod lineno_cast_tests {
    use super::*;

    /// Build a Failed result with a given lineno (lineno only exists on Failed/Error variants).
    fn failed_result_with_lineno(lineno: Option<u64>) -> WireResult {
        serde_json::from_str(&format!(
            r#"{{"node_id":"t","outcome":"failed","duration_ms":0.0,"lineno":{}}}"#,
            match lineno {
                Some(n) => n.to_string(),
                None => "null".to_string(),
            }
        ))
        .unwrap()
    }

    #[test]
    fn lineno_none_maps_to_zero() {
        let result = failed_result_with_lineno(None);
        match &result {
            WireResult::Failed { lineno, .. } => assert_eq!(*lineno, None),
            other => panic!("expected Failed, got {other:?}"),
        }
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
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Failed(d) => {
                assert_eq!(d.lineno, LineNo::new(42));
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn lineno_u32_max_does_not_panic() {
        let result = failed_result_with_lineno(Some(u32::MAX as u64));
        let _ = result.into_outcome();
    }

    #[test]
    fn lineno_u64_max_does_not_panic() {
        let result = failed_result_with_lineno(Some(u64::MAX));
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
        match r {
            WireResult::Passed {
                ref node_id,
                duration_ms,
                no_message_lines,
                ..
            } => {
                assert_eq!(node_id, "t.py::test_a");
                assert!((duration_ms - 1.2).abs() < 1e-9);
                assert!(no_message_lines.is_empty());
            }
            ref other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn failed_full() {
        let json = r#"{
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
        }"#;
        let r = deser(json);
        match &r {
            WireResult::Failed {
                message,
                file,
                lineno,
                source_line,
                left,
                right,
                op,
                ..
            } => {
                assert_eq!(message, "assert 1 == 2");
                assert_eq!(file, "t.py");
                assert_eq!(*lineno, Some(10));
                assert_eq!(source_line, "assert x == 2");
                assert_eq!(left, "1");
                assert_eq!(right, "2");
                assert_eq!(op, "==");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
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
        match &r {
            WireResult::Error {
                message,
                file,
                lineno,
                frames,
                ..
            } => {
                assert_eq!(message, "RuntimeError: boom");
                assert_eq!(file, "t.py");
                assert_eq!(*lineno, Some(7));
                assert_eq!(frames.len(), 1);
                assert_eq!(frames[0].name, "test_c");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn skipped() {
        let json = r#"{
            "node_id": "t.py::test_d",
            "outcome": "skipped",
            "duration_ms": 0.1,
            "message": "needs network"
        }"#;
        let r = deser(json);
        match &r {
            WireResult::Skipped { message, .. } => {
                assert_eq!(message, "needs network");
            }
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn xfailed() {
        let json = r#"{
            "node_id": "t.py::test_e",
            "outcome": "xfailed",
            "duration_ms": 0.3,
            "message": "known bug #42"
        }"#;
        let r = deser(json);
        match &r {
            WireResult::XFailed { message, .. } => {
                assert_eq!(message, "known bug #42");
            }
            other => panic!("expected XFailed, got {other:?}"),
        }
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
        match &r {
            WireResult::XPassed { strict, .. } => assert!(*strict),
            other => panic!("expected XPassed, got {other:?}"),
        }
    }

    #[test]
    fn xpassed_lenient() {
        let json = r#"{
            "node_id": "t.py::test_g",
            "outcome": "xpassed",
            "duration_ms": 0.4
        }"#;
        let r = deser(json);
        match &r {
            WireResult::XPassed { strict, .. } => assert!(!*strict),
            other => panic!("expected XPassed, got {other:?}"),
        }
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
        match &r {
            WireResult::Warned { message, .. } => {
                assert_eq!(message, "DeprecationWarning: old_api is deprecated");
            }
            other => panic!("expected Warned, got {other:?}"),
        }
    }

    #[test]
    fn timeout() {
        let json = r#"{
            "node_id": "t.py::test_i",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "message": "Test timed out after 5s"
        }"#;
        let r = deser(json);
        match &r {
            WireResult::Timeout { message, .. } => {
                assert_eq!(message, "Test timed out after 5s");
            }
            other => panic!("expected Timeout, got {other:?}"),
        }
    }

    #[test]
    fn unknown_outcome_is_deser_error() {
        // With internally-tagged enum, unknown outcome values fail deserialization.
        let json = r#"{
            "node_id": "t.py::test_j",
            "outcome": "completely_made_up",
            "duration_ms": 0.0
        }"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }
}

#[cfg(test)]
mod compact_and_error_tests {
    use super::*;

    #[test]
    fn compact_passed_only_required_fields() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        match &r {
            WireResult::Passed {
                no_message_lines, ..
            } => {
                assert!(no_message_lines.is_empty());
            }
            other => panic!("expected Passed, got {other:?}"),
        }
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
        match &r {
            WireResult::Passed {
                no_message_lines, ..
            } => {
                assert_eq!(*no_message_lines, vec![5_i64, 10_i64, 15_i64]);
            }
            other => panic!("expected Passed, got {other:?}"),
        }
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
        assert!(matches!(r, WireResult::Passed { .. }));
        match &r {
            WireResult::Passed { duration_ms, .. } => {
                assert!((*duration_ms - 0.5).abs() < 1e-9);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn protocol_version_round_trips() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0,"protocol_version":2}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert_eq!(r.protocol_version(), PROTOCOL_VERSION);
    }

    #[test]
    fn missing_protocol_version_defaults_to_zero() {
        let json = r#"{"node_id":"t","outcome":"passed","duration_ms":0.0}"#;
        let r: WireResult = serde_json::from_str(json).expect("valid JSON");
        assert_eq!(r.protocol_version(), 0);
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
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Passed { tips } => {
                assert_eq!(tips.as_deref(), Some([3usize, 7].as_slice()));
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn failed_to_outcome_uses_message() {
        let json = r#"{
            "node_id": "t",
            "outcome": "failed",
            "duration_ms": 1.0,
            "message": "structured message"
        }"#;
        let r = make_result(json);
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Failed(d) => {
                assert_eq!(d.message, "structured message");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn skipped_to_outcome_uses_message() {
        let json = r#"{
            "node_id": "t",
            "outcome": "skipped",
            "duration_ms": 0.0,
            "message": "needs network"
        }"#;
        let r = make_result(json);
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Skipped { reason } => {
                assert_eq!(reason, "needs network");
            }
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn xfailed_to_outcome_uses_message() {
        let json = r#"{
            "node_id": "t",
            "outcome": "xfailed",
            "duration_ms": 0.0,
            "message": "known bug #99"
        }"#;
        let r = make_result(json);
        let outcome = r.into_outcome().outcome;
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
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::XPassed { strict } => {
                assert!(strict);
            }
            other => panic!("expected XPassed, got {other:?}"),
        }
    }

    #[test]
    fn timeout_to_outcome_uses_message() {
        let json = r#"{
            "node_id": "t",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "message": "Test timed out after 5s"
        }"#;
        let r = make_result(json);
        let outcome = r.into_outcome().outcome;
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
        let outcome = r.into_outcome().outcome;
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
        let outcome = r.into_outcome().outcome;
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
        assert!((dur.as_f64() - 30_000.0).abs() < 1e-9);
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
        let resolved = wire.into_outcome();
        assert_eq!(resolved.node_id.as_ref(), "t.py::test_b");
        assert!((resolved.duration_ms.as_f64() - 5.0).abs() < 1e-9);
        match resolved.outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.message, "assert 1 == 2");
                assert_eq!(d.file, "t.py");
                assert_eq!(d.lineno, LineNo::new(10));
                let cmp = d.comparison.as_ref().expect("expected comparison");
                assert_eq!(cmp.left, "1");
                assert_eq!(cmp.right, "2");
                assert_eq!(cmp.op, "==");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn wire_passed_converts_with_tips() {
        let wire = deser(
            r#"{"node_id":"t","outcome":"passed","duration_ms":0.0,"no_message_lines":[3,7]}"#,
        );
        let outcome = wire.into_outcome().outcome;
        match outcome {
            TestOutcome::Passed { tips } => {
                assert_eq!(tips.as_deref(), Some([3usize, 7].as_slice()));
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn wire_skipped_uses_message() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "skipped",
            "duration_ms": 0.0,
            "message": "needs network"
        }"#,
        );
        let outcome = wire.into_outcome().outcome;
        match outcome {
            TestOutcome::Skipped { reason } => assert_eq!(reason, "needs network"),
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn wire_xfailed_uses_message() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "xfailed",
            "duration_ms": 0.0,
            "message": "known bug #99"
        }"#,
        );
        let outcome = wire.into_outcome().outcome;
        match outcome {
            TestOutcome::XFailed { reason } => assert_eq!(reason, "known bug #99"),
            other => panic!("expected XFailed, got {other:?}"),
        }
    }

    #[test]
    fn wire_timeout_uses_message() {
        let wire = deser(
            r#"{
            "node_id": "t",
            "outcome": "timeout",
            "duration_ms": 5000.0,
            "message": "Test timed out after 5s"
        }"#,
        );
        let outcome = wire.into_outcome().outcome;
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
        let outcome = wire.into_outcome().outcome;
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
        let outcome = wire.into_outcome().outcome;
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
    fn wire_unknown_outcome_is_deser_error() {
        let json = r#"{"node_id":"t","outcome":"completely_made_up","duration_ms":0.0}"#;
        assert!(serde_json::from_str::<WireResult>(json).is_err());
    }

    #[test]
    fn wire_xpassed_strict() {
        let wire = deser(r#"{"node_id":"t","outcome":"xpassed","duration_ms":0.0,"strict":true}"#);
        let outcome = wire.into_outcome().outcome;
        match outcome {
            TestOutcome::XPassed { strict } => assert!(strict),
            other => panic!("expected XPassed, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod regression_955_tests {
    use super::*;

    #[test]
    fn skipped_reason_from_message_field() {
        // Regression test for #955: Python to_wire() emits reason in "message",
        // not "failure_repr". The old flat struct read failure_repr, losing the reason.
        let json =
            r#"{"node_id":"t","outcome":"skipped","duration_ms":0.0,"message":"needs network"}"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Skipped { reason } => assert_eq!(reason, "needs network"),
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn xfailed_reason_from_message_field() {
        let json =
            r#"{"node_id":"t","outcome":"xfailed","duration_ms":0.0,"message":"known bug #42"}"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::XFailed { reason } => assert_eq!(reason, "known bug #42"),
            other => panic!("expected XFailed, got {other:?}"),
        }
    }

    #[test]
    fn timeout_reason_from_message_field() {
        let json = r#"{"node_id":"t","outcome":"timeout","duration_ms":5000.0,"message":"Timed out after 5s"}"#;
        let r: WireResult = serde_json::from_str(json).unwrap();
        let outcome = r.into_outcome().outcome;
        match outcome {
            types::TestOutcome::Timeout { message } => assert_eq!(message, "Timed out after 5s"),
            other => panic!("expected Timeout, got {other:?}"),
        }
    }
}
