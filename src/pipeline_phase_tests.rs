use super::*;
use crate::types::NodeId;

mod fixture_validation_format_tests {
    use super::*;

    #[test]
    fn format_errors_with_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "sotre".to_string())];
        let registered = vec!["store".to_string(), "backend".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(
            msg.contains("ERROR collecting tests"),
            "expected error header in fixture validation output"
        );
        assert!(
            msg.contains("fixture 'sotre' not found"),
            "expected fixture-not-found message for typo"
        );
        assert!(
            msg.contains("did you mean 'store'?"),
            "expected edit-distance suggestion for close match"
        );
    }

    #[test]
    fn format_errors_without_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "zzzzz".to_string())];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(
            msg.contains("fixture 'zzzzz' not found"),
            "expected fixture-not-found message for distant name"
        );
        assert!(
            !msg.contains("did you mean"),
            "no suggestion expected when edit distance exceeds threshold"
        );
    }

    #[test]
    fn format_errors_multiple() {
        let errors = vec![
            (NodeId::from_raw("test.py::test_a"), "sotre".to_string()),
            (NodeId::from_raw("test.py::test_b"), "xyz".to_string()),
        ];
        let registered = vec!["store".to_string()];
        let msg = format_fixture_errors(&errors, &registered);
        assert!(
            msg.contains("test.py::test_a"),
            "expected first node ID in multi-error output"
        );
        assert!(
            msg.contains("test.py::test_b"),
            "expected second node ID in multi-error output"
        );
        assert!(
            msg.contains("sotre"),
            "expected first bad fixture name in output"
        );
        assert!(
            msg.contains("xyz"),
            "expected second bad fixture name in output"
        );
    }

    #[test]
    fn format_errors_empty_registered() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "store".to_string())];
        let msg = format_fixture_errors(&errors, &[]);
        assert!(
            msg.contains("fixture 'store' not found"),
            "expected fixture-not-found even with empty registry"
        );
        assert!(
            !msg.contains("did you mean"),
            "no suggestion expected with empty registry"
        );
    }
}

mod pipeline_shared_round_trip_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;

    #[test]
    fn into_parts_and_into_pipeline_preserves_rootdir() {
        let p = make_pipeline(PipelinePhase::Empty);
        let original_rootdir = p.rootdir.clone();
        let (shared, _phase) = p.into_parts();
        assert_eq!(
            shared.rootdir, original_rootdir,
            "rootdir must survive into_parts decomposition"
        );
    }

    #[test]
    fn into_parts_and_into_pipeline_round_trip() {
        let p = make_pipeline(PipelinePhase::Empty);
        let original_rootdir = p.rootdir.clone();
        let original_python_bin = p.python_bin.clone();
        let original_is_tty = p.is_tty;
        let original_use_color = p.use_color;

        let (shared, phase) = p.into_parts();
        let restored = Pipeline { shared, phase };

        assert_eq!(
            restored.rootdir, original_rootdir,
            "rootdir must survive round-trip"
        );
        assert_eq!(
            restored.python_bin, original_python_bin,
            "python_bin must survive round-trip"
        );
        assert_eq!(
            restored.is_tty, original_is_tty,
            "is_tty must survive round-trip"
        );
        assert_eq!(
            restored.use_color, original_use_color,
            "use_color must survive round-trip"
        );
    }

    #[test]
    fn into_parts_and_into_pipeline_with_different_state() {
        let p = make_pipeline(PipelinePhase::Empty);
        let original_rootdir = p.rootdir.clone();

        let (mut shared, _) = p.into_parts();
        shared.test_files = vec![camino::Utf8PathBuf::from("tests/test_a.py")];
        let files_pipeline = Pipeline {
            shared,
            phase: PipelinePhase::FilesCollected,
        };

        assert_eq!(
            files_pipeline.rootdir, original_rootdir,
            "rootdir must survive phase change"
        );
        assert_eq!(
            files_pipeline.shared.test_files.len(),
            1,
            "test_files must reflect the mutation"
        );
        assert_eq!(
            files_pipeline.shared.test_files[0].as_str(),
            "tests/test_a.py",
            "test_files path must match what was set"
        );
    }

    #[test]
    fn shared_fields_survive_state_transition_chain() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Empty);
            let original_rootdir = p.rootdir.clone();
            let original_python_bin = p.python_bin.clone();

            // Empty -> FilesCollected
            let (shared, _) = p.into_parts();
            let p2 = Pipeline {
                shared,
                phase: PipelinePhase::FilesCollected,
            };
            assert_eq!(
                p2.rootdir, original_rootdir,
                "rootdir must survive Empty->FilesCollected"
            );
            assert_eq!(
                p2.python_bin, original_python_bin,
                "python_bin must survive Empty->FilesCollected"
            );

            // FilesCollected -> Ready
            let (shared2, _) = p2.into_parts();
            let p3 = Pipeline {
                shared: shared2,
                phase: PipelinePhase::Ready {
                    session: crate::bridge::FixtureSession::stub(py),
                    clean_items: vec![],
                    violated_items: vec![],
                    all_violations: vec![],
                    suite_lines: vec![],
                },
            };
            assert_eq!(
                p3.rootdir, original_rootdir,
                "rootdir must survive FilesCollected->Ready"
            );
            assert_eq!(
                p3.python_bin, original_python_bin,
                "python_bin must survive FilesCollected->Ready"
            );
        });
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
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_three").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            // cfg.markers.strict is None by default

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed when strict is None"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ref all_violations,
                ref suite_lines,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                3,
                "all items should be clean when strict is None"
            );
            assert!(
                violated_items.is_empty(),
                "no items should be violated when strict is None"
            );
            assert!(
                all_violations.is_empty(),
                "no violations expected when strict is None"
            );
            assert!(
                suite_lines.is_empty(),
                "no suite lines expected when strict is None"
            );
        });
    }

    #[test]
    fn strict_none_ignores_raw_violations() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![TestItem::builder_raw("tests/test_a.py::test_one").arc()],
                raw_violations: vec![crate::bridge::RawViolation {
                    node_id: "tests/test_a.py::test_one".to_string(),
                    kind: crate::bridge::ViolationKind::BareAssert,
                    detail: "line 10".to_string(),
                }],
                session: crate::bridge::FixtureSession::stub(py),
            });
            // strict is None — violations should be ignored entirely

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed when strict is None"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ref all_violations,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "item should pass through as clean when strict is None"
            );
            assert!(
                violated_items.is_empty(),
                "no items should be violated when strict is None"
            );
            assert!(
                all_violations.is_empty(),
                "violations should be ignored when strict is None"
            );
        });
    }

    #[test]
    fn strict_none_with_empty_items() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with empty items"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ref all_violations,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert!(
                clean_items.is_empty(),
                "clean items should be empty when input is empty"
            );
            assert!(
                violated_items.is_empty(),
                "violated items should be empty when input is empty"
            );
            assert!(
                all_violations.is_empty(),
                "violations should be empty when input is empty"
            );
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
            let mut p = make_pipeline(PipelinePhase::Collected {
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
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed in enforce mode with violations"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ref all_violations,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(clean_items.len(), 1, "only the clean item should remain");
            assert_eq!(
                clean_items[0].node_id.as_ref(),
                "tests/test_a.py::test_clean",
                "the clean item should be test_clean"
            );
            assert_eq!(violated_items.len(), 2, "both bad items should be violated");
            assert!(
                !all_violations.is_empty(),
                "violations list should be non-empty"
            );
        });
    }

    #[test]
    fn enforce_produces_suite_lines() {
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
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let p = p.strict_or_skip(py).unwrap();
            let PipelinePhase::Ready {
                ref suite_lines, ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            // Suite lines should be empty because BareAssert is PerTest, not Suite
            // (suite_level only picks StrictViolation::Suite variants)
            assert!(
                suite_lines.is_empty(),
                "BareAssert is per-test, should not produce suite lines"
            );
        });
    }

    #[test]
    fn enforce_preserves_test_and_conftest_files() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![crate::types::TestItem::builder_raw("tests/test_x.py::test_fn").arc()],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.shared.test_files = vec![camino::Utf8PathBuf::from("tests/test_x.py")];
            p.shared.conftest_files = vec![camino::Utf8PathBuf::from("tests/conftest.py")];
            p.cfg.markers.strict = Some(StrictMode::Enforce);

            let p = p.strict_or_skip(py).unwrap();
            assert_eq!(
                p.shared.test_files.len(),
                1,
                "test_files must survive strict_or_skip"
            );
            assert_eq!(
                p.shared.test_files[0].as_str(),
                "tests/test_x.py",
                "test_files path must be preserved"
            );
            assert_eq!(
                p.shared.conftest_files.len(),
                1,
                "conftest_files must survive strict_or_skip"
            );
            assert_eq!(
                p.shared.conftest_files[0].as_str(),
                "tests/conftest.py",
                "conftest_files path must be preserved"
            );
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
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.filter.failed = Some(FailedMode::Only);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with --failed=only and no cache"
            );
            let p = result.unwrap();
            let PipelinePhase::Ready {
                ref clean_items, ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            // No cached failures -> all items pass through
            assert_eq!(
                clean_items.len(),
                2,
                "all items should pass through with no cached failures"
            );
        });
    }

    #[test]
    fn failed_first_with_no_cache_preserves_order() {
        Python::initialize();
        Python::attach(|py| {
            let mut p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            p.cfg.filter.failed = Some(FailedMode::First);

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with --failed=first and no cache"
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
                "all items should pass through with --failed=first and no cache"
            );
        });
    }

    #[test]
    fn no_failed_mode_passes_all() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![
                    TestItem::builder_raw("tests/test_a.py::test_one").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_two").arc(),
                    TestItem::builder_raw("tests/test_a.py::test_three").arc(),
                ],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            // cfg.filter.failed is None by default

            let result = p.strict_or_skip(py);
            assert!(
                result.is_ok(),
                "strict_or_skip should succeed with no failed mode"
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
                3,
                "all items should pass through with no failed mode"
            );
        });
    }
}

mod filter_preserves_violations_tests {
    use super::*;
    use crate::bridge::{RawViolation, ViolationKind};
    use crate::config::StrictMode;
    use crate::reporter::test_helpers::make_pipeline;
    use crate::types::TestItem;

    #[test]
    fn strict_then_filter_carries_violations_through() {
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

            let p = p.strict_or_skip(py).unwrap();
            let PipelinePhase::Ready {
                ref clean_items,
                ref violated_items,
                ref all_violations,
                ..
            } = p.phase
            else {
                panic!("expected Ready phase after strict_or_skip")
            };
            assert_eq!(
                clean_items.len(),
                1,
                "one clean item expected after strict enforce"
            );
            assert_eq!(
                violated_items.len(),
                1,
                "one violated item expected after strict enforce"
            );
            assert_eq!(
                all_violations.len(),
                1,
                "one violation expected after strict enforce"
            );
        });
    }
}

mod state_construction_tests {
    use super::*;
    use crate::reporter::test_helpers::make_pipeline;

    #[test]
    fn pipeline_empty_has_default_config() {
        let p = make_pipeline(PipelinePhase::Empty);
        assert_eq!(
            p.rootdir.as_str(),
            ".",
            "default rootdir should be current directory"
        );
        assert!(!p.is_tty, "test pipeline should not be a tty");
        assert!(!p.use_color, "test pipeline should not use color");
        assert_eq!(
            p.python_bin, "python3",
            "default python_bin should be python3"
        );
    }

    #[test]
    fn pipeline_debug_formats_with_rootdir() {
        let p = make_pipeline(PipelinePhase::Empty);
        let debug_str = format!("{p:?}");
        assert!(
            debug_str.contains("Pipeline"),
            "debug output should contain 'Pipeline'"
        );
        assert!(
            debug_str.contains("."),
            "debug output should contain rootdir"
        );
    }

    #[test]
    fn pipeline_files_collected_state() {
        let mut p = make_pipeline(PipelinePhase::FilesCollected);
        p.shared.test_files = vec![
            camino::Utf8PathBuf::from("tests/test_a.py"),
            camino::Utf8PathBuf::from("tests/test_b.py"),
        ];
        p.shared.conftest_files = vec![camino::Utf8PathBuf::from("tests/conftest.py")];
        assert_eq!(p.shared.test_files.len(), 2, "should have two test files");
        assert_eq!(
            p.shared.conftest_files.len(),
            1,
            "should have one conftest file"
        );
    }

    #[test]
    fn pipeline_collected_state_with_items() {
        Python::initialize();
        Python::attach(|py| {
            let p = make_pipeline(PipelinePhase::Collected {
                items: vec![crate::types::TestItem::builder_raw("tests/test_a.py::test_fn").arc()],
                raw_violations: vec![],
                session: crate::bridge::FixtureSession::stub(py),
            });
            let PipelinePhase::Collected {
                ref items,
                ref raw_violations,
                ..
            } = p.phase
            else {
                panic!("expected Collected phase")
            };
            assert_eq!(items.len(), 1, "should have one collected item");
            assert!(raw_violations.is_empty(), "should have no raw violations");
        });
    }
}
