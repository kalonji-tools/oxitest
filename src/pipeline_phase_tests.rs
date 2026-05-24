use super::*;

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

    fn make_test_context() -> PipelineContext {
        let cfg = config::Config::default();
        let cli = config::Cli::default_for_test();
        let rootdir = camino::Utf8PathBuf::from(".");
        let is_tty = false;
        let use_color = false;
        let base = reporter::ReporterOptsBuilder::from_config(&cfg, use_color);
        let cache = cache::TestCache::load(camino::Utf8Path::new("/nonexistent"));
        PipelineContext::from_setup(SetupContext {
            cfg,
            cache,
            cli,
            rootdir,
            is_tty,
            use_color,
            base,
        })
    }

    #[test]
    fn context_starts_with_empty_items() {
        let ctx = make_test_context();
        assert!(ctx.items.is_empty());
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
        let ctx = make_test_context();
        assert!(ctx.session.is_none());
        assert!(ctx.reporter.is_none());
    }
}
