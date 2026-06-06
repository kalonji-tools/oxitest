//! PyO3 bridge functions for the reporter subsystem.
//!
//! These free functions extract fixture timing and cache statistics from the
//! Python [`FixtureSession`](crate::bridge::FixtureSession) for use by
//! reporters during finalization.

use pyo3::prelude::*;

use super::stats::{FixtureCacheEntry, FixtureCacheStats, FixtureTimingEntry};
use crate::bridge::FixtureSession;

/// Fetch shared fixture cache hit/miss statistics.
pub(crate) fn get_cache_stats(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<FixtureCacheStats> {
    let obj = session.as_py_object(py);
    let result = obj.call_method0("get_cache_stats")?;
    let total_hits: usize = result.get_item("total_hits")?.extract()?;
    let total_misses: usize = result.get_item("total_misses")?.extract()?;
    let breakdown_list = result.get_item("breakdown")?;
    let mut breakdown = Vec::new();
    for entry in breakdown_list.try_iter()? {
        let entry: Bound<'_, PyAny> = entry?;
        let name: String = entry.get_item("name")?.extract()?;
        let hits: usize = entry.get_item("hits")?.extract()?;
        let misses: usize = entry.get_item("misses")?.extract()?;
        breakdown.push(FixtureCacheEntry { name, hits, misses });
    }
    Ok(FixtureCacheStats {
        hits: total_hits,
        misses: total_misses,
        breakdown,
    })
}

/// Fetch per-fixture setup/teardown timing aggregates.
pub(crate) fn get_fixture_timings(
    session: &FixtureSession,
    py: Python<'_>,
) -> PyResult<Vec<FixtureTimingEntry>> {
    let obj = session.as_py_object(py);
    let result = obj.call_method0("get_fixture_timings")?;
    let mut timings = Vec::new();
    for entry in result.try_iter()? {
        let entry: Bound<'_, PyAny> = entry?;
        let name: String = entry.get_item("name")?.extract()?;
        let total_setup_ms: f64 = entry.get_item("total_setup_ms")?.extract()?;
        let setup_count: usize = entry.get_item("setup_count")?.extract()?;
        let total_teardown_ms: f64 = entry.get_item("total_teardown_ms")?.extract()?;
        let teardown_count: usize = entry.get_item("teardown_count")?.extract()?;
        timings.push(FixtureTimingEntry {
            name,
            total_setup_ms,
            setup_count,
            total_teardown_ms,
            teardown_count,
        });
    }
    Ok(timings)
}
