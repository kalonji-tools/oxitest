use ahash::AHashMap;

use super::{CacheEntry, CacheFile, TestCache, CACHE_VERSION};
use crate::types::{DurationMs, NodeId, OutcomeKind, TestTiming};

pub(super) fn cache_with_entries(entries: &[(&str, f64)]) -> TestCache {
    let timings = entries
        .iter()
        .map(|(id, ms)| {
            (
                id.to_string(),
                CacheEntry {
                    duration_ms: DurationMs::new(*ms),
                    age: 0,
                    last_outcome: None,
                    flaky_count: 0,
                },
            )
        })
        .collect();
    TestCache {
        inner: CacheFile {
            version: CACHE_VERSION,
            timings,
            modules: AHashMap::new(),
        },
        dirty: false,
    }
}

pub(super) fn make_timing(node_id: &str, ms: f64, outcome: OutcomeKind) -> TestTiming {
    TestTiming {
        node_id: NodeId::from_raw(node_id),
        duration_ms: DurationMs::new(ms),
        outcome,
    }
}
