use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use super::{CacheEntry, TestCache};
use crate::scheduler::ModuleGroup;
use crate::types::{DurationMs, TestItem};

/// Cache for test timing data (scheduling, timeout suggestions, duration estimates).
pub trait TimingCache {
    /// Estimate total duration for the given items.
    ///
    /// `ast_fallback_ms` is the sum of AST-derived body weights for the same item list.
    /// When the cache has >= 50% coverage, the cached sum is used directly.
    /// When coverage < 50% and `ast_fallback_ms` is provided, a blend is returned:
    ///   `cached_ms + ast_total * uncovered_fraction`.
    /// When fully cold (0 covered) and `ast_fallback_ms` is provided, `ast_total` is used.
    /// Otherwise returns `None`.
    #[must_use = "caller must use the duration estimate to decide parallel vs serial"]
    fn estimated_duration(
        &self,
        items: &[Arc<TestItem>],
        ast_fallback_ms: Option<f64>,
    ) -> Option<Duration>;
    fn suggested_timeout_secs(&self, item: &TestItem, multiplier: f64) -> Option<u64>;
    fn sort_groups(&self, groups: &mut Vec<ModuleGroup>);
    fn merge_timings(&mut self, timings: &[crate::types::TestTiming], max_age: u32);
    fn invalidate(&mut self, items: &[Arc<TestItem>]);
}

impl TestCache {
    /// Returns `(total_duration_ms, covered_count)` for items present in the timing cache.
    pub(super) fn sum_and_count(&self, items: &[Arc<TestItem>]) -> (f64, usize) {
        let mut total = 0.0f64;
        let mut count = 0usize;
        for item in items {
            if let Some(entry) = self.inner.timings.get(item.node_id.as_ref()) {
                total += entry.duration_ms.as_f64();
                count += 1;
            }
        }
        (total, count)
    }

    pub(super) fn module_duration_sum(&self, items: &[Arc<TestItem>]) -> Option<f64> {
        let (total, count) = self.sum_and_count(items);
        if count > 0 {
            Some(total)
        } else {
            None
        }
    }
}

impl TimingCache for TestCache {
    fn estimated_duration(
        &self,
        items: &[Arc<TestItem>],
        ast_fallback_ms: Option<f64>,
    ) -> Option<Duration> {
        if items.is_empty() {
            return None;
        }
        let (cached_ms, covered) = self.sum_and_count(items);
        let total = items.len();

        // >= 50% coverage: use the cached sum directly (existing behaviour).
        if covered * 2 >= total {
            return Some(Duration::from_millis(cached_ms as u64));
        }

        // < 50% coverage — try AST fallback.
        if let Some(ast_total) = ast_fallback_ms {
            if covered == 0 {
                // Fully cold: use AST estimate directly.
                return Some(Duration::from_millis(ast_total as u64));
            }
            // Partial cache: blend cached + AST estimate for uncovered fraction.
            let uncovered_fraction = (total - covered) as f64 / total as f64;
            // NOTE: ast_total is the sum across ALL prescan items, not just uncovered ones.
            // The proportional blend is an approximation — it assumes per-test AST weights
            // are roughly uniform. This is acceptable for a heuristic that only applies
            // to partial cache scenarios (new tests added between runs).
            let blended = cached_ms + ast_total * uncovered_fraction;
            return Some(Duration::from_millis(blended as u64));
        }

        None
    }

    /// Returns a suggested timeout in whole seconds for `item`, scaled by `multiplier`.
    /// Returns `None` if the item has no cached timing (caller should use global timeout).
    /// Result is `ceil(cached_duration_secs * multiplier)`, minimum 1 second.
    fn suggested_timeout_secs(&self, item: &TestItem, multiplier: f64) -> Option<u64> {
        let entry = self.inner.timings.get(item.node_id.as_ref())?;
        let scaled_secs = (entry.duration_ms.as_f64() / 1000.0) * multiplier;
        Some((scaled_secs.ceil() as u64).max(1))
    }

    /// Sort module groups heaviest-first for optimal parallel scheduling.
    ///
    /// Groups with known total duration are sorted by descending sum of cached
    /// durations. Uncached groups fall back to descending item count. Assigning
    /// the heaviest module to the first worker minimises tail latency by ensuring
    /// the longest-running work starts immediately.
    fn sort_groups(&self, groups: &mut Vec<ModuleGroup>) {
        // Pre-compute (duration_sum, item_count) for each group once — O(N*M) total.
        // Avoids re-running module_duration_sum inside the comparator, which would be
        // O(N log N * M) because the comparator fires once per sort comparison.
        let mut keyed: Vec<(Option<f64>, usize, ModuleGroup)> = std::mem::take(groups)
            .into_iter()
            .map(|g| {
                let sum = self.module_duration_sum(&g.items);
                let len = g.items.len();
                (sum, len, g)
            })
            .collect();

        keyed.sort_by(
            |(sum_a, len_a, ..), (sum_b, len_b, ..)| match (sum_a, sum_b) {
                (Some(da), Some(db)) => db.partial_cmp(da).unwrap_or(std::cmp::Ordering::Equal),
                (Some(_), None) => std::cmp::Ordering::Less,
                (None, Some(_)) => std::cmp::Ordering::Greater,
                (None, None) => len_b.cmp(len_a),
            },
        );

        *groups = keyed.into_iter().map(|(_, _, g)| g).collect();
    }

    /// Merge test timings directly from `&[TestTiming]`, avoiding intermediate allocations.
    fn merge_timings(&mut self, timings: &[crate::types::TestTiming], max_age: u32) {
        let executed: HashSet<&str> = timings.iter().map(|t| t.node_id.as_ref()).collect();

        for t in timings {
            let entry = self
                .inner
                .timings
                .entry(t.node_id.to_string())
                .or_insert(CacheEntry {
                    duration_ms: DurationMs::ZERO,
                    age: 0,
                    last_outcome: None,
                    flaky_count: 0,
                });
            entry.duration_ms = t.duration_ms;
            entry.age = 0;
        }

        let before = self.inner.timings.len();
        self.inner.timings.retain(|key, entry| {
            if executed.contains(key.as_str()) {
                return true;
            }
            entry.age += 1;
            entry.age <= max_age
        });

        if !timings.is_empty() || self.inner.timings.len() != before {
            self.dirty = true;
        }
    }

    /// Remove timing entries whose node IDs are not in the current item list.
    ///
    /// Called after collection to prune stale entries (e.g. deleted or renamed tests).
    /// Sets `dirty = true` if any entries were removed, triggering a cache save.
    fn invalidate(&mut self, items: &[Arc<TestItem>]) {
        let live: HashSet<&str> = items.iter().map(|item| item.node_id.as_ref()).collect();
        let before = self.inner.timings.len();
        self.inner
            .timings
            .retain(|key, _| live.contains(key.as_str()));
        if self.inner.timings.len() != before {
            self.dirty = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use camino::Utf8PathBuf;

    use super::super::test_helpers::{cache_with_entries, make_timing};
    use super::*;
    use crate::types::{OutcomeKind, TestItem};
    use std::sync::Arc;

    fn make_group(module: &str, names: &[&str]) -> crate::scheduler::ModuleGroup {
        let items = names
            .iter()
            .map(|n| TestItem::builder(module, n).arc())
            .collect();
        crate::scheduler::ModuleGroup::new(Utf8PathBuf::from(module), items)
    }

    #[test]
    fn invalidate_removes_entries_not_in_items() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
        ]);
        let items = vec![TestItem::builder_raw("tests/test_foo.py::test_a").arc()];
        cache.invalidate(&items);
        assert!(!cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_b"));
        assert!(cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_a"));
    }

    #[test]
    fn invalidate_keeps_entries_present_in_items() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let items = vec![TestItem::builder_raw("tests/test_foo.py::test_a").arc()];
        cache.invalidate(&items);
        assert_eq!(cache.inner.timings.len(), 1);
    }

    #[test]
    fn invalidate_sets_dirty_when_entries_pruned() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_gone", 5.0),
        ]);
        let items = vec![TestItem::builder_raw("tests/test_foo.py::test_a").arc()];
        cache.invalidate(&items);
        assert!(cache.dirty);
    }

    #[test]
    fn invalidate_does_not_set_dirty_when_nothing_pruned() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let items = vec![TestItem::builder_raw("tests/test_foo.py::test_a").arc()];
        cache.invalidate(&items);
        assert!(!cache.dirty);
    }

    #[test]
    fn sort_groups_orders_by_sum_duration_heaviest_first() {
        let cache = cache_with_entries(&[
            ("tests/fast.py::test_a", 5.0),
            ("tests/slow.py::test_x", 200.0),
            ("tests/slow.py::test_y", 300.0),
        ]);
        let mut groups = vec![
            make_group("tests/fast.py", &["test_a"]),
            make_group("tests/slow.py", &["test_x", "test_y"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/slow.py"));
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/fast.py"));
    }

    #[test]
    fn sort_groups_puts_uncached_modules_after_cached() {
        let cache = cache_with_entries(&[("tests/known.py::test_a", 10.0)]);
        let mut groups = vec![
            make_group("tests/unknown.py", &["test_z"]),
            make_group("tests/known.py", &["test_a"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/known.py"));
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/unknown.py"));
    }

    #[test]
    fn sort_groups_falls_back_to_count_when_no_cache_data() {
        let cache = TestCache::empty();
        let mut groups = vec![
            make_group("tests/small.py", &["test_a"]),
            make_group("tests/large.py", &["test_x", "test_y", "test_z"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/large.py"));
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/small.py"));
    }

    #[test]
    fn sort_groups_partial_cache_uses_available_data() {
        let cache = cache_with_entries(&[
            ("tests/slow.py::test_x", 50.0),
            ("tests/fast.py::test_a", 5.0),
        ]);
        let mut groups = vec![
            make_group("tests/fast.py", &["test_a"]),
            make_group("tests/slow.py", &["test_x", "test_uncached"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/slow.py"));
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/fast.py"));
    }

    #[test]
    fn estimated_duration_returns_none_on_empty_items() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        assert!(cache.estimated_duration(&[], None).is_none());
    }

    #[test]
    fn estimated_duration_returns_none_below_half_coverage() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_c").arc(),
        ];
        assert!(cache.estimated_duration(&items, None).is_none());
    }

    #[test]
    fn estimated_duration_returns_some_at_exactly_half_coverage() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
        ];
        assert!(cache.estimated_duration(&items, None).is_some());
    }

    #[test]
    fn estimated_duration_sums_cached_entries() {
        let cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 100.0),
            ("tests/test_foo.py::test_b", 200.0),
        ]);
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
        ];
        let est = cache.estimated_duration(&items, None).unwrap();
        assert_eq!(est.as_millis(), 300);
    }

    #[test]
    fn suggested_timeout_secs_returns_none_for_uncached_item() {
        let cache = TestCache::empty();
        let item = TestItem::builder_raw("tests/test_foo.py::test_a").arc();
        assert!(cache.suggested_timeout_secs(&item, 3.0).is_none());
    }

    #[test]
    fn suggested_timeout_secs_returns_scaled_duration() {
        // cached: 500ms = 0.5s -> multiplier 3.0 -> 1.5s -> ceil -> 2s
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 500.0)]);
        let item = TestItem::builder_raw("tests/test_foo.py::test_a").arc();
        let timeout = cache.suggested_timeout_secs(&item, 3.0).unwrap();
        assert_eq!(timeout, 2); // ceil(0.5 * 3.0) = ceil(1.5) = 2
    }

    #[test]
    fn suggested_timeout_secs_rounds_up() {
        // cached: 100ms = 0.1s -> multiplier 2.0 -> 0.2s -> ceil -> 1s (minimum 1)
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let item = TestItem::builder_raw("tests/test_foo.py::test_a").arc();
        let timeout = cache.suggested_timeout_secs(&item, 2.0).unwrap();
        assert_eq!(timeout, 1); // ceil(0.2) = 1
    }

    #[test]
    fn suggested_timeout_secs_exact_seconds() {
        // cached: 2000ms = 2s -> multiplier 3.0 -> 6s exactly
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 2000.0)]);
        let item = TestItem::builder_raw("tests/test_foo.py::test_a").arc();
        let timeout = cache.suggested_timeout_secs(&item, 3.0).unwrap();
        assert_eq!(timeout, 6);
    }

    // -- merge_timings --

    #[test]
    fn merge_timings_updates_duration_and_resets_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_a")
            .unwrap()
            .age = 5;
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            42.0,
            OutcomeKind::Passed,
        )];
        cache.merge_timings(&timings, 50);
        let entry = &cache.inner.timings["tests/test_foo.py::test_a"];
        assert!((entry.duration_ms.as_f64() - 42.0).abs() < 0.01);
        assert_eq!(entry.age, 0);
    }

    #[test]
    fn merge_timings_increments_age_for_unexecuted_tests() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
        ]);
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Passed,
        )];
        cache.merge_timings(&timings, 50);
        assert_eq!(cache.inner.timings["tests/test_foo.py::test_b"].age, 1);
    }

    #[test]
    fn merge_timings_drops_entries_exceeding_max_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 50;
        cache.merge_timings(&[], 50);
        assert!(!cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
    }

    #[test]
    fn merge_timings_keeps_entries_at_max_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 49;
        cache.merge_timings(&[], 50);
        assert!(cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
    }

    #[test]
    fn merge_timings_adds_new_entry() {
        let mut cache = TestCache::empty();
        let timings = vec![make_timing(
            "tests/test_foo.py::test_new",
            15.0,
            OutcomeKind::Passed,
        )];
        cache.merge_timings(&timings, 50);
        assert!(cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_new"));
        assert_eq!(cache.inner.timings["tests/test_foo.py::test_new"].age, 0);
    }

    #[test]
    fn merge_timings_sets_dirty_when_results_present() {
        let mut cache = TestCache::empty();
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Passed,
        )];
        cache.merge_timings(&timings, 50);
        assert!(cache.dirty);
    }

    #[test]
    fn merge_timings_sets_dirty_when_only_entries_dropped() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 50;
        cache.merge_timings(&[], 50);
        assert!(!cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
        assert!(cache.dirty);
    }

    // ── estimated_duration with AST fallback ──────────────────────────

    #[test]
    fn estimated_duration_cold_cache_with_ast_fallback() {
        // Cold cache (0 covered), 2 items — AST fallback used directly.
        let cache = TestCache::empty();
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
        ];
        let est = cache.estimated_duration(&items, Some(400.0)).unwrap();
        assert_eq!(est.as_millis(), 400);
    }

    #[test]
    fn estimated_duration_partial_cache_blends_with_ast() {
        // 1 of 4 items cached (25% < 50%). AST total = 200ms.
        // uncovered_fraction = 3/4 = 0.75. blend = 50.0 + 200.0 * 0.75 = 200ms.
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 50.0)]);
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_c").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_d").arc(),
        ];
        let est = cache.estimated_duration(&items, Some(200.0)).unwrap();
        assert_eq!(est.as_millis(), 200); // 50 + 200*0.75 = 200
    }

    #[test]
    fn estimated_duration_warm_cache_ignores_ast_fallback() {
        // 2 of 2 items cached (100% >= 50%) — cached sum used, AST ignored.
        let cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 100.0),
            ("tests/test_foo.py::test_b", 200.0),
        ]);
        let items = vec![
            TestItem::builder_raw("tests/test_foo.py::test_a").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_b").arc(),
        ];
        // AST says 999ms, but warm cache returns 300ms.
        let est = cache.estimated_duration(&items, Some(999.0)).unwrap();
        assert_eq!(est.as_millis(), 300);
    }

    #[test]
    fn estimated_duration_no_ast_no_cache_returns_none() {
        // Cold cache, no AST fallback — returns None.
        let cache = TestCache::empty();
        let items = vec![TestItem::builder_raw("tests/test_foo.py::test_a").arc()];
        assert!(cache.estimated_duration(&items, None).is_none());
    }

    #[test]
    fn ast_estimate_enables_parallel_decision() {
        // Simulate: 30 heavy tests (below min_parallel_tests=100),
        // each estimated at 50ms by AST → total 1500ms.
        // With 4 workers and 250ms overhead: 1500 > 1000 → parallel.
        let cache = TestCache::empty();
        let items: Vec<Arc<TestItem>> = (0..30)
            .map(|i| TestItem::builder_raw(&format!("tests/test_heavy.py::test_{i}")).arc())
            .collect();

        // Without AST fallback: None (would trigger count-based fallback)
        assert!(cache.estimated_duration(&items, None).is_none());

        // With AST fallback: 1500ms total
        let est = cache.estimated_duration(&items, Some(1500.0)).unwrap();
        assert_eq!(est.as_millis(), 1500);
        // 1500ms > 250ms × 4 workers (1000ms) → parallel decision
        assert!(est.as_millis() as f64 > 250.0 * 4.0);
    }
}
