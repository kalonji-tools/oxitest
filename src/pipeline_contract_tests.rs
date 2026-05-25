use super::*;

fn make_ctx() -> PipelineContext {
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
