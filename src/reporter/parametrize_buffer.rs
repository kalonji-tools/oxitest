use crate::types::{DurationMs, TestItem, TestOutcome};

// ─── ParametrizeBuffer ───────────────────────────────────────────────────────

/// Buffers results for all cases of a single parametrized test function.
///
/// Parametrized cases are accumulated here so the reporter can emit a combined
/// summary line (e.g. `test_add — 3 passed, 1 failed`) once all cases finish,
/// rather than one line per case.
///
/// Statistics are computed incrementally on each `push()` to avoid repeated
/// iteration over the results vector.
pub(crate) struct ParametrizeBuffer {
    pub fn_name: String,
    pub results: Vec<(TestItem, TestOutcome, DurationMs)>,
    total_ms: DurationMs,
    has_failure: bool,
    passed_count: usize,
}

impl ParametrizeBuffer {
    pub fn new(fn_name: String) -> Self {
        Self {
            fn_name,
            results: Vec::new(),
            total_ms: DurationMs::ZERO,
            has_failure: false,
            passed_count: 0,
        }
    }

    pub fn push(&mut self, item: TestItem, outcome: TestOutcome, ms: DurationMs) {
        self.total_ms += ms;
        if outcome.is_hard_failure() {
            self.has_failure = true;
        }
        if matches!(outcome, TestOutcome::Passed { .. }) {
            self.passed_count += 1;
        }
        self.results.push((item, outcome, ms));
    }

    pub fn total_ms(&self) -> DurationMs {
        self.total_ms
    }

    pub fn any_failed(&self) -> bool {
        self.has_failure
    }

    pub fn passed_count(&self) -> usize {
        self.passed_count
    }
}
