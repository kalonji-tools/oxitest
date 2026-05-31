//! Test doubles for pipeline unit testing.
//!
//! Provides in-memory implementations of the four pipeline trait seams
//! ([`Session`], [`ModuleCollector`], [`TestRunner`], [`ParallelRunner`]) and a
//! configurable [`MockPhase`] implementing [`PipelinePhase`].  All types are
//! `#[cfg(test)]`-gated and live exclusively in the test binary.

#[cfg(test)]
pub(crate) mod doubles {
    use std::cell::{Cell, RefCell};
    use std::collections::HashMap;
    use std::sync::Arc;

    use camino::{Utf8Path, Utf8PathBuf};
    use pyo3::prelude::*;

    use crate::bridge::RawViolation;
    use crate::config::Config;
    use crate::parallel::PhaseResult;
    use crate::pipeline::traits::{ModuleCollector, ParallelRunner, Session, TestRunner};
    use crate::pipeline::{PhaseOutcome, PipelineContext, PipelinePhase};
    use crate::reporter::Reporter;
    use crate::types::{CollectError, ExitCode, TestItem, TestOutcome, TestTiming};

    // ─── StubCollector ───────────────────────────────────────────────────────

    /// In-memory [`ModuleCollector`] that returns pre-configured results.
    ///
    /// If the requested path has no entry, returns `Ok((vec![], vec![]))`.
    pub(crate) struct StubCollector {
        #[allow(clippy::type_complexity)]
        pub results: HashMap<Utf8PathBuf, Result<(Vec<TestItem>, Vec<RawViolation>), CollectError>>,
    }

    impl ModuleCollector for StubCollector {
        fn collect_module(
            &self,
            _py: Python<'_>,
            path: &Utf8Path,
            _session: &dyn Session,
            _collect_violations: bool,
        ) -> Result<(Vec<TestItem>, Vec<RawViolation>), CollectError> {
            match self.results.get(path) {
                Some(Ok((items, violations))) => Ok((items.clone(), violations.clone())),
                Some(Err(e)) => Err(match e {
                    CollectError::ImportError { path, message } => CollectError::ImportError {
                        path: path.clone(),
                        message: message.clone(),
                    },
                    CollectError::PyError(msg) => CollectError::PyError(msg.clone()),
                    CollectError::Affected(err) => CollectError::Affected(err.clone()),
                }),
                None => Ok((vec![], vec![])),
            }
        }
    }

    // ─── StubRunner ──────────────────────────────────────────────────────────

    /// In-memory [`TestRunner`] that returns pre-configured outcomes.
    ///
    /// Records each call as `(node_id_string, timeout)` in `calls`.
    /// For any node ID without a configured outcome, returns
    /// `TestOutcome::Passed { no_message_lines: vec![] }`.
    pub(crate) struct StubRunner {
        pub outcomes: HashMap<String, TestOutcome>,
        pub calls: RefCell<Vec<(String, Option<u64>)>>,
    }

    impl Default for StubRunner {
        fn default() -> Self {
            Self {
                outcomes: HashMap::new(),
                calls: RefCell::new(vec![]),
            }
        }
    }

    impl TestRunner for StubRunner {
        #[allow(clippy::too_many_arguments)]
        fn run_test(
            &self,
            _py: Python<'_>,
            item: &TestItem,
            _session: &dyn Session,
            timeout: Option<u64>,
            _debug_mode: Option<&str>,
            _keep_tmp: Option<&str>,
            _show_locals: bool,
            _show_internals: bool,
        ) -> TestOutcome {
            let node_id = item.node_id.to_string();
            self.calls.borrow_mut().push((node_id.clone(), timeout));
            self.outcomes
                .get(&node_id)
                .cloned()
                .unwrap_or(TestOutcome::Passed {
                    no_message_lines: vec![],
                })
        }
    }

    // ─── StubParallelRunner ──────────────────────────────────────────────────

    /// In-memory [`ParallelRunner`] that returns a fixed [`PhaseResult`].
    #[allow(dead_code)] // Used by later tasks (pipeline execution contract tests).
    pub(crate) struct StubParallelRunner {
        pub result: PhaseResult,
    }

    impl Default for StubParallelRunner {
        fn default() -> Self {
            Self {
                result: PhaseResult {
                    interrupted: false,
                    timings: vec![],
                },
            }
        }
    }

    impl ParallelRunner for StubParallelRunner {
        fn run_parallel(
            &self,
            _groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
            _cfg: &Config,
            _workers: usize,
            _conftest_files: &[Utf8PathBuf],
            _python_bin: &str,
            _rep: &mut dyn Reporter,
        ) -> PhaseResult {
            PhaseResult {
                interrupted: self.result.interrupted,
                timings: self.result.timings.clone(),
            }
        }
    }

    // ─── RecordingSession ────────────────────────────────────────────────────

    /// In-memory [`Session`] that records lifecycle calls for assertion.
    ///
    /// `end_module_calls` accumulates every path passed to [`end_module`].
    /// `end_session_called` is set to `true` on the first [`end_session`] call.
    pub(crate) struct RecordingSession {
        pub obj: Py<PyAny>,
        pub end_module_calls: RefCell<Vec<Utf8PathBuf>>,
        pub end_session_called: Cell<bool>,
    }

    impl RecordingSession {
        pub(crate) fn new(py: Python<'_>) -> Self {
            Self {
                obj: py.None(),
                end_module_calls: RefCell::new(vec![]),
                end_session_called: Cell::new(false),
            }
        }
    }

    impl Session for RecordingSession {
        fn end_module(&self, _py: Python<'_>, module_path: &Utf8Path) -> PyResult<()> {
            self.end_module_calls
                .borrow_mut()
                .push(module_path.to_owned());
            Ok(())
        }

        fn end_session(&self, _py: Python<'_>) -> PyResult<()> {
            self.end_session_called.set(true);
            Ok(())
        }

        fn shared_fixture_names(&self, _py: Python<'_>) -> Vec<String> {
            vec![]
        }

        fn shared_fixture_groups(&self, _py: Python<'_>) -> Vec<Vec<String>> {
            vec![]
        }

        fn as_py_object<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
            self.obj.bind(py).clone()
        }
    }

    // ─── MockPhase ───────────────────────────────────────────────────────────

    /// Configurable [`PipelinePhase`] that records whether it was executed.
    ///
    /// `exit_code` maps directly to the returned [`PhaseOutcome`]:
    /// - `None` → [`PhaseOutcome::Continue`]
    /// - `Some(code)` → [`PhaseOutcome::EarlyExit(code)`]
    pub(crate) struct MockPhase {
        pub name: &'static str,
        pub should_run: bool,
        pub exit_code: Option<ExitCode>,
        pub called: Cell<bool>,
    }

    impl MockPhase {
        pub(crate) fn new(name: &'static str, should_run: bool, outcome: PhaseOutcome) -> Self {
            let exit_code = match outcome {
                PhaseOutcome::Continue => None,
                PhaseOutcome::EarlyExit(code) => Some(code),
            };
            Self {
                name,
                should_run,
                exit_code,
                called: Cell::new(false),
            }
        }

        pub(crate) fn was_called(&self) -> bool {
            self.called.get()
        }
    }

    impl PipelinePhase for MockPhase {
        fn name(&self) -> &'static str {
            self.name
        }

        fn should_run(&self, _ctx: &PipelineContext) -> bool {
            self.should_run
        }

        fn execute(
            &self,
            _py: Python<'_>,
            _ctx: &mut PipelineContext,
        ) -> Result<PhaseOutcome, ExitCode> {
            self.called.set(true);
            match self.exit_code {
                None => Ok(PhaseOutcome::Continue),
                Some(code) => Ok(PhaseOutcome::EarlyExit(code)),
            }
        }
    }

    // ─── StubHarness ─────────────────────────────────────────────────────────

    /// Stub harness that returns a configurable [`PhaseResult`].
    #[allow(dead_code)] // Used by later tasks (execution harness contract tests).
    pub(crate) struct StubHarness {
        pub result: PhaseResult,
    }

    impl crate::pipeline::traits::ExecutionHarness for StubHarness {
        fn execute_groups(
            &self,
            _groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
            _rep: &mut dyn Reporter,
        ) -> PhaseResult {
            PhaseResult {
                interrupted: self.result.interrupted,
                timings: self.result.timings.clone(),
            }
        }
    }

    // ─── Helpers ─────────────────────────────────────────────────────────────

    /// Build a minimal [`TestItem`] with the given node-id string.
    ///
    /// All other fields are set to sensible empty defaults. Useful for
    /// constructing [`StubRunner`] / [`StubCollector`] inputs without
    /// repeating boilerplate in each test.
    pub(crate) fn make_test_item(node_id_str: &str) -> TestItem {
        TestItem::builder_raw(node_id_str).lineno(1).build()
    }

    /// Build a [`TestItem`] with the `inprocess` marker set.
    pub(crate) fn make_inprocess_item(node_id_str: &str) -> TestItem {
        TestItem::builder_raw(node_id_str)
            .lineno(1)
            .markers(vec!["inprocess".to_string()])
            .build()
    }

    /// Build a zero-duration [`TestTiming`] for a given node-id string.
    #[allow(dead_code)] // Used by later tasks (execution contract tests).
    pub(crate) fn make_timing(node_id_str: &str) -> TestTiming {
        use crate::types::{DurationMs, NodeId, OutcomeKind};
        TestTiming {
            node_id: NodeId::from_raw(node_id_str),
            duration_ms: DurationMs::ZERO,
            outcome: OutcomeKind::Passed,
        }
    }
}
