//! Core data types shared across the runner.
//!
//! Defines [`NodeId`] (stable test identifier), [`TestItem`] (collected test metadata),
//! [`TestOutcome`] (the eight possible results of running a test), [`CollectError`],
//! and [`TestTiming`].

mod exit;
mod item;
pub mod node_id;
mod outcome;

#[cfg(test)]
pub mod test_support;

// Re-export all public types at the `types::` path so no external imports break.
pub use exit::{ExitCode, TestTiming};
pub use item::{
    ArrangedEntry, FieldDiff, FixtureModule, LocalVar, MarkerSet, PackageDeclaration, ParamPair,
    TestItem,
};
pub use node_id::{DurationMs, LineNo, NodeId};
pub use outcome::{
    CollectError, ComparisonDetail, FailureDiagnostic, Frame, OutcomeKind, ResolvedOutcome,
    TestOutcome,
};

// pub(crate) re-exports
pub use exit::FailureAccumulator;
