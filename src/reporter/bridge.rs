//! PyO3 bridge functions for the reporter subsystem.
//!
//! These free functions extract fixture timing and cache statistics from the
//! Python [`FixtureSession`] for use by
//! reporters during finalization.

use pyo3::prelude::*;

use super::stats::{FixtureCacheEntry, FixtureCacheStats, FixtureTimingEntry};
use crate::bridge::FixtureSession;
use crate::types::DurationMs;

#[derive(FromPyObject)]
struct BridgeCacheEntry {
    name: String,
    hits: usize,
    misses: usize,
}

#[derive(FromPyObject)]
struct BridgeCacheStats {
    total_hits: usize,
    total_misses: usize,
    breakdown: Vec<BridgeCacheEntry>,
}

#[derive(FromPyObject)]
struct BridgeFixtureTiming {
    name: String,
    total_setup_ms: f64,
    setup_count: usize,
    total_teardown_ms: f64,
    teardown_count: usize,
}

/// Fetch shared fixture cache hit/miss statistics.
pub(crate) fn get_cache_stats(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<FixtureCacheStats> {
    let obj = session.as_py_object(py);
    let raw: BridgeCacheStats = obj.call_method0("get_cache_stats")?.extract()?;
    Ok(FixtureCacheStats {
        hits: raw.total_hits,
        misses: raw.total_misses,
        breakdown: raw
            .breakdown
            .into_iter()
            .map(|e| FixtureCacheEntry {
                name: e.name,
                hits: e.hits,
                misses: e.misses,
            })
            .collect(),
    })
}

/// Fetch per-fixture setup/teardown timing aggregates.
pub(crate) fn get_fixture_timings(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<FixtureTimingEntry>> {
    let obj = session.as_py_object(py);
    let raw: Vec<BridgeFixtureTiming> = obj.call_method0("get_fixture_timings")?.extract()?;
    Ok(raw
        .into_iter()
        .map(|e| FixtureTimingEntry {
            name: e.name,
            total_setup: DurationMs::new(e.total_setup_ms),
            setup_count: e.setup_count,
            total_teardown: DurationMs::new(e.total_teardown_ms),
            teardown_count: e.teardown_count,
        })
        .collect())
}
