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

mod orchestration_tests {
    use super::*;
    use crate::test_doubles::doubles::MockPhase;

    #[test]
    fn skipped_phases_are_not_called() {
        Python::initialize();
        Python::attach(|py| {
            let skipped = MockPhase::new("skipped", false, PhaseOutcome::Continue);
            let active = MockPhase::new("active", true, PhaseOutcome::Continue);
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&skipped, &active];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(0));
            assert!(!skipped.was_called());
            assert!(active.was_called());
        });
    }

    #[test]
    fn early_exit_stops_pipeline() {
        Python::initialize();
        Python::attach(|py| {
            let first = MockPhase::new("first", true, PhaseOutcome::Continue);
            let exiter = MockPhase::new("exiter", true, PhaseOutcome::EarlyExit(3));
            let after = MockPhase::new("after", true, PhaseOutcome::Continue);
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&first, &exiter, &after];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(3));
            assert!(first.was_called());
            assert!(exiter.was_called());
            assert!(!after.was_called());
        });
    }

    #[test]
    fn early_exit_zero_is_success() {
        Python::initialize();
        Python::attach(|py| {
            let exiter = MockPhase::new("exiter", true, PhaseOutcome::EarlyExit(0));
            let after = MockPhase::new("after", true, PhaseOutcome::Continue);
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&exiter, &after];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(0));
            assert!(exiter.was_called());
            assert!(!after.was_called());
        });
    }

    #[test]
    fn all_phases_skipped_returns_zero() {
        Python::initialize();
        Python::attach(|py| {
            let first = MockPhase::new("first", false, PhaseOutcome::Continue);
            let second = MockPhase::new("second", false, PhaseOutcome::EarlyExit(1));
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&first, &second];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(0));
            assert!(!first.was_called());
            assert!(!second.was_called());
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

mod strict_phase_contract_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_item_raw;

    #[test]
    fn strict_enforce_partitions_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            ctx.items.push(make_item_raw("tests/test_a.py::test_good"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_bad"));
            ctx.raw_violations.push(RawViolation {
                node_id: "tests/test_a.py::test_bad".to_string(),
                kind: ViolationKind::BareAssert,
                detail: "line 5".to_string(),
            });

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            assert_eq!(ctx.items.len(), 1);
            assert_eq!(ctx.items[0].node_id.as_ref(), "tests/test_a.py::test_good");
            assert_eq!(ctx.violated_items.len(), 1);
            assert_eq!(
                ctx.violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad"
            );
        });
    }

    #[test]
    fn strict_abort_with_violations_exits_3() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Abort);
            ctx.items.push(make_item_raw("tests/test_a.py::test_one"));
            ctx.raw_violations.push(RawViolation {
                node_id: "tests/test_a.py::test_one".to_string(),
                kind: ViolationKind::BareAssert,
                detail: "line 3".to_string(),
            });

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::EarlyExit(3)));
        });
    }

    #[test]
    fn strict_enforce_no_violations_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            ctx.items.push(make_item_raw("tests/test_a.py::test_clean"));

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            assert_eq!(ctx.items.len(), 1);
            assert!(ctx.violated_items.is_empty());
        });
    }
}

mod filter_phase_contract_tests {
    use super::*;
    use crate::reporter::test_helpers::make_item_raw;

    #[test]
    fn keyword_filter_reduces_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.items.push(make_item_raw("tests/test_a.py::test_alpha"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_beta"));
            ctx.cli.keyword = Some("alpha".to_string());

            let result = phases::FilterPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            assert_eq!(ctx.items.len(), 1);
            assert!(ctx.items[0].node_id.as_ref().contains("alpha"));
        });
    }

    #[test]
    fn no_filters_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.items.push(make_item_raw("tests/test_a.py::test_one"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_two"));

            let result = phases::FilterPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            assert_eq!(ctx.items.len(), 2);
        });
    }
}

mod list_phase_contract_tests {
    use super::*;
    use crate::reporter::test_helpers::make_item_raw;

    #[test]
    fn list_phase_returns_early_exit_zero() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.items.push(make_item_raw("tests/test_a.py::test_one"));

            let result = phases::ListPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::EarlyExit(0)));
        });
    }

    #[test]
    fn list_phase_empty_items_still_exits_zero() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();

            let result = phases::ListPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::EarlyExit(0)));
        });
    }
}

mod context_threading_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_item_raw;

    #[test]
    fn strict_then_filter_threads_clean_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            ctx.items.push(make_item_raw("tests/test_a.py::test_bad"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_alpha"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_beta"));
            ctx.raw_violations.push(RawViolation {
                node_id: "tests/test_a.py::test_bad".to_string(),
                kind: ViolationKind::BareAssert,
                detail: "line 5".to_string(),
            });

            let strict_result = phases::StrictPhase.execute(py, &mut ctx);
            assert!(matches!(strict_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(ctx.items.len(), 2);
            assert_eq!(ctx.violated_items.len(), 1);

            ctx.cli.keyword = Some("alpha".to_string());
            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(ctx.items.len(), 1);
            assert!(ctx.items[0].node_id.as_ref().contains("alpha"));
            assert_eq!(ctx.violated_items.len(), 1);
        });
    }

    #[test]
    fn strict_skipped_preserves_all_items_for_filter() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = None;
            ctx.items.push(make_item_raw("tests/test_a.py::test_one"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_two"));

            assert!(!phases::StrictPhase.should_run(&ctx));

            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(ctx.items.len(), 2);
        });
    }

    #[test]
    fn filter_then_list_threads_filtered_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.items.push(make_item_raw("tests/test_a.py::test_alpha"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_beta"));
            ctx.cli.keyword = Some("alpha".to_string());

            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(ctx.items.len(), 1);

            ctx.cli.list = true;
            let list_result = phases::ListPhase.execute(py, &mut ctx);
            assert!(matches!(list_result, Ok(PhaseOutcome::EarlyExit(0))));
            assert_eq!(ctx.items.len(), 1);
        });
    }

    #[test]
    fn full_pure_rust_chain_strict_filter_list() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            ctx.cli.keyword = Some("good".to_string());
            ctx.cli.list = true;
            ctx.items.push(make_item_raw("tests/test_a.py::test_good"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_bad"));
            ctx.items.push(make_item_raw("tests/test_a.py::test_other"));
            ctx.raw_violations.push(RawViolation {
                node_id: "tests/test_a.py::test_bad".to_string(),
                kind: ViolationKind::BareAssert,
                detail: "line 3".to_string(),
            });

            let pipeline: &[&dyn PipelinePhase] = &[
                &phases::StrictPhase,
                &phases::FilterPhase,
                &phases::ListPhase,
            ];
            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(0));
            assert_eq!(ctx.items.len(), 1);
            assert_eq!(ctx.items[0].node_id.as_ref(), "tests/test_a.py::test_good");
            assert_eq!(ctx.violated_items.len(), 1);
            assert_eq!(
                ctx.violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad"
            );
        });
    }
}
