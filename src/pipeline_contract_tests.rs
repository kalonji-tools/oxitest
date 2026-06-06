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
            let mut p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_good").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad").arc(),
                ],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_bad".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 5".to_string(),
                }],
                collection_profile: None,
            });
            p.cfg.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert_eq!(
                p.state.clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_good"
            );
            assert_eq!(p.state.violated_items.len(), 1);
            assert_eq!(
                p.state.violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad"
            );
        });
    }

    #[test]
    fn strict_abort_with_violations_exits() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                items: vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
                collection_profile: None,
            });
            p.cfg.strict = Some(StrictMode::Abort);

            let result = p.strict_or_skip(py);
            assert!(result.is_err());
            assert_eq!(result.unwrap_err(), ExitCode::CollectError);
        });
    }

    #[test]
    fn strict_enforce_no_violations_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                items: vec![TestItem::builder_raw("tests/test_a.py::test_clean").arc()],
                raw_violations: vec![],
                collection_profile: None,
            });
            p.cfg.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert!(p.state.violated_items.is_empty());
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
            let mut p = make_pipeline(PreFilter {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                clean_items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_alpha").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_beta").arc(),
                ],
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            });
            match &mut p.command {
                crate::config::Command::Run(a) => {
                    a.filter.expression = Some("name(alpha)".to_string())
                }
                _ => {}
            }

            let result = p.filter(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert!(p.state.clean_items[0].node_id.as_ref().contains("alpha"));
        });
    }

    #[test]
    fn no_filters_passes_all_items() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PreFilter {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                clean_items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            });

            let result = p.filter(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 2);
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
            let mut p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
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
                collection_profile: None,
            });
            p.cfg.strict = Some(StrictMode::Enforce);

            let p = p.strict_or_skip(py).unwrap();
            assert_eq!(p.state.clean_items.len(), 2);
            assert_eq!(p.state.violated_items.len(), 1);

            let mut p = p;
            match &mut p.command {
                crate::config::Command::Run(a) => {
                    a.filter.expression = Some("name(alpha)".to_string())
                }
                _ => {}
            }
            let p = p.filter(py).unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert!(p.state.clean_items[0].node_id.as_ref().contains("alpha"));
            assert_eq!(p.state.violated_items.len(), 1);
        });
    }

    #[test]
    fn strict_skipped_preserves_all_items_for_filter() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                raw_violations: vec![],
                collection_profile: None,
            });
            // strict is None by default

            let p = p.strict_or_skip(py).unwrap();
            let p = p.filter(py).unwrap();
            assert_eq!(p.state.clean_items.len(), 2);
        });
    }

    #[test]
    fn full_pure_rust_chain_strict_filter() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                test_files: vec![],
                conftest_files: vec![],
                session: crate::bridge::FixtureSession::stub(py),
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
                collection_profile: None,
            });
            p.cfg.strict = Some(StrictMode::Enforce);
            match &mut p.command {
                crate::config::Command::Run(a) => {
                    a.filter.expression = Some("name(good)".to_string())
                }
                _ => {}
            }

            let p = p.strict_or_skip(py).unwrap();
            let p = p.filter(py).unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert_eq!(
                p.state.clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_good"
            );
            assert_eq!(p.state.violated_items.len(), 1);
            assert_eq!(
                p.state.violated_items[0].node_id.as_ref(),
                "tests/test_a.py::test_bad"
            );
        });
    }
}
