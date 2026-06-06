//! Test doubles for pipeline unit testing.
//!
//! Provides a configurable [`MockPhase`] implementing [`PipelinePhase`] and a
//! [`StubHarness`] implementing [`ExecutionHarness`].  All types are
//! `#[cfg(test)]`-gated and live exclusively in the test binary.

#[cfg(test)]
pub(crate) mod doubles {
    use std::cell::Cell;
    use std::sync::Arc;

    use camino::Utf8PathBuf;

    use crate::parallel::PhaseResult;
    use crate::pipeline::{PhaseOutcome, PipelineContext, PipelinePhase};
    use crate::reporter::Reporter;
    use crate::types::{ExitCode, TestItem, TestTiming};

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
            _py: pyo3::Python<'_>,
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
