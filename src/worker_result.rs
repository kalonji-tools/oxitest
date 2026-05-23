//! Deserialization of JSON results from worker subprocesses.
//!
//! Each worker writes one JSON line per test to stdout. This module defines
//! [`WorkerResult`] and converts it to [`TestOutcome`](types::TestOutcome).
//! Also provides sentinel builders for worker crashes and timeouts.
//!
//! The send-side schema — the JSON task sent *to* worker subprocesses — is
//! defined here as [`WorkerTask`] / [`WorkerTaskItem`] so the protocol is
//! type-checked at compile time rather than constructed with ad-hoc macros.

use crate::types::{self, Frame};

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
}

/// One test item within a [`WorkerTask`].
#[derive(serde::Serialize)]
pub(crate) struct WorkerTaskItem<'a> {
    pub fn_name: &'a str,
    pub param_id: Option<&'a str>,
}

#[derive(Debug, serde::Deserialize)]
pub(crate) struct FrameEntry {
    pub file: String,
    pub lineno: u64,
    pub name: String,
    pub line: String,
}

#[derive(Debug, serde::Deserialize)]
pub(crate) struct WorkerResult {
    pub node_id: String,
    pub outcome: types::OutcomeKind,
    pub duration_ms: f64,
    #[serde(default)]
    pub failure_repr: Option<String>,
    // Structured diagnostic fields from worker JSON
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub file: Option<String>,
    #[serde(default)]
    pub lineno: Option<u64>, // u64 because JSON integers are u64; convert to usize in to_outcome()
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
    pub frames: Vec<FrameEntry>,
}

impl WorkerResult {
    pub fn to_outcome(&self) -> types::TestOutcome {
        // skipped/xfailed/timeout carry their human-readable reason in
        // failure_repr.  For failed/error/warned, use message — failure_repr is a
        // legacy fallback that predates structured diagnostic fields; using it as
        // a message masks the left/right/op display in the reporter.
        // Warned uses message because failure_repr returns None for non-failure
        // statuses (warned is in _NON_FAILURE_STATUSES on the Python side).
        let message: String = match self.outcome {
            types::OutcomeKind::Skipped
            | types::OutcomeKind::XFailed
            | types::OutcomeKind::Timeout => {
                self.failure_repr.as_deref().unwrap_or_default().to_owned()
            }
            _ => self.message.as_deref().unwrap_or_default().to_owned(),
        };

        if self.outcome == types::OutcomeKind::Unknown {
            tracing::warn!(
                outcome = %self.outcome,
                "Unknown outcome string from worker — treating as error"
            );
        }

        // normalise no_message_lines into a Vec first so we can borrow it
        let no_message_lines: Vec<usize> = self
            .no_message_lines
            .iter()
            .filter(|&&n| n > 0)
            .map(|&n| usize::try_from(n).unwrap_or(0))
            .collect();

        let frames: Vec<Frame> = self
            .frames
            .iter()
            .map(|f| Frame {
                file: f.file.clone(),
                lineno: usize::try_from(f.lineno).unwrap_or(0),
                name: f.name.clone(),
                line: f.line.clone(),
            })
            .collect();

        types::TestOutcome::from_raw(types::RawOutcome {
            status: self.outcome.as_str(),
            message: &message,
            file: self.file.as_deref().unwrap_or_default(),
            lineno: self.lineno.map_or(0, |n| usize::try_from(n).unwrap_or(0)),
            source_line: self.source_line.as_deref().unwrap_or_default(),
            no_message_lines: &no_message_lines,
            left: self.left.as_deref().unwrap_or_default(),
            right: self.right.as_deref().unwrap_or_default(),
            op: self.op.as_deref().unwrap_or_default(),
            strict: self.strict,
            frames: &frames,
        })
    }
}

impl WorkerResult {
    /// Synthesise an error result for a test that could not be executed.
    pub(crate) fn error_sentinel(node_id: String, message: String, duration_ms: f64) -> Self {
        WorkerResult {
            node_id,
            outcome: types::OutcomeKind::Error,
            duration_ms,
            failure_repr: Some(message.clone()),
            message: Some(message),
            file: None,
            lineno: None,
            source_line: None,
            no_message_lines: vec![],
            left: None,
            right: None,
            op: None,
            strict: false,
            frames: vec![],
        }
    }

    /// Synthesise an error result for a test whose subprocess never responded.
    pub(crate) fn timed_out(node_id: String, watchdog: std::time::Duration) -> Self {
        Self::error_sentinel(
            node_id,
            format!(
                "Worker subprocess unresponsive after {}s",
                watchdog.as_secs()
            ),
            watchdog.as_millis() as f64,
        )
    }

    /// Synthesise an error result for a test whose subprocess exited unexpectedly.
    pub(crate) fn crashed(node_id: String) -> Self {
        Self::error_sentinel(
            node_id,
            "Worker subprocess exited unexpectedly".to_string(),
            0.0,
        )
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
        let r: WorkerResult = serde_json::from_str(json).unwrap();
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
        let r: WorkerResult = serde_json::from_str(json).unwrap();
        match r.to_outcome() {
            types::TestOutcome::Failed { frames, .. } => {
                assert_eq!(frames.len(), 2);
                assert_eq!(
                    frames[0],
                    Frame {
                        file: "t.py".to_string(),
                        lineno: 10,
                        name: "test_f".to_string(),
                        line: "do_thing()".to_string(),
                    }
                );
                assert_eq!(
                    frames[1],
                    Frame {
                        file: "t.py".to_string(),
                        lineno: 3,
                        name: "do_thing".to_string(),
                        line: "raise ValueError".to_string(),
                    }
                );
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn missing_frames_defaults_to_empty() {
        let json = r#"{"node_id":"t","outcome":"failed","duration_ms":0.0}"#;
        let r: WorkerResult = serde_json::from_str(json).unwrap();
        assert!(r.frames.is_empty());
        match r.to_outcome() {
            types::TestOutcome::Failed { frames, .. } => assert!(frames.is_empty()),
            other => panic!("expected Failed, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod lineno_cast_tests {
    use super::*;

    fn passed_result_with_lineno(lineno: Option<u64>) -> WorkerResult {
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
        // to_outcome() must not panic
        let _ = result.to_outcome();
    }

    #[test]
    fn lineno_small_value_passes_through() {
        let r: WorkerResult = serde_json::from_str(
            r#"{"node_id":"t","outcome":"failed","duration_ms":0.0,
                "lineno":42,"file":"t.py","source_line":"assert x"}"#,
        )
        .unwrap();
        match r.to_outcome() {
            types::TestOutcome::Failed { lineno, .. } => {
                assert_eq!(lineno, 42usize);
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn lineno_u32_max_does_not_panic() {
        let result = passed_result_with_lineno(Some(u32::MAX as u64));
        let _ = result.to_outcome();
    }

    #[test]
    fn lineno_u64_max_does_not_panic() {
        let result = passed_result_with_lineno(Some(u64::MAX));
        let _ = result.to_outcome();
    }
}
