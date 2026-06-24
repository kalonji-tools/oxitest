//! Test doubles for pipeline unit testing.
//!
//! All types are `#[cfg(test)]`-gated and live exclusively in the test binary.

#[cfg(test)]
pub(crate) mod doubles {
    use crate::parallel::PhaseResult;
    use crate::types::TestTiming;

    // ─── Helpers ─────────────────────────────────────────────────────────────

    /// Return a canned [`PhaseResult`] for use in tests that need a stub execution outcome.
    ///
    /// Replaces the former `StubHarness` struct; since `ExecutionDispatch` is a concrete enum
    /// (not a trait object), tests can supply canned results directly without a separate
    /// harness abstraction.
    #[allow(dead_code)] // Used by later tasks (execution contract tests).
    pub(crate) fn stub_phase_result(interrupted: bool, timings: Vec<TestTiming>) -> PhaseResult {
        PhaseResult {
            interrupted,
            timings,
        }
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
