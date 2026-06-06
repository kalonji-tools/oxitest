//! Trait seams for pipeline testability.

use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::{parallel, types};

/// Abstraction over test execution strategies (serial, parallel).
///
/// Given grouped test items, executes them and returns timing results.
pub(crate) trait ExecutionHarness {
    fn execute_groups(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
        rep: &mut dyn crate::reporter::Reporter,
    ) -> parallel::PhaseResult;
}
