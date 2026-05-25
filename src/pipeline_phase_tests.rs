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
        let outcome = PhaseOutcome::EarlyExit(3);
        assert!(matches!(outcome, PhaseOutcome::EarlyExit(3)));
        if let PhaseOutcome::EarlyExit(code) = outcome {
            assert_eq!(code, 3);
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
    fn skips_when_fixtures_flag_not_set() {
        let ctx = make_ctx();
        let phase = phases::FixturesPhase;
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_fixtures_flag_set() {
        let mut ctx = make_ctx();
        ctx.cli.fixtures = true;
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
    fn skips_when_list_not_set() {
        let ctx = make_ctx();
        let phase = phases::ListPhase;
        assert!(!phase.should_run(&ctx));
    }

    #[test]
    fn runs_when_list_set() {
        let mut ctx = make_ctx();
        ctx.cli.list = true;
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
