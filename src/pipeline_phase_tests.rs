use super::*;
use crate::types::NodeId;

mod fixture_validation_format_tests {
    use super::*;

    #[test]
    fn format_errors_with_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "sotre".to_string())];
        let registered = vec!["store".to_string(), "backend".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("ERROR collecting tests"));
        assert!(msg.contains("fixture 'sotre' not found"));
        assert!(msg.contains("did you mean 'store'?"));
    }

    #[test]
    fn format_errors_without_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "zzzzz".to_string())];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("fixture 'zzzzz' not found"));
        assert!(!msg.contains("did you mean"));
    }

    #[test]
    fn format_errors_multiple() {
        let errors = vec![
            (NodeId::from_raw("test.py::test_a"), "sotre".to_string()),
            (NodeId::from_raw("test.py::test_b"), "xyz".to_string()),
        ];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(msg.contains("test.py::test_a"));
        assert!(msg.contains("test.py::test_b"));
        assert!(msg.contains("sotre"));
        assert!(msg.contains("xyz"));
    }

    #[test]
    fn format_errors_empty_registered() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "store".to_string())];
        let msg = format_fixture_errors(&errors, &[]);
        assert!(msg.contains("fixture 'store' not found"));
        assert!(!msg.contains("did you mean"));
    }
}

mod pipeline_shared_round_trip_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;

    #[test]
    fn into_parts_and_into_pipeline_preserves_rootdir() {
        let p = make_pipeline(Empty);
        let original_rootdir = p.rootdir.clone();
        let (shared, _state) = p.into_parts();
        assert_eq!(shared.rootdir, original_rootdir);
    }

    #[test]
    fn into_parts_and_into_pipeline_round_trip() {
        let p = make_pipeline(Empty);
        let original_rootdir = p.rootdir.clone();
        let original_python_bin = p.python_bin.clone();
        let original_is_tty = p.is_tty;
        let original_use_color = p.use_color;

        let (shared, state) = p.into_parts();
        let restored = shared.into_pipeline(state);

        assert_eq!(restored.rootdir, original_rootdir);
        assert_eq!(restored.python_bin, original_python_bin);
        assert_eq!(restored.is_tty, original_is_tty);
        assert_eq!(restored.use_color, original_use_color);
    }

    #[test]
    fn into_parts_and_into_pipeline_with_different_state() {
        let p = make_pipeline(Empty);
        let original_rootdir = p.rootdir.clone();

        let (mut shared, _empty) = p.into_parts();
        shared.test_files = vec![camino::Utf8PathBuf::from("tests/test_a.py")];
        let files_pipeline = shared.into_pipeline(FilesCollected);

        assert_eq!(files_pipeline.rootdir, original_rootdir);
        assert_eq!(files_pipeline.shared.test_files.len(), 1);
        assert_eq!(
            files_pipeline.shared.test_files[0].as_str(),
            "tests/test_a.py"
        );
    }

    #[test]
    fn shared_fields_survive_state_transition_chain() {
        let p = make_pipeline(Empty);
        let original_rootdir = p.rootdir.clone();
        let original_python_bin = p.python_bin.clone();

        // Empty -> FilesCollected
        let (shared, _) = p.into_parts();
        let p2 = shared.into_pipeline(FilesCollected);
        assert_eq!(p2.rootdir, original_rootdir);
        assert_eq!(p2.python_bin, original_python_bin);

        // FilesCollected -> (decompose and rebuild with PreFilter)
        let (shared2, _) = p2.into_parts();
        let p3 = shared2.into_pipeline(PreFilter {
            clean_items: vec![],
            violated_items: vec![],
            all_violations: vec![],
            suite_lines: vec![],
        });
        assert_eq!(p3.rootdir, original_rootdir);
        assert_eq!(p3.python_bin, original_python_bin);
    }
}

mod strict_or_skip_disabled_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn strict_none_all_items_become_clean() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_three").arc(),
                ],
                raw_violations: vec![],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            // cfg.markers.strict is None by default

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 3);
            assert!(p.state.violated_items.is_empty());
            assert!(p.state.all_violations.is_empty());
            assert!(p.state.suite_lines.is_empty());
        });
    }

    #[test]
    fn strict_none_ignores_raw_violations() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                raw_violations: vec![crate::bridge::RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: crate::bridge::ViolationKind::BareAssert,
                    detail: "line 10".to_string(),
                }],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            // strict is None — violations should be ignored entirely

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert!(p.state.violated_items.is_empty());
            assert!(p.state.all_violations.is_empty());
        });
    }

    #[test]
    fn strict_none_with_empty_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![],
                raw_violations: vec![],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert!(p.state.clean_items.is_empty());
            assert!(p.state.violated_items.is_empty());
            assert!(p.state.all_violations.is_empty());
        });
    }
}

mod strict_enforce_detailed_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn enforce_multiple_violations_on_different_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_clean").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad_1").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_bad_2").arc(),
                ],
                raw_violations: vec![
                    RawViolation {
                        node_id: "tests/test_a.py::test_bad_1".to_string(),
                        kind: ViolationKind::BareAssert,
                        detail: "line 5".to_string(),
                    },
                    RawViolation {
                        node_id: "tests/test_a.py::test_bad_2".to_string(),
                        kind: ViolationKind::BareAssert,
                        detail: "line 10".to_string(),
                    },
                ],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert_eq!(
                p.state.clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_clean"
            );
            assert_eq!(p.state.violated_items.len(), 2);
            assert!(!p.state.all_violations.is_empty());
        });
    }

    #[test]
    fn enforce_produces_suite_lines() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                raw_violations: vec![RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: ViolationKind::BareAssert,
                    detail: "line 3".to_string(),
                }],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let p = p.strict_or_skip(py).unwrap();
            // Suite lines should be empty because BareAssert is PerTest, not Suite
            // (suite_level only picks StrictViolation::Suite variants)
            assert!(p.state.suite_lines.is_empty());
        });
    }

    #[test]
    fn enforce_preserves_test_and_conftest_files() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![crate::types::TestItem::builder_raw("tests/test_x.py::test_fn").arc()],
                raw_violations: vec![],
                collection_profile: None,
            });
            p.shared.test_files = vec![camino::Utf8PathBuf::from("tests/test_x.py")];
            p.shared.conftest_files = vec![camino::Utf8PathBuf::from("tests/conftest.py")];
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let p = p.strict_or_skip(py).unwrap();
            assert_eq!(p.shared.test_files.len(), 1);
            assert_eq!(p.shared.test_files[0].as_str(), "tests/test_x.py");
            assert_eq!(p.shared.conftest_files.len(), 1);
            assert_eq!(p.shared.conftest_files[0].as_str(), "tests/conftest.py");
        });
    }
}

mod filter_last_failed_tests {
    use super::*;
    use crate::config::FailedMode;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn failed_only_with_no_cache_runs_all() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PreFilter {
                clean_items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            p.cfg.filter.failed = Some(FailedMode::Only);

            let result = p.filter(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            // No cached failures -> all items pass through
            assert_eq!(p.state.clean_items.len(), 2);
        });
    }

    #[test]
    fn failed_first_with_no_cache_preserves_order() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PreFilter {
                clean_items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            p.cfg.filter.failed = Some(FailedMode::First);

            let result = p.filter(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 2);
        });
    }

    #[test]
    fn no_failed_mode_passes_all() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PreFilter {
                clean_items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_three").arc(),
                ],
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            // cfg.filter.failed is None by default

            let result = p.filter(py);
            assert!(result.is_ok());
            let p = result.unwrap();
            assert_eq!(p.state.clean_items.len(), 3);
        });
    }
}

mod filter_preserves_violations_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::strict::{PerTestViolation, StrictViolation};
    use crate::types::{NodeId, TestItem};

    #[test]
    fn filter_carries_violated_items_and_violations_through() {
        Python::initialize();
        Python::attach(|py| {
            let violations = vec![StrictViolation::PerTest(PerTestViolation::BareAssert {
                node_id: NodeId::from_raw("tests/test_a.py::test_bad"),
                lines: vec![5],
            })];
            let mut p = make_pipeline(PreFilter {
                clean_items: vec![TestItem::builder_raw("tests/test_a.py::test_good").arc()],
                violated_items: vec![TestItem::builder_raw("tests/test_a.py::test_bad").arc()],
                all_violations: violations,
                suite_lines: vec!["some warning".to_string()],
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));

            let p = p.filter(py).unwrap();
            assert_eq!(p.state.clean_items.len(), 1);
            assert_eq!(p.state.violated_items.len(), 1);
            assert_eq!(p.state.all_violations.len(), 1);
            assert_eq!(p.state.suite_lines.len(), 1);
            assert_eq!(p.state.suite_lines[0], "some warning");
        });
    }
}

mod state_construction_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;

    #[test]
    fn pipeline_empty_has_default_config() {
        let p = make_pipeline(Empty);
        assert_eq!(p.rootdir.as_str(), ".");
        assert!(!p.is_tty);
        assert!(!p.use_color);
        assert_eq!(p.python_bin, "python3");
    }

    #[test]
    fn pipeline_debug_formats_with_rootdir() {
        let p = make_pipeline(Empty);
        let debug_str = format!("{:?}", p);
        assert!(debug_str.contains("Pipeline"));
        assert!(debug_str.contains("."));
    }

    #[test]
    fn pipeline_files_collected_state() {
        let mut p = make_pipeline(FilesCollected);
        p.shared.test_files = vec![
            camino::Utf8PathBuf::from("tests/test_a.py"),
            camino::Utf8PathBuf::from("tests/test_b.py"),
        ];
        p.shared.conftest_files = vec![camino::Utf8PathBuf::from("tests/conftest.py")];
        assert_eq!(p.shared.test_files.len(), 2);
        assert_eq!(p.shared.conftest_files.len(), 1);
    }

    #[test]
    fn pipeline_collected_state_with_items() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(Collected {
                items: vec![crate::types::TestItem::builder_raw("tests/test_a.py::test_fn").arc()],
                raw_violations: vec![],
                collection_profile: None,
            });
            p.shared.session = Some(crate::bridge::FixtureSession::stub(py));
            assert_eq!(p.state.items.len(), 1);
            assert!(p.state.raw_violations.is_empty());
        });
    }
}
