//! Shared test helpers for reporter unit tests.
#![cfg(test)]

use camino::Utf8PathBuf;

use crate::types::{DurationMs, LineNo, NodeId, OutcomeKind, RawOutcome, TestOutcome, TestTiming};

pub(crate) fn make_failed(msg: &str, file: &str, lineno: usize, src: &str) -> TestOutcome {
    TestOutcome::Failed {
        message: msg.to_string(),
        file: Utf8PathBuf::from(file),
        lineno: LineNo::new(lineno),
        source_line: src.to_string(),
        left: String::new(),
        right: String::new(),
        op: String::new(),
        frames: vec![],
        field_diffs: vec![],
    }
}

pub(crate) fn make_error(msg: &str, file: &str, lineno: usize, src: &str) -> TestOutcome {
    TestOutcome::Error {
        message: msg.to_string(),
        file: Utf8PathBuf::from(file),
        lineno: LineNo::new(lineno),
        source_line: src.to_string(),
        frames: vec![],
    }
}

/// Build a zero-duration [`TestTiming`] for the given `node_id` string and `outcome`.
pub(crate) fn make_timing(node_id: &str, outcome: OutcomeKind) -> TestTiming {
    TestTiming {
        node_id: NodeId::from_raw(node_id),
        duration_ms: DurationMs::ZERO,
        outcome,
    }
}

/// Build a [`TestOutcome`] from a bare status string (e.g. `"passed"`, `"failed"`).
///
/// All optional diagnostic fields are left empty. Useful for outcome-routing tests
/// that do not care about failure messages or tracebacks.
pub(crate) fn make_outcome(status: &str) -> TestOutcome {
    TestOutcome::from_raw(RawOutcome {
        status,
        message: "",
        file: "",
        lineno: LineNo::ZERO,
        source_line: "",
        no_message_lines: &[],
        left: "",
        right: "",
        op: "",
        strict: false,
        frames: &[],
        field_diffs: &[],
    })
}

/// Build a minimal [`crate::pipeline::PipelineContext`] suitable for unit tests.
///
/// Uses default config, no-op CLI flags, a missing-path cache (loads empty), and
/// `is_tty = false` / `use_color = false`. All incremental fields start empty.
pub(crate) fn make_ctx() -> crate::pipeline::PipelineContext {
    let cfg = crate::config::Config::default();
    let command = crate::config::Command::Run(crate::config::RunArgs::default_for_test());
    let cache = crate::cache::TestCache::load(camino::Utf8Path::new("/nonexistent"));
    let rootdir = camino::Utf8PathBuf::from(".");
    let use_color = false;
    let base = super::ReporterOptsBuilder::from_config(&cfg, use_color);
    crate::pipeline::PipelineContext::from_setup(crate::pipeline::SetupContext {
        cfg,
        cache,
        command,
        rootdir,
        is_tty: false,
        use_color,
        python_bin: "python3".to_string(),
        base,
    })
}
