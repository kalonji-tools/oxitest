//! Deserialization of JSON results from worker subprocesses.
//!
//! Each worker writes one JSON line per test to stdout. This module defines
//! [`WireResult`] (serde-only deserialization target) whose
//! [`into_outcome`](WireResult::into_outcome) method produces a
//! [`TestOutcome`](crate::types::TestOutcome) directly. The serial PyO3 path
//! (`bridge.rs`) likewise produces `TestOutcome` without an intermediate enum.

mod wire;
pub(crate) use wire::*;

mod convert;
pub(crate) use convert::build_diagnostic;

#[cfg(test)]
mod tests;
