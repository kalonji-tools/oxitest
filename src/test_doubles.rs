//! Test doubles for pipeline unit testing.
//!
//! Provides a [`StubHarness`] implementing [`ExecutionHarness`].
//! All types are `#[cfg(test)]`-gated and live exclusively in the test binary.

#[cfg(test)]
pub(crate) mod doubles {
    use std::sync::Arc;

    use camino::Utf8PathBuf;

    use crate::parallel::PhaseResult;
    use crate::reporter::Reporter;
    use crate::types::{TestItem, TestTiming};

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
