//! Deserialization of JSON results from worker subprocesses.
//!
//! Each worker writes one JSON line per test to stdout. This module defines
//! [`WireResult`] (serde-only deserialization target) and [`RawOutcome`], the
//! shared intermediate enum that both the JSON worker path and the serial PyO3
//! path (`bridge.rs`) converge on before producing a final
//! [`TestOutcome`](crate::types::TestOutcome).

mod wire;
pub use wire::*;

mod convert;
pub use convert::RawOutcome;

#[cfg(test)]
mod tests;
