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
            let kwargs = pyo3::types::PyDict::new(py);
            kwargs.set_item("item", &node_id).ok();
            if let Err(e) = self.obj.call_method(py, "test_started", (), Some(&kwargs)) {
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
            let kwargs = pyo3::types::PyDict::new(py);
            kwargs.set_item("item", &node_id).ok();
            kwargs.set_item("outcome", status).ok();
            kwargs.set_item("duration_ms", duration_ms.as_f64()).ok();
            if let Err(e) = self
                .obj
                .call_method(py, "test_completed", (), Some(&kwargs))
            {
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
            let kwargs = pyo3::types::PyDict::new(py);
            if let Err(e) = kwargs
                .set_item("collect_errors", collect_errors.len())
                .and_then(|()| kwargs.set_item("interrupted", interrupted))
            {
                tracing::warn!("Plugin reporter finish kwargs error: {e}");
                return;
            }
            if let Err(e) = self.obj.call_method(py, "finish", (), Some(&kwargs)) {
                tracing::warn!("Plugin reporter finish error: {e}");
            }
        });
        // Plugin reporters don't influence exit code
        super::ExitVote::Abstain
    }
}
