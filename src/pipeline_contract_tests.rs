use super::*;

mod strict_phase_contract_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn strict_enforce_partitions_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_good").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                ],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 5".to_string(),
                }],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed in enforce mode"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "one clean item expected after enforce partition"
            );
            assert_eq!(
                clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_good",
                "clean item should be test_good"
            );
            assert_eq!(
                violated_items.len(),
                1,
                "one violated item expected after enforce partition"
            );
            assert_eq!(
                violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad",
                "violated item should be test_bad"
            );
        });
    }

    #[test]
    fn strict_abort_with_violations_exits() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Abort);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_err(),
                "strict_or_skip should fail in abort mode with violations"
            );
            assert_eq!(
                result.unwrap_err(),
                ExitCode::CollectError,
                "abort mode should return CollectError exit code"
            );
        });
    }

    #[test]
    fn strict_enforce_no_violations_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![TestItem::builder_raw("tests/test_a.py::test_clean").arc()],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with no violations"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "all items should be clean when no violations"
            );
            assert!(
                violated_items.is_empty(),
                "no items should be violated when no violations"
            );
        });
    }
}

mod filter_phase_contract_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn expression_filter_reduces_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            if let crate::config::Command::Run(a) = &mut p.command {
                a.filter.expression = Some("name(alpha)".to_string())
            }

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with expression filter"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items, ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "expression filter should reduce to one item"
            );
            assert!(
                clean_items[0].node_id.as_ref().contains("alpha"),
                "filtered item should be the alpha test"
            );
        });
    }

    #[test]
    fn no_filters_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with no filters"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items, ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                2,
                "all items should pass through with no filters"
            );
        });
    }
}

mod context_threading_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn strict_then_filter_threads_clean_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 5".to_string(),
                }],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Enforce);
            if let crate::config::Command::Run(a) = &mut p.command {
                a.filter.expression = Some("name(alpha)".to_string())
            }

            // strict_or_skip now does strict-mode split AND filtering in one step
            let p = p.strict_or_skip(py).unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "only alpha should survive strict + expression filter"
            );
            assert!(
                clean_items[0].node_id.as_ref().contains("alpha"),
                "surviving item should be alpha"
            );
            assert_eq!(
                violated_items.len(),
                1,
                "test_bad should be in violated items"
            );
        });
    }

    #[test]
    fn strict_skipped_preserves_all_items_for_filter() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            // strict is None by default

            let p = p.strict_or_skip(py).unwrap();
            let PipelinePhase::Ready {
                ref clean_items, ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                2,
                "all items should pass through when strict is None"
            );
        });
    }

    #[test]
    fn full_pure_rust_chain_strict_filter() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_good").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_other").arc(),
                ],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Enforce);
            if let crate::config::Command::Run(a) = &mut p.command {
                a.filter.expression = Some("name(good)".to_string())
            }

            let p = p.strict_or_skip(py).unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(clean_items.len(), 1, "only test_good should survive");
            assert_eq!(
                clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_good",
                "surviving clean item should be test_good"
            );
            assert_eq!(violated_items.len(), 1, "test_bad should be in violated");
            assert_eq!(
                violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad",
                "violated item should be test_bad"
            );
        });
    }
}
