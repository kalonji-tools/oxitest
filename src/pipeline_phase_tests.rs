use super::*;
use crate::reporter::test_helpers::make_ctx;

mod phase_outcome_tests {
    use super::*;

    #[test]
    fn continue_variant_exists() {
        let outcome = PhaseOutcome::Continue;
        assert!(matches!(outcome, PhaseOutcome::Continue));
    }

    #[test]
    fn early_exit_carries_code() {
        use crate::types::ExitCode;
        let outcome = PhaseOutcome::EarlyExit(ExitCode::CollectError);
        assert!(matches!(
            outcome,
            PhaseOutcome::EarlyExit(ExitCode::CollectError)
        ));
        if let PhaseOutcome::EarlyExit(code) = outcome {
            assert_eq!(code, ExitCode::CollectError);
        }
    }
}

mod pipeline_context_tests {
    use super::*;

    #[test]
    fn context_starts_with_empty_items() {
        let ctx = make_ctx();
        assert!(ctx.items.is_empty());
        assert!(ctx.raw_violations.is_empty());
        assert!(ctx.violated_items.is_empty());
        assert!(ctx.all_violations.is_empty());
        assert!(ctx.suite_lines.is_empty());
        assert!(ctx.timings.is_empty());
        assert!(ctx.test_files.is_empty());
        assert!(ctx.conftest_files.is_empty());
        assert!(!ctx.interrupted);
    }

    #[test]
    fn context_session_starts_none() {
        let ctx = make_ctx();
        assert!(ctx.session.is_none());
        assert!(ctx.reporter.is_none());
    }
}

mod file_collection_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::FileCollectionPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod session_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::SessionPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod fixtures_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        // FixturesPhase now always returns true (only in pipeline when needed)
        let ctx = make_ctx();
        let phase = phases::FixturesPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod affected_phase_tests {
    use super::*;

    #[test]
    fn skips_when_no_affected_config() {
        let ctx = make_ctx();
        let phase = phases::AffectedPhase;
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_affected_set() {
        let mut ctx = make_ctx();
        ctx.cfg.affected = Some("HEAD~1".to_string());
        let phase = phases::AffectedPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod collection_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::CollectionPhase {
            collector: &crate::pipeline::traits::BridgeCollector,
        };
        assert!(phase.should_run(&ctx));
    }
}

mod strict_phase_tests {
    use super::*;
    use crate::config::StrictMode;

    #[test]
    fn skips_when_strict_none() {
        let ctx = make_ctx();
        let phase = phases::StrictPhase;
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_strict_abort() {
        let mut ctx = make_ctx();
        ctx.cfg.strict = Some(StrictMode::Abort);
        let phase = phases::StrictPhase;
        assert!(phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_strict_enforce() {
        let mut ctx = make_ctx();
        ctx.cfg.strict = Some(StrictMode::Enforce);
        let phase = phases::StrictPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod filter_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::FilterPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod list_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        // ListPhase now always returns true (only in pipeline when needed)
        let ctx = make_ctx();
        let phase = phases::ListPhase;
        assert!(phase.should_run(&ctx));
    }
}

mod execution_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::ExecutionPhase {
            runner: &crate::pipeline::traits::BridgeRunner,
            parallel: &crate::pipeline::traits::DefaultParallelRunner,
        };
        assert!(phase.should_run(&ctx));
    }
}

mod retry_phase_tests {
    use super::*;

    #[test]
    fn skips_when_retries_zero() {
        let ctx = make_ctx();
        let phase = phases::RetryPhase {
            runner: &crate::pipeline::traits::BridgeRunner,
        };
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn skips_when_interrupted() {
        let mut ctx = make_ctx();
        ctx.cfg.retries = 2;
        ctx.interrupted = true;
        let phase = phases::RetryPhase {
            runner: &crate::pipeline::traits::BridgeRunner,
        };
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_retries_set_and_not_interrupted() {
        let mut ctx = make_ctx();
        ctx.cfg.retries = 2;
        ctx.interrupted = false;
        let phase = phases::RetryPhase {
            runner: &crate::pipeline::traits::BridgeRunner,
        };
        assert!(phase.should_run(&ctx));
    }
}

mod finalize_phase_tests {
    use super::*;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::FinalizePhase;
        assert!(phase.should_run(&ctx));
    }
}

mod fixture_validation_phase_tests {
    use super::*;
    use crate::types::NodeId;

    #[test]
    fn always_runs() {
        let ctx = make_ctx();
        let phase = phases::FixtureValidationPhase;
        assert!(phase.should_run(&ctx));
    }

    #[test]
    fn name_is_fixture_validation() {
        let phase = phases::FixtureValidationPhase;
        assert_eq!(phase.name(), "fixture-validation");
    }

    #[test]
    fn format_errors_with_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "sotre".to_string())];
        let registered = vec!["store".to_string(), "backend".to_string()];
        let msg = phases::format_fixture_errors(&errors, &registered);
        assert!(msg.contains("ERROR collecting tests"));
        assert!(msg.contains("fixture 'sotre' not found"));
        assert!(msg.contains("did you mean 'store'?"));
    }

    #[test]
    fn format_errors_without_suggestion() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "zzzzz".to_string())];
        let registered = vec!["store".to_string()];
        let msg = phases::format_fixture_errors(&errors, &registered);
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
        let msg = phases::format_fixture_errors(&errors, &registered);
        assert!(msg.contains("test.py::test_a"));
        assert!(msg.contains("test.py::test_b"));
        assert!(msg.contains("sotre"));
        assert!(msg.contains("xyz"));
    }

    #[test]
    fn format_errors_empty_registered() {
        let errors = vec![(NodeId::from_raw("test.py::test_foo"), "store".to_string())];
        let msg = phases::format_fixture_errors(&errors, &[]);
        assert!(msg.contains("fixture 'store' not found"));
        assert!(!msg.contains("did you mean"));
    }
}
