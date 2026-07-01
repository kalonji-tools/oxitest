use super::*;
use crate::types::{self, Frame, LineNo, TestOutcome};
use camino::Utf8PathBuf;

mod frame_tests {
    use super::*;

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

mod wire_conversion_tests {
    use super::*;

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

mod raw_outcome_tests {
    use super::*;
    use crate::types::ComparisonDetail;

    #[test]
    fn passed_with_tips() {
        let outcome = RawOutcome::Passed {
            no_message_lines: vec![3, 7],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Passed { tips } => {
                assert_eq!(
                    tips.as_deref(),
                    Some([3usize, 7].as_slice()),
                    "Passed tips should carry through from no_message_lines"
                );
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn passed_empty_no_message_lines_gives_none_tips() {
        let outcome = RawOutcome::Passed {
            no_message_lines: vec![],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Passed { tips } => {
                assert!(
                    tips.is_none(),
                    "empty no_message_lines should produce None tips"
                );
            }
            other => panic!("expected Passed, got {other:?}"),
        }
    }

    #[test]
    fn failed_carries_diagnostic_and_comparison() {
        let outcome = RawOutcome::Failed {
            message: "assert 1 == 2".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(42),
            source_line: "assert x == 2".to_string(),
            frames: vec![],
            comparison: ComparisonDetail {
                left: "1".to_string(),
                right: "2".to_string(),
                op: "==".to_string(),
                field_diffs: vec![],
            },
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.message, "assert 1 == 2", "message should pass through");
                assert_eq!(d.file, "test.py", "file should pass through");
                assert_eq!(d.lineno, LineNo::new(42), "lineno should pass through");
                assert_eq!(
                    d.source_line, "assert x == 2",
                    "source_line should pass through"
                );
                let cmp = d
                    .comparison
                    .as_ref()
                    .expect("Failed should carry comparison");
                assert_eq!(cmp.left, "1", "comparison left should pass through");
                assert_eq!(cmp.right, "2", "comparison right should pass through");
                assert_eq!(cmp.op, "==", "comparison op should pass through");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn failed_with_frames() {
        let outcome = RawOutcome::Failed {
            message: "oops".to_string(),
            file: Utf8PathBuf::from("t.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            frames: vec![Frame {
                file: Utf8PathBuf::from("t.py"),
                lineno: LineNo::new(10),
                name: "test_fn".to_string(),
                line: "do_thing()".to_string(),
                locals: vec![],
            }],
            comparison: ComparisonDetail {
                left: String::new(),
                right: String::new(),
                op: String::new(),
                field_diffs: vec![],
            },
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.frames.len(), 1, "single frame should be preserved");
                assert_eq!(
                    d.frames[0].name, "test_fn",
                    "frame name should pass through"
                );
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn failed_empty_frames() {
        let outcome = RawOutcome::Failed {
            message: "msg".to_string(),
            file: Utf8PathBuf::from("t.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            frames: vec![],
            comparison: ComparisonDetail {
                left: String::new(),
                right: String::new(),
                op: String::new(),
                field_diffs: vec![],
            },
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Failed(d) => {
                assert!(d.frames.is_empty(), "empty frames should remain empty");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn failed_empty_comparison_fields() {
        let outcome = RawOutcome::Failed {
            message: "msg".to_string(),
            file: Utf8PathBuf::from("t.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            frames: vec![],
            comparison: ComparisonDetail {
                left: String::new(),
                right: String::new(),
                op: String::new(),
                field_diffs: vec![],
            },
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Failed(d) => {
                let cmp = d
                    .comparison
                    .as_ref()
                    .expect("comparison should be Some even with empty fields");
                assert!(cmp.left.is_empty(), "empty left should remain empty");
                assert!(cmp.right.is_empty(), "empty right should remain empty");
                assert!(cmp.op.is_empty(), "empty op should remain empty");
                assert!(
                    cmp.field_diffs.is_empty(),
                    "empty field_diffs should remain empty"
                );
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn error_carries_diagnostic_without_comparison() {
        let outcome = RawOutcome::Error {
            message: "RuntimeError: boom".to_string(),
            file: Utf8PathBuf::from("mod.py"),
            lineno: LineNo::new(7),
            source_line: "raise RuntimeError".to_string(),
            frames: vec![],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Error(d) => {
                assert_eq!(
                    d.message, "RuntimeError: boom",
                    "message should pass through"
                );
                assert_eq!(d.file, "mod.py", "file should pass through");
                assert_eq!(d.lineno, LineNo::new(7), "lineno should pass through");
                assert!(
                    d.comparison.is_none(),
                    "Error should have no comparison detail"
                );
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn error_with_frames() {
        let outcome = RawOutcome::Error {
            message: "ImportError".to_string(),
            file: Utf8PathBuf::from("conftest.py"),
            lineno: LineNo::new(3),
            source_line: "import missing".to_string(),
            frames: vec![
                Frame {
                    file: Utf8PathBuf::from("conftest.py"),
                    lineno: LineNo::new(3),
                    name: "<module>".to_string(),
                    line: "import missing".to_string(),
                    locals: vec![],
                },
                Frame {
                    file: Utf8PathBuf::from("conftest.py"),
                    lineno: LineNo::new(1),
                    name: "setup".to_string(),
                    line: "from missing import x".to_string(),
                    locals: vec![],
                },
            ],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Error(d) => {
                assert_eq!(d.frames.len(), 2, "two frames should be preserved");
                assert_eq!(
                    d.frames[0].name, "<module>",
                    "first frame name should match"
                );
                assert_eq!(d.frames[1].name, "setup", "second frame name should match");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn error_empty_frames() {
        let outcome = RawOutcome::Error {
            message: "msg".to_string(),
            file: Utf8PathBuf::from("t.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            frames: vec![],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Error(d) => {
                assert!(d.frames.is_empty(), "empty frames should remain empty");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn skipped_carries_reason() {
        let outcome = RawOutcome::Skipped {
            reason: "needs network".to_string(),
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Skipped { reason } => {
                assert_eq!(reason, "needs network", "reason should pass through");
            }
            other => panic!("expected Skipped, got {other:?}"),
        }
    }

    #[test]
    fn warned_with_tips() {
        let outcome = RawOutcome::Warned {
            reason: "DeprecationWarning".to_string(),
            no_message_lines: vec![5],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Warned { reason, tips } => {
                assert_eq!(reason, "DeprecationWarning", "reason should pass through");
                assert_eq!(
                    tips.as_deref(),
                    Some([5usize].as_slice()),
                    "tips should carry through from no_message_lines"
                );
            }
            other => panic!("expected Warned, got {other:?}"),
        }
    }

    #[test]
    fn warned_empty_no_message_lines_gives_none_tips() {
        let outcome = RawOutcome::Warned {
            reason: "warning".to_string(),
            no_message_lines: vec![],
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Warned { tips, .. } => {
                assert!(
                    tips.is_none(),
                    "empty no_message_lines should produce None tips"
                );
            }
            other => panic!("expected Warned, got {other:?}"),
        }
    }

    #[test]
    fn xfailed_carries_reason() {
        let outcome = RawOutcome::XFailed {
            reason: "known bug #42".to_string(),
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::XFailed { reason } => {
                assert_eq!(reason, "known bug #42", "reason should pass through");
            }
            other => panic!("expected XFailed, got {other:?}"),
        }
    }

    #[test]
    fn xpassed_strict() {
        let outcome = RawOutcome::XPassed { strict: true }.into_test_outcome();
        match outcome {
            TestOutcome::XPassed { strict } => {
                assert!(strict, "strict flag should be true");
            }
            other => panic!("expected XPassed, got {other:?}"),
        }
    }

    #[test]
    fn xpassed_lenient() {
        let outcome = RawOutcome::XPassed { strict: false }.into_test_outcome();
        match outcome {
            TestOutcome::XPassed { strict } => {
                assert!(!strict, "strict flag should be false");
            }
            other => panic!("expected XPassed, got {other:?}"),
        }
    }

    #[test]
    fn timeout_carries_message() {
        let outcome = RawOutcome::Timeout {
            message: "Test timed out after 5s".to_string(),
        }
        .into_test_outcome();
        match outcome {
            TestOutcome::Timeout { message } => {
                assert_eq!(
                    message, "Test timed out after 5s",
                    "message should pass through"
                );
            }
            other => panic!("expected Timeout, got {other:?}"),
        }
    }
}
