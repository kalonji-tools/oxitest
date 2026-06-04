use super::*;
use crate::reporter::test_helpers::make_ctx;
use crate::test_doubles::doubles::{MockPhase, RecordingSession, StubCollector, StubRunner};
use crate::types::{ExitCode, TestItem};

/// Helper: set the collection state to `Collected` with given items and raw violations.
fn set_collected(
    ctx: &mut PipelineContext,
    items: Vec<std::sync::Arc<types::TestItem>>,
    raw_violations: Vec<bridge::RawViolation>,
) {
    ctx.collection = CollectionState::Collected {
        items,
        raw_violations,
    };
}

/// Helper: extract items from collection state (works for Collected, Partitioned, Ready, Executed).
fn get_items(ctx: &PipelineContext) -> &[std::sync::Arc<types::TestItem>] {
    match &ctx.collection {
        CollectionState::Collected { items, .. } => items,
        CollectionState::Partitioned { clean_items, .. } => clean_items,
        CollectionState::Ready { clean_items, .. } => clean_items,
        CollectionState::Executed { items } => items,
        CollectionState::Empty => &[],
    }
}

/// Helper: extract violated items from collection state.
fn get_violated_items(ctx: &PipelineContext) -> &[std::sync::Arc<types::TestItem>] {
    match &ctx.collection {
        CollectionState::Partitioned { violated_items, .. }
        | CollectionState::Ready { violated_items, .. } => violated_items,
        _ => &[],
    }
}

/// Helper: set the keyword in the command variant of a pipeline context.
fn set_keyword(ctx: &mut PipelineContext, keyword: &str) {
    match &mut ctx.command {
        config::Command::Run(a) => a.filter.keyword = Some(keyword.to_string()),
        config::Command::Debug(a) => a.filter.keyword = Some(keyword.to_string()),
        _ => {}
    }
}

mod loop_tests {
    use super::*;

    #[test]
    fn run_pipeline_returns_zero_when_no_phases() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[];
            let result = run_pipeline(py, pipeline, &mut ctx);
            assert_eq!(result, Ok(ExitCode::Success));
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
            let item = TestItem::builder_raw("tests/test_foo.py::test_one").build();
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
            let item = TestItem::builder_raw("tests/test_foo.py::test_unknown").build();
            let session = RecordingSession::new(py);

            let outcome = runner.run_test(py, &item, &session, None, None, None, false, false);

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

            assert_eq!(result, Ok(ExitCode::Success));
            assert!(!skipped.was_called());
            assert!(active.was_called());
        });
    }

    #[test]
    fn early_exit_stops_pipeline() {
        Python::initialize();
        Python::attach(|py| {
            let first = MockPhase::new("first", true, PhaseOutcome::Continue);
            let exiter = MockPhase::new(
                "exiter",
                true,
                PhaseOutcome::EarlyExit(ExitCode::CollectError),
            );
            let after = MockPhase::new("after", true, PhaseOutcome::Continue);
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&first, &exiter, &after];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(ExitCode::CollectError));
            assert!(first.was_called());
            assert!(exiter.was_called());
            assert!(!after.was_called());
        });
    }

    #[test]
    fn early_exit_zero_is_success() {
        Python::initialize();
        Python::attach(|py| {
            let exiter = MockPhase::new("exiter", true, PhaseOutcome::EarlyExit(ExitCode::Success));
            let after = MockPhase::new("after", true, PhaseOutcome::Continue);
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&exiter, &after];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(ExitCode::Success));
            assert!(exiter.was_called());
            assert!(!after.was_called());
        });
    }

    #[test]
    fn all_phases_skipped_returns_zero() {
        Python::initialize();
        Python::attach(|py| {
            let first = MockPhase::new("first", false, PhaseOutcome::Continue);
            let second =
                MockPhase::new("second", false, PhaseOutcome::EarlyExit(ExitCode::Failure));
            let mut ctx = make_ctx();
            let pipeline: &[&dyn PipelinePhase] = &[&first, &second];

            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(ExitCode::Success));
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
        assert!(matches!(ctx.collection, CollectionState::Empty));
        assert!(ctx.test_files.is_empty());
        assert!(ctx.execution_results.is_none());
        assert!(ctx.session.is_none());
    }
}

mod strict_phase_contract_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::types::TestItem;

    #[test]
    fn strict_enforce_partitions_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_good").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                ],
                vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 5".to_string(),
                }],
            );

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert_eq!(items[0].node_id.as_ref(), "tests/test_a.py::test_good");
            let violated = get_violated_items(&ctx);
            assert_eq!(violated.len(), 1);
            assert_eq!(violated[0].node_id.as_ref(), "tests/test_a.py::test_bad");
        });
    }

    #[test]
    fn strict_abort_with_violations_exits_3() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Abort);
            set_collected(
                &mut ctx,
                vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                vec![RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
            );

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(
                result.unwrap(),
                PhaseOutcome::EarlyExit(ExitCode::CollectError)
            ));
        });
    }

    #[test]
    fn strict_enforce_no_violations_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            set_collected(
                &mut ctx,
                vec![TestItem::builder_raw("tests/test_a.py::test_clean").arc()],
                vec![],
            );

            let result = phases::StrictPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert!(get_violated_items(&ctx).is_empty());
        });
    }
}

mod filter_phase_contract_tests {
    use super::*;
    use crate::types::TestItem;

    #[test]
    fn keyword_filter_reduces_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                vec![],
            );
            set_keyword(&mut ctx, "alpha");

            let result = phases::FilterPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert!(items[0].node_id.as_ref().contains("alpha"));
        });
    }

    #[test]
    fn no_filters_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                vec![],
            );

            let result = phases::FilterPhase.execute(py, &mut ctx);

            assert!(result.is_ok());
            assert!(matches!(result.unwrap(), PhaseOutcome::Continue));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 2);
        });
    }
}

mod context_threading_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::types::TestItem;

    #[test]
    fn strict_then_filter_threads_clean_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 5".to_string(),
                }],
            );

            let strict_result = phases::StrictPhase.execute(py, &mut ctx);
            assert!(matches!(strict_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(get_items(&ctx).len(), 2);
            assert_eq!(get_violated_items(&ctx).len(), 1);

            set_keyword(&mut ctx, "alpha");
            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert!(items[0].node_id.as_ref().contains("alpha"));
            assert_eq!(get_violated_items(&ctx).len(), 1);
        });
    }

    #[test]
    fn strict_skipped_preserves_all_items_for_filter() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = None;
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                vec![],
            );

            assert!(!phases::StrictPhase.should_run(&ctx));

            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            assert_eq!(get_items(&ctx).len(), 2);
        });
    }

    #[test]
    fn filter_preserves_items_after_filtering() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                vec![],
            );
            set_keyword(&mut ctx, "alpha");

            let filter_result = phases::FilterPhase.execute(py, &mut ctx);
            assert!(matches!(filter_result, Ok(PhaseOutcome::Continue)));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert!(items[0].node_id.as_ref().contains("alpha"));
        });
    }

    #[test]
    fn full_pure_rust_chain_strict_filter() {
        Python::initialize();
        Python::attach(|py| {
            let mut ctx = make_ctx();
            ctx.cfg.strict = Some(StrictMode::Enforce);
            set_keyword(&mut ctx, "good");
            set_collected(
                &mut ctx,
                vec![
                    TestItem::builder_raw("tests/test_a.py::test_good").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_other").arc(),
                ],
                vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
            );

            let pipeline: &[&dyn PipelinePhase] = &[&phases::StrictPhase, &phases::FilterPhase];
            let result = run_pipeline(py, pipeline, &mut ctx);

            assert_eq!(result, Ok(ExitCode::Success));
            let items = get_items(&ctx);
            assert_eq!(items.len(), 1);
            assert_eq!(items[0].node_id.as_ref(), "tests/test_a.py::test_good");
            let violated = get_violated_items(&ctx);
            assert_eq!(violated.len(), 1);
            assert_eq!(violated[0].node_id.as_ref(), "tests/test_a.py::test_bad");
        });
    }
}
