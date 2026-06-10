//! Shared test helpers for reporter unit tests.
#![cfg(test)]

use crate::types::{DurationMs, NodeId, OutcomeKind, TestTiming};

/// Build a zero-duration [`TestTiming`] for the given `node_id` string and `outcome`.
pub(crate) fn make_timing(node_id: &str, outcome: OutcomeKind) -> TestTiming {
    TestTiming {
        node_id: NodeId::from_raw(node_id),
        duration_ms: DurationMs::ZERO,
        outcome,
    }
}

/// Build a minimal [`Pipeline<S>`] suitable for unit tests.
///
/// Uses default config, no-op CLI flags, a missing-path cache (loads empty), and
/// `is_tty = false` / `use_color = false`.
pub(crate) fn make_pipeline<S>(state: S) -> crate::pipeline::Pipeline<S> {
    let cfg = crate::config::Config::default();
    let command = crate::config::Command::Run(crate::config::RunArgs::default_for_test());
    let cache = crate::cache::TestCache::load(camino::Utf8Path::new("/nonexistent"));
    let use_color = false;
    let base = super::ReporterOptsBuilder::from_config(&cfg, use_color);
    crate::pipeline::Pipeline {
        shared: crate::pipeline::PipelineShared {
            cfg,
            command,
            rootdir: camino::Utf8PathBuf::from("."),
            is_tty: false,
            use_color,
            base,
            cache,
            python_bin: "python3".to_string(),
            ast_weight_ms: None,
        },
        state,
    }
}
