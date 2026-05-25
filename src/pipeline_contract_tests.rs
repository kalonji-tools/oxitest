use super::*;
use crate::reporter::test_helpers::make_ctx;
use crate::test_doubles::doubles::{
    make_test_item, MockPhase, RecordingSession, StubCollector, StubRunner,
};

mod loop_tests {
    use super::*;

    #[test]
    fn run_pipeline_returns_zero_when_no_phases() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[];
            let result = run_pipeline(py, pipeline, &mut ctx);
            assert_eq!(result, Ok(0));
        });
    }
}

mod double_tests {
    use super::*;
    use crate::pipeline::traits::{ModuleCollector, Session, TestRunner};
    use camino::Utf8PathBuf;
    use std::collections::HashMap;

    #[test]
    fn stub_collector_returns_configured_items() {
        Python::initialize();
        Python::attach(|py| {
            let path = Utf8PathBuf::from("tests/test_foo.py");
            let item = make_test_item("tests/test_foo.py::test_one");
            let mut results = HashMap::new();
            results.insert(path.clone(), Ok((vec![item], vec![])));
            let collector = StubCollector { results };

            let session = RecordingSession::new(py);
            let (items, violations) = collector
                .collect_module(py, &path, &session, false)
                .expect("collect should succeed");

            assert_eq!(items.len(), 1);
            assert_eq!(items[0].node_id.to_string(), "tests/test_foo.py::test_one");
            assert!(violations.is_empty());
        });
    }

    #[test]
    fn stub_runner_returns_passed_for_unknown_node() {
        Python::initialize();
        Python::attach(|py| {
            let runner = StubRunner::default();
            let item = make_test_item("tests/test_foo.py::test_unknown");
            let session = RecordingSession::new(py);

            let outcome = runner.run_test(py, &item, &session, None);

            assert!(
                matches!(outcome, crate::types::TestOutcome::Passed { .. }),
                "expected Passed, got {:?}",
                outcome.as_str()
            );
            let calls = runner.calls.borrow();
            assert_eq!(calls.len(), 1);
            assert_eq!(calls[0].0, "tests/test_foo.py::test_unknown");
            assert_eq!(calls[0].1, None);
        });
    }

    #[test]
    fn recording_session_tracks_end_module_calls() {
        Python::initialize();
        Python::attach(|py| {
            let session = RecordingSession::new(py);

            session
                .end_module(py, camino::Utf8Path::new("tests/test_a.py"))
                .unwrap();
            session
                .end_module(py, camino::Utf8Path::new("tests/test_b.py"))
                .unwrap();

            let calls = session.end_module_calls.borrow();
            assert_eq!(calls.len(), 2);
            assert_eq!(calls[0], Utf8PathBuf::from("tests/test_a.py"));
            assert_eq!(calls[1], Utf8PathBuf::from("tests/test_b.py"));
            assert!(!session.end_session_called.get());
        });
    }

    #[test]
    fn mock_phase_records_execution() {
        Python::initialize();
        Python::attach(|py| {
            let phase = MockPhase::new("test_phase", true, PhaseOutcome::Continue);
            assert!(!phase.was_called());

            let mut ctx = make_ctx();
            let outcome = phase.execute(py, &mut ctx).expect("execute should succeed");

            assert!(phase.was_called());
            assert!(matches!(outcome, PhaseOutcome::Continue));
        });
    }
}

mod helper_tests {
    use super::*;
    use crate::types::{DurationMs, OutcomeKind};

    #[test]
    fn make_timing_creates_timing_with_zero_duration() {
        let timing =
            reporter::test_helpers::make_timing("tests/test_a.py::test_one", OutcomeKind::Passed);
        assert_eq!(timing.node_id.as_ref(), "tests/test_a.py::test_one");
        assert_eq!(timing.duration_ms, DurationMs::ZERO);
        assert_eq!(timing.outcome, OutcomeKind::Passed);
    }

    #[test]
    fn make_outcome_passed() {
        let outcome = reporter::test_helpers::make_outcome("passed");
        assert!(matches!(outcome, types::TestOutcome::Passed { .. }));
    }

    #[test]
    fn make_outcome_failed() {
        let outcome = reporter::test_helpers::make_outcome("failed");
        assert!(matches!(outcome, types::TestOutcome::Failed { .. }));
    }

    #[test]
    fn make_ctx_creates_empty_context() {
        let ctx = reporter::test_helpers::make_ctx();
        assert!(ctx.items.is_empty());
        assert!(ctx.test_files.is_empty());
        assert!(!ctx.interrupted);
        assert!(ctx.session.is_none());
    }
}
