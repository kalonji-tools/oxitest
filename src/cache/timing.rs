use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use camino::Utf8PathBuf;

use super::{CacheEntry, TestCache};
use crate::types::TestItem;

/// Cache for test timing data (scheduling, timeout suggestions, duration estimates).
pub trait TimingCache {
    #[must_use = "caller must use the duration estimate to decide parallel vs serial"]
    fn estimated_duration(&self, items: &[Arc<TestItem>]) -> Option<Duration>;
    fn suggested_timeout_secs(&self, item: &TestItem, multiplier: f64) -> Option<u64>;
    fn sort_groups(&self, groups: &mut Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>);
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
                total += entry.duration_ms;
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
    fn estimated_duration(&self, items: &[Arc<TestItem>]) -> Option<Duration> {
        if items.is_empty() {
            return None;
        }
        let (total_ms, covered) = self.sum_and_count(items);
        if covered * 2 < items.len() {
            return None;
        }
        Some(Duration::from_millis(total_ms as u64))
    }

    /// Returns a suggested timeout in whole seconds for `item`, scaled by `multiplier`.
    /// Returns `None` if the item has no cached timing (caller should use global timeout).
    /// Result is `ceil(cached_duration_secs * multiplier)`, minimum 1 second.
    fn suggested_timeout_secs(&self, item: &TestItem, multiplier: f64) -> Option<u64> {
        let entry = self.inner.timings.get(item.node_id.as_ref())?;
        let scaled_secs = (entry.duration_ms / 1000.0) * multiplier;
        Some((scaled_secs.ceil() as u64).max(1))
    }

    /// Sort module groups heaviest-first for optimal parallel scheduling.
    ///
    /// Groups with known total duration are sorted by descending sum of cached
    /// durations. Uncached groups fall back to descending item count. Assigning
    /// the heaviest module to the first worker minimises tail latency by ensuring
    /// the longest-running work starts immediately.
    fn sort_groups(&self, groups: &mut Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>) {
        // Pre-compute (duration_sum, item_count) for each group once — O(N*M) total.
        // Avoids re-running module_duration_sum inside the comparator, which would be
        // O(N log N * M) because the comparator fires once per sort comparison.
        #[allow(clippy::type_complexity)]
        let mut keyed: Vec<(Option<f64>, usize, Utf8PathBuf, Vec<Arc<TestItem>>)> =
            std::mem::take(groups)
                .into_iter()
                .map(|(path, items)| {
                    let sum = self.module_duration_sum(&items);
                    let len = items.len();
                    (sum, len, path, items)
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

        *groups = keyed
            .into_iter()
            .map(|(_, _, path, items)| (path, items))
            .collect();
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
                    duration_ms: 0.0,
                    age: 0,
                    last_outcome: None,
                    flaky_count: 0,
                });
            entry.duration_ms = t.duration_ms.as_f64();
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
    use crate::reporter::test_helpers::{make_group, make_item_raw as make_item};
    use crate::types::OutcomeKind;

    #[test]
    fn invalidate_removes_entries_not_in_items() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
        ]);
        let items = vec![make_item("tests/test_foo.py::test_a")];
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
        let items = vec![make_item("tests/test_foo.py::test_a")];
        cache.invalidate(&items);
        assert_eq!(cache.inner.timings.len(), 1);
    }

    #[test]
    fn invalidate_sets_dirty_when_entries_pruned() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_gone", 5.0),
        ]);
        let items = vec![make_item("tests/test_foo.py::test_a")];
        cache.invalidate(&items);
        assert!(cache.dirty);
    }

    #[test]
    fn invalidate_does_not_set_dirty_when_nothing_pruned() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let items = vec![make_item("tests/test_foo.py::test_a")];
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
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/slow.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/fast.py"));
    }

    #[test]
    fn sort_groups_puts_uncached_modules_after_cached() {
        let cache = cache_with_entries(&[("tests/known.py::test_a", 10.0)]);
        let mut groups = vec![
            make_group("tests/unknown.py", &["test_z"]),
            make_group("tests/known.py", &["test_a"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/known.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/unknown.py"));
    }

    #[test]
    fn sort_groups_falls_back_to_count_when_no_cache_data() {
        let cache = TestCache::empty();
        let mut groups = vec![
            make_group("tests/small.py", &["test_a"]),
            make_group("tests/large.py", &["test_x", "test_y", "test_z"]),
        ];
        cache.sort_groups(&mut groups);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/large.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/small.py"));
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
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/slow.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/fast.py"));
    }

    #[test]
    fn estimated_duration_returns_none_on_empty_items() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        assert!(cache.estimated_duration(&[]).is_none());
    }

    #[test]
    fn estimated_duration_returns_none_below_half_coverage() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let items = vec![
            make_item("tests/test_foo.py::test_a"),
            make_item("tests/test_foo.py::test_b"),
            make_item("tests/test_foo.py::test_c"),
        ];
        assert!(cache.estimated_duration(&items).is_none());
    }

    #[test]
    fn estimated_duration_returns_some_at_exactly_half_coverage() {
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let items = vec![
            make_item("tests/test_foo.py::test_a"),
            make_item("tests/test_foo.py::test_b"),
        ];
        assert!(cache.estimated_duration(&items).is_some());
    }

    #[test]
    fn estimated_duration_sums_cached_entries() {
        let cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 100.0),
            ("tests/test_foo.py::test_b", 200.0),
        ]);
        let items = vec![
            make_item("tests/test_foo.py::test_a"),
            make_item("tests/test_foo.py::test_b"),
        ];
        let est = cache.estimated_duration(&items).unwrap();
        assert_eq!(est.as_millis(), 300);
    }

    #[test]
    fn suggested_timeout_secs_returns_none_for_uncached_item() {
        let cache = TestCache::empty();
        let item = make_item("tests/test_foo.py::test_a");
        assert!(cache.suggested_timeout_secs(&item, 3.0).is_none());
    }

    #[test]
    fn suggested_timeout_secs_returns_scaled_duration() {
        // cached: 500ms = 0.5s -> multiplier 3.0 -> 1.5s -> ceil -> 2s
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 500.0)]);
        let item = make_item("tests/test_foo.py::test_a");
        let timeout = cache.suggested_timeout_secs(&item, 3.0).unwrap();
        assert_eq!(timeout, 2); // ceil(0.5 * 3.0) = ceil(1.5) = 2
    }

    #[test]
    fn suggested_timeout_secs_rounds_up() {
        // cached: 100ms = 0.1s -> multiplier 2.0 -> 0.2s -> ceil -> 1s (minimum 1)
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let item = make_item("tests/test_foo.py::test_a");
        let timeout = cache.suggested_timeout_secs(&item, 2.0).unwrap();
        assert_eq!(timeout, 1); // ceil(0.2) = 1
    }

    #[test]
    fn suggested_timeout_secs_exact_seconds() {
        // cached: 2000ms = 2s -> multiplier 3.0 -> 6s exactly
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 2000.0)]);
        let item = make_item("tests/test_foo.py::test_a");
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
        assert!((entry.duration_ms - 42.0).abs() < 0.01);
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
}
