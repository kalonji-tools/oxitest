//! Trait seams for pipeline testability.

use crate::{parallel, scheduler};

/// Abstraction over test execution strategies (serial, parallel).
///
/// Given grouped test items, executes them and returns timing results.
pub(crate) trait ExecutionHarness {
    fn execute_groups(
        &self,
        groups: Vec<scheduler::ModuleGroup>,
        rep: &mut dyn crate::reporter::Reporter,
    ) -> parallel::PhaseResult;
}
