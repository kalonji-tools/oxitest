use pyo3::prelude::*;

use super::ReporterSession;
use crate::types::{CollectError, DurationMs, TestItem, TestOutcome};

/// Wraps a Python plugin reporter object and forwards `Reporter` trait calls
/// through PyO3. Acquires the GIL on each call.
pub(crate) struct PyPluginReporter {
    obj: Py<PyAny>,
}

impl PyPluginReporter {
    pub fn new(obj: Py<PyAny>) -> Self {
        Self { obj }
    }
}

impl super::Reporter for PyPluginReporter {
    fn test_started(&mut self, item: &TestItem) {
        Python::attach(|py| {
            let node_id = item.node_id.to_string();
            if let Err(e) = self.obj.call_method1(py, "test_started", (node_id,)) {
                tracing::warn!("Plugin reporter test_started error: {e}");
            }
        });
    }

    fn test_completed(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
        _parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
    ) {
        Python::attach(|py| {
            let node_id = item.node_id.to_string();
            let status = outcome.as_str();
            if let Err(e) = self.obj.call_method1(
                py,
                "test_completed",
                (node_id, status, duration_ms.as_f64()),
            ) {
                tracing::warn!("Plugin reporter test_completed error: {e}");
            }
        });
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        _session: &ReporterSession,
    ) -> super::ExitVote {
        Python::attach(|py| {
            let err_count = collect_errors.len();
            if let Err(e) = self
                .obj
                .call_method1(py, "finish", (err_count, interrupted))
            {
                tracing::warn!("Plugin reporter finish error: {e}");
            }
        });
        // Plugin reporters don't influence exit code
        super::ExitVote::Abstain
    }
}
