//! Timing cache and outcome persistence.
//!
//! Manages `.oxitest_cache/timings.json` — stores per-test durations and
//! pass/fail outcomes across runs. Used by the scheduler to sort groups
//! heaviest-first, and by `--lf`/`--ff` to filter or prioritize failed tests.
//!
//! Cache entries age out after `cache_max_age` runs without being refreshed.
//! The file format is versioned ([`CACHE_VERSION`]) to allow future migration.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use ahash::AHashMap;
use camino::{Utf8Path, Utf8PathBuf};

use crate::types::{NodeId, OutcomeKind, TestItem};

const CACHE_VERSION: u32 = 1;

/// Timing and outcome record for a single test, stored by node ID.
///
/// `age` counts how many runs have elapsed since this test last executed.
/// Entries with `age > cache_max_age` are pruned during [`TestCache::merge_timings`].
#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct CacheEntry {
    duration_ms: f64,
    age: u32,
    #[serde(default)]
    last_outcome: Option<OutcomeKind>,
    #[serde(default)]
    flaky_count: u32,
}

/// Serialized representation of a single TestItem (module_path and node_id are derived).
#[derive(Debug, serde::Serialize, serde::Deserialize, Clone)]
struct CachedItemData {
    fn_name: String,
    lineno: usize,
    markers: Vec<String>,
    param_id: Option<String>,
    param_values: Vec<(String, String)>,
    #[serde(default)]
    is_async: bool,
}

/// Per-module collection cache keyed by file path.
///
/// Stores the list of [`CachedItemData`] items collected from a module, tagged
/// with the file's mtime at collection time. When `mtime_secs` no longer matches
/// the file on disk, the cache entry is stale and Python collection runs again.
#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct ModuleCache {
    mtime_secs: u64,
    items: Vec<CachedItemData>,
}

/// Serialize an `AHashMap` in alphabetically sorted key order.
///
/// `AHashMap` iteration order is non-deterministic. Without sorting, the cache
/// file would diff on every write even when no data changed, breaking VCS workflows
/// and making it harder to audit what actually changed between runs.
fn serialize_sorted<S, V>(map: &AHashMap<String, V>, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
    V: serde::Serialize,
{
    use serde::ser::SerializeMap;
    let mut entries: Vec<(&str, &V)> = map.iter().map(|(k, v)| (k.as_str(), v)).collect();
    entries.sort_unstable_by_key(|(k, _)| *k);
    let mut state = serializer.serialize_map(Some(entries.len()))?;
    for (k, v) in entries {
        state.serialize_entry(k, v)?;
    }
    state.end()
}

/// On-disk representation of the timing cache, written to `.oxitest_cache/timings.json`.
///
/// The `version` field guards against incompatible format changes — mismatches
/// cause `load()` to silently return an empty cache rather than failing. Both
/// `timings` and `modules` maps are serialized in sorted key order for
/// deterministic output (see [`serialize_sorted`]).
#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct CacheFile {
    version: u32,
    #[serde(serialize_with = "serialize_sorted")]
    timings: AHashMap<String, CacheEntry>,
    #[serde(default, serialize_with = "serialize_sorted")]
    modules: AHashMap<String, ModuleCache>,
}

/// Persists per-test timing and outcome data across runs.
///
/// Loaded at startup from `.oxitest_cache/timings.json` and saved after each run
/// if the data changed (`dirty` flag). Used by the scheduler to sort module groups
/// heaviest-first, and by `--lf`/`--ff` to identify previously-failed tests.
///
/// Errors during `load()` are silently swallowed — a missing or corrupt cache file
/// just means a cold start with no timing data.
#[derive(Debug)]
pub struct TestCache {
    inner: CacheFile,
    dirty: bool,
}

impl TestCache {
    fn cache_path(rootdir: &Utf8Path) -> Utf8PathBuf {
        rootdir.join(".oxitest_cache").join("timings.json")
    }

    fn empty() -> Self {
        Self {
            inner: CacheFile {
                version: CACHE_VERSION,
                timings: AHashMap::new(),
                modules: AHashMap::new(),
            },
            dirty: false,
        }
    }

    /// Load the cache from `<rootdir>/.oxitest_cache/timings.json`.
    ///
    /// Returns an empty cache on any error (missing file, parse failure, version
    /// mismatch) so callers never need to handle a `Result`.
    pub fn load(rootdir: &Utf8Path) -> Self {
        let path = Self::cache_path(rootdir);
        let content = match std::fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => return Self::empty(),
        };
        let file: CacheFile = match serde_json::from_str(&content) {
            Ok(f) => f,
            Err(_) => return Self::empty(),
        };
        if file.version != CACHE_VERSION {
            return Self::empty();
        }
        Self {
            inner: file,
            dirty: false,
        }
    }

    /// Remove timing entries whose node IDs are not in the current item list.
    ///
    /// Called after collection to prune stale entries (e.g. deleted or renamed tests).
    /// Sets `dirty = true` if any entries were removed, triggering a cache save.
    pub fn invalidate(&mut self, items: &[Arc<TestItem>]) {
        let live: HashSet<String> = items.iter().map(|item| item.node_id.to_string()).collect();
        let before = self.inner.timings.len();
        self.inner.timings.retain(|key, _| live.contains(key));
        if self.inner.timings.len() != before {
            self.dirty = true;
        }
    }

    /// Remove module cache entries for paths that no longer exist on disk.
    /// Sets dirty = true if any entries were pruned.
    pub fn invalidate_modules(&mut self) {
        let before = self.inner.modules.len();
        self.inner
            .modules
            .retain(|key, _| Utf8Path::new(key).exists());
        if self.inner.modules.len() != before {
            self.dirty = true;
        }
    }

    /// Returns `(total_duration_ms, covered_count)` for items present in the timing cache.
    fn sum_and_count(&self, items: &[Arc<TestItem>]) -> (f64, usize) {
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

    fn module_duration_sum(&self, items: &[Arc<TestItem>]) -> Option<f64> {
        let (total, count) = self.sum_and_count(items);
        if count > 0 {
            Some(total)
        } else {
            None
        }
    }

    /// Sort module groups heaviest-first for optimal parallel scheduling.
    ///
    /// Groups with known total duration are sorted by descending sum of cached
    /// durations. Uncached groups fall back to descending item count. Assigning
    /// the heaviest module to the first worker minimises tail latency by ensuring
    /// the longest-running work starts immediately.
    pub(crate) fn sort_groups(&self, groups: &mut Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>) {
        // Pre-compute (duration_sum, item_count) for each group once — O(N×M) total.
        // Avoids re-running module_duration_sum inside the comparator, which would be
        // O(N log N × M) because the comparator fires once per sort comparison.
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

    #[must_use = "caller must use the duration estimate to decide parallel vs serial"]
    pub fn estimated_duration(&self, items: &[Arc<TestItem>]) -> Option<Duration> {
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
    pub fn suggested_timeout_secs(&self, item: &TestItem, multiplier: f64) -> Option<u64> {
        let entry = self.inner.timings.get(item.node_id.as_ref())?;
        let scaled_secs = (entry.duration_ms / 1000.0) * multiplier;
        Some((scaled_secs.ceil() as u64).max(1))
    }

    #[allow(dead_code)]
    pub fn merge(&mut self, results: &[(NodeId, f64)], max_age: u32) {
        let executed: HashSet<&str> = results.iter().map(|(id, _)| id.as_ref()).collect();

        for (node_id, duration_ms) in results {
            let entry = self
                .inner
                .timings
                .entry(node_id.to_string())
                .or_insert(CacheEntry {
                    duration_ms: 0.0,
                    age: 0,
                    last_outcome: None,
                    flaky_count: 0,
                }); // initial values overwritten below
            entry.duration_ms = *duration_ms;
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

        if !results.is_empty() || self.inner.timings.len() != before {
            self.dirty = true;
        }
    }

    /// Merge test timings directly from `&[TestTiming]`, avoiding intermediate allocations.
    pub fn merge_timings(&mut self, timings: &[crate::types::TestTiming], max_age: u32) {
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

    /// Record outcomes from `&[TestTiming]` directly.
    pub fn record_timing_outcomes(&mut self, timings: &[crate::types::TestTiming]) {
        let mut changed = false;
        for t in timings {
            if let Some(entry) = self.inner.timings.get_mut(t.node_id.as_ref()) {
                entry.last_outcome = Some(t.outcome.clone());
                if t.outcome == OutcomeKind::Flaky {
                    entry.flaky_count = entry.flaky_count.saturating_add(1);
                }
                changed = true;
            }
        }
        if changed {
            self.dirty = true;
        }
    }

    /// Update `last_outcome` for entries that appear in `outcomes`.
    /// Entries not in `outcomes` are unchanged.
    /// Sets dirty = true if any entry was updated.
    #[allow(dead_code)]
    pub fn record_outcomes(&mut self, outcomes: &[(crate::types::NodeId, OutcomeKind)]) {
        let mut changed = false;
        for (node_id, outcome) in outcomes {
            if let Some(entry) = self.inner.timings.get_mut(node_id.as_ref()) {
                entry.last_outcome = Some(outcome.clone());
                changed = true;
            }
        }
        if changed {
            self.dirty = true;
        }
    }

    /// Returns node IDs whose last recorded outcome was a failure
    /// (failed, error, or timeout).
    pub fn last_failed_ids(&self) -> std::collections::HashSet<String> {
        self.inner
            .timings
            .iter()
            .filter(|(_, entry)| {
                entry
                    .last_outcome
                    .as_ref()
                    .is_some_and(OutcomeKind::is_failure)
            })
            .map(|(id, _)| id.clone())
            .collect()
    }

    pub fn save(&self, rootdir: &Utf8Path) {
        if !self.dirty {
            return;
        }
        let dir = rootdir.join(".oxitest_cache");
        if std::fs::create_dir_all(&dir).is_err() {
            return;
        }
        let content = match serde_json::to_string_pretty(&self.inner) {
            Ok(c) => c,
            Err(_) => return,
        };
        let _ = std::fs::write(dir.join("timings.json"), content);
    }

    /// Returns cached TestItems for `path` if the file's mtime matches `current_mtime_secs`.
    /// Returns None on mtime mismatch or unknown path (caller must run Python collection).
    pub fn cached_module_items(
        &self,
        path: &Utf8Path,
        current_mtime_secs: u64,
    ) -> Option<Vec<Arc<TestItem>>> {
        let key = path.as_str();
        let mc = self.inner.modules.get(key)?;
        if mc.mtime_secs != current_mtime_secs {
            return None;
        }
        let items = mc
            .items
            .iter()
            .map(|d| {
                Arc::new(TestItem {
                    node_id: NodeId::new(key, &d.fn_name, d.param_id.as_deref()),
                    module_path: path.to_owned(),
                    fn_name: d.fn_name.clone(),
                    lineno: d.lineno,
                    markers: d.markers.clone(),
                    param_id: d.param_id.clone(),
                    param_values: d.param_values.clone(),
                    is_async: d.is_async,
                })
            })
            .collect();
        Some(items)
    }

    /// Store the collection result for `path` with the given mtime.
    /// Sets dirty = true.
    pub fn update_module_cache(
        &mut self,
        path: &Utf8Path,
        mtime_secs: u64,
        items: &[Arc<TestItem>],
    ) {
        let key = path.as_str().to_string();
        let cached_items = items
            .iter()
            .map(|item| CachedItemData {
                fn_name: item.fn_name.clone(),
                lineno: item.lineno,
                markers: item.markers.clone(),
                param_id: item.param_id.clone(),
                param_values: item.param_values.clone(),
                is_async: item.is_async,
            })
            .collect();
        self.inner.modules.insert(
            key,
            ModuleCache {
                mtime_secs,
                items: cached_items,
            },
        );
        self.dirty = true;
    }
}

#[cfg(test)]
impl TestCache {
    pub(crate) fn empty_for_test() -> Self {
        Self::empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::test_helpers::{make_group, make_item_raw as make_item};
    use assert_fs::prelude::*;

    fn cache_with_entries(entries: &[(&str, f64)]) -> TestCache {
        let timings = entries
            .iter()
            .map(|(id, ms)| {
                (
                    id.to_string(),
                    CacheEntry {
                        duration_ms: *ms,
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

    #[test]
    fn load_missing_file_returns_empty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty);
    }

    #[test]
    fn load_wrong_version_returns_empty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir
            .child("timings.json")
            .write_str(r#"{"version":99,"timings":{"foo::test_a":{"duration_ms":10.0,"age":0}}}"#)
            .unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty);
    }

    #[test]
    fn load_corrupt_json_returns_empty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir
            .child("timings.json")
            .write_str("not json at all")
            .unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty);
    }

    #[test]
    fn load_valid_file_returns_entries() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir.child("timings.json").write_str(
            r#"{"version":1,"timings":{"tests/test_foo.py::test_a":{"duration_ms":42.5,"age":0}}}"#
        ).unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert_eq!(cache.inner.timings.len(), 1);
        assert!((cache.inner.timings["tests/test_foo.py::test_a"].duration_ms - 42.5).abs() < 0.01);
    }

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
        // slow.py has one cached test (50ms) and one uncached (contributes 0ms to sum).
        // fast.py has one cached test (5ms).
        // slow.py sum = 50ms > fast.py sum = 5ms → slow.py first.
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
        // 1 cached out of 3 items = 33% < 50% → None
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
        // 1 cached out of 2 items = 50% → Some
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
    fn merge_updates_duration_and_resets_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        // Manually set age to non-zero
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_a")
            .unwrap()
            .age = 5;
        let results = vec![(NodeId::from_raw("tests/test_foo.py::test_a"), 42.0)];
        cache.merge(&results, 50);
        let entry = &cache.inner.timings["tests/test_foo.py::test_a"];
        assert!((entry.duration_ms - 42.0).abs() < 0.01);
        assert_eq!(entry.age, 0);
    }

    #[test]
    fn merge_increments_age_for_unexecuted_tests() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
        ]);
        let results = vec![(NodeId::from_raw("tests/test_foo.py::test_a"), 10.0)];
        cache.merge(&results, 50);
        assert_eq!(cache.inner.timings["tests/test_foo.py::test_b"].age, 1);
    }

    #[test]
    fn merge_drops_entries_exceeding_max_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        // After merge with no results, age: 50 + 1 = 51 > 50 → dropped
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 50;
        cache.merge(&[], 50);
        assert!(!cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
    }

    #[test]
    fn merge_keeps_entries_at_max_age() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        // After merge, age: 49 + 1 = 50 <= 50 → kept
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 49;
        cache.merge(&[], 50);
        assert!(cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
    }

    #[test]
    fn merge_adds_new_entry_for_first_time_test() {
        let mut cache = TestCache::empty();
        let results = vec![(NodeId::from_raw("tests/test_foo.py::test_new"), 15.0)];
        cache.merge(&results, 50);
        assert!(cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_new"));
        assert_eq!(cache.inner.timings["tests/test_foo.py::test_new"].age, 0);
    }

    #[test]
    fn merge_sets_dirty_when_results_present() {
        let mut cache = TestCache::empty();
        let results = vec![(NodeId::from_raw("tests/test_foo.py::test_a"), 10.0)];
        cache.merge(&results, 50);
        assert!(cache.dirty);
    }

    #[test]
    fn merge_sets_dirty_when_only_entries_are_dropped() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_old", 10.0)]);
        // Set age to max_age so one more merge will drop it (50 + 1 = 51 > 50)
        cache
            .inner
            .timings
            .get_mut("tests/test_foo.py::test_old")
            .unwrap()
            .age = 50;
        // Empty results — no tests ran, but one entry gets dropped due to age
        cache.merge(&[], 50);
        assert!(!cache
            .inner
            .timings
            .contains_key("tests/test_foo.py::test_old"));
        assert!(cache.dirty);
    }

    #[test]
    fn save_noop_when_not_dirty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::empty(); // dirty = false
        cache.save(utf8_dir);
        assert!(!dir
            .path()
            .join(".oxitest_cache")
            .join("timings.json")
            .exists());
    }

    #[test]
    fn save_writes_file_when_dirty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let mut cache = TestCache::empty();
        cache.dirty = true;
        cache.save(utf8_dir);
        assert!(dir
            .path()
            .join(".oxitest_cache")
            .join("timings.json")
            .exists());
    }

    #[test]
    fn save_then_load_round_trips() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let results = vec![(NodeId::from_raw("tests/test_foo.py::test_a"), 77.5)];
        let mut cache = TestCache::empty();
        cache.merge(&results, 50);
        cache.save(utf8_dir);

        let loaded = TestCache::load(utf8_dir);
        assert!(
            (loaded.inner.timings["tests/test_foo.py::test_a"].duration_ms - 77.5).abs() < 0.01
        );
    }

    #[test]
    fn suggested_timeout_secs_returns_none_for_uncached_item() {
        let cache = TestCache::empty();
        let item = make_item("tests/test_foo.py::test_a");
        assert!(cache.suggested_timeout_secs(&item, 3.0).is_none());
    }

    #[test]
    fn suggested_timeout_secs_returns_scaled_duration() {
        // cached: 500ms = 0.5s → multiplier 3.0 → 1.5s → ceil → 2s
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 500.0)]);
        let item = make_item("tests/test_foo.py::test_a");
        let timeout = cache.suggested_timeout_secs(&item, 3.0).unwrap();
        assert_eq!(timeout, 2); // ceil(0.5 * 3.0) = ceil(1.5) = 2
    }

    #[test]
    fn suggested_timeout_secs_rounds_up() {
        // cached: 100ms = 0.1s → multiplier 2.0 → 0.2s → ceil → 1s (minimum 1)
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 100.0)]);
        let item = make_item("tests/test_foo.py::test_a");
        let timeout = cache.suggested_timeout_secs(&item, 2.0).unwrap();
        assert_eq!(timeout, 1); // ceil(0.2) = 1
    }

    #[test]
    fn suggested_timeout_secs_exact_seconds() {
        // cached: 2000ms = 2s → multiplier 3.0 → 6s exactly
        let cache = cache_with_entries(&[("tests/test_foo.py::test_a", 2000.0)]);
        let item = make_item("tests/test_foo.py::test_a");
        let timeout = cache.suggested_timeout_secs(&item, 3.0).unwrap();
        assert_eq!(timeout, 6);
    }

    #[test]
    fn record_outcomes_sets_last_outcome_on_known_entries() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let outcomes = vec![(
            NodeId::from_raw("tests/test_foo.py::test_a"),
            OutcomeKind::Failed,
        )];
        cache.record_outcomes(&outcomes);
        assert_eq!(
            cache.inner.timings["tests/test_foo.py::test_a"].last_outcome,
            Some(OutcomeKind::Failed)
        );
        assert!(cache.dirty);
    }

    #[test]
    fn record_outcomes_ignores_unknown_node_ids() {
        let mut cache = TestCache::empty();
        let outcomes = vec![(
            NodeId::from_raw("tests/test_foo.py::test_unknown"),
            OutcomeKind::Failed,
        )];
        cache.record_outcomes(&outcomes);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty, "unknown node_id must not mark cache as dirty");
    }

    #[test]
    fn last_failed_ids_returns_failed_and_error_and_timeout() {
        let mut cache = cache_with_entries(&[
            ("tests/test_foo.py::test_a", 10.0),
            ("tests/test_foo.py::test_b", 20.0),
            ("tests/test_foo.py::test_c", 30.0),
            ("tests/test_foo.py::test_d", 40.0),
        ]);
        let outcomes = vec![
            (
                NodeId::from_raw("tests/test_foo.py::test_a"),
                OutcomeKind::Failed,
            ),
            (
                NodeId::from_raw("tests/test_foo.py::test_b"),
                OutcomeKind::Passed,
            ),
            (
                NodeId::from_raw("tests/test_foo.py::test_c"),
                OutcomeKind::Error,
            ),
            (
                NodeId::from_raw("tests/test_foo.py::test_d"),
                OutcomeKind::Timeout,
            ),
        ];
        cache.record_outcomes(&outcomes);
        let failed = cache.last_failed_ids();
        assert!(failed.contains("tests/test_foo.py::test_a"));
        assert!(!failed.contains("tests/test_foo.py::test_b"));
        assert!(failed.contains("tests/test_foo.py::test_c"));
        assert!(failed.contains("tests/test_foo.py::test_d"));
    }

    #[test]
    fn last_failed_ids_returns_empty_on_cold_cache() {
        let cache = TestCache::empty();
        assert!(cache.last_failed_ids().is_empty());
    }

    #[test]
    fn record_outcomes_sets_dirty() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let outcomes = vec![(
            NodeId::from_raw("tests/test_foo.py::test_a"),
            OutcomeKind::Passed,
        )];
        cache.record_outcomes(&outcomes);
        assert!(cache.dirty);
    }

    #[test]
    fn load_file_without_last_outcome_parses_as_none() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir.child("timings.json").write_str(
            r#"{"version":1,"timings":{"tests/test_foo.py::test_a":{"duration_ms":10.0,"age":0}}}"#
        ).unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert_eq!(
            cache.inner.timings["tests/test_foo.py::test_a"].last_outcome,
            None
        );
    }

    #[test]
    fn update_and_retrieve_module_cache_roundtrip() {
        let _dir = assert_fs::TempDir::new().unwrap();
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![
            Arc::new(TestItem {
                node_id: NodeId::new("tests/test_foo.py", "test_a", None),
                module_path: Utf8PathBuf::from("tests/test_foo.py"),
                fn_name: "test_a".to_string(),
                lineno: 5,
                markers: vec!["slow".to_string()],
                param_id: None,
                param_values: vec![],
                is_async: false,
            }),
            Arc::new(TestItem {
                node_id: NodeId::new("tests/test_foo.py", "test_b", Some("x0")),
                module_path: Utf8PathBuf::from("tests/test_foo.py"),
                fn_name: "test_b".to_string(),
                lineno: 10,
                markers: vec![],
                param_id: Some("x0".to_string()),
                param_values: vec![("x".to_string(), "0".to_string())],
                is_async: false,
            }),
        ];
        cache.update_module_cache(module_path, 12345, &items);

        let cached = cache.cached_module_items(module_path, 12345).unwrap();
        assert_eq!(cached.len(), 2);
        assert_eq!(cached[0].fn_name, "test_a");
        assert_eq!(cached[0].lineno, 5);
        assert_eq!(cached[0].markers, vec!["slow".to_string()]);
        assert_eq!(cached[1].fn_name, "test_b");
        assert_eq!(cached[1].param_id, Some("x0".to_string()));
        assert_eq!(
            cached[1].param_values,
            vec![("x".to_string(), "0".to_string())]
        );
    }

    #[test]
    fn cached_module_items_returns_none_on_mtime_mismatch() {
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![Arc::new(TestItem {
            node_id: NodeId::new("tests/test_foo.py", "test_a", None),
            module_path: Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_a".to_string(),
            lineno: 1,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        })];
        cache.update_module_cache(module_path, 12345, &items);
        assert!(cache.cached_module_items(module_path, 99999).is_none());
    }

    #[test]
    fn cached_module_items_returns_none_for_unknown_module() {
        let cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_unknown.py");
        assert!(cache.cached_module_items(module_path, 12345).is_none());
    }

    #[test]
    fn update_module_cache_sets_dirty() {
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        cache.update_module_cache(module_path, 1, &[]);
        assert!(cache.dirty);
    }

    #[test]
    fn load_file_without_modules_field_parses_empty_modules() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir.child("timings.json").write_str(
            r#"{"version":1,"timings":{"tests/test_foo.py::test_a":{"duration_ms":10.0,"age":0}}}"#
        ).unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert!(cache.inner.modules.is_empty());
    }

    #[test]
    fn invalidate_modules_removes_nonexistent_paths() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let mut cache = TestCache::empty();
        // path_a exists on disk; path_b does not
        let path_a = utf8_dir.join("test_a.py");
        std::fs::write(&path_a, "").unwrap();
        let path_b = utf8_dir.join("test_b.py"); // never created
        cache.update_module_cache(&path_a, 1, &[]);
        cache.update_module_cache(&path_b, 2, &[]);
        cache.invalidate_modules();
        assert!(cache.inner.modules.contains_key(path_a.as_str()));
        assert!(!cache.inner.modules.contains_key(path_b.as_str()));
        assert!(cache.dirty);
    }

    #[test]
    fn invalidate_modules_sets_dirty_only_when_pruned() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let mut cache = TestCache::empty();
        let path_a = utf8_dir.join("test_a.py");
        std::fs::write(&path_a, "").unwrap();
        cache.update_module_cache(&path_a, 1, &[]);
        cache.dirty = false; // reset
        cache.invalidate_modules(); // path_a exists → not pruned
        assert!(!cache.dirty); // nothing pruned
    }

    #[test]
    fn save_then_load_preserves_module_cache() {
        let dir = assert_fs::TempDir::new().unwrap();
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![Arc::new(TestItem {
            node_id: NodeId::new("tests/test_foo.py", "test_a", None),
            module_path: Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: "test_a".to_string(),
            lineno: 3,
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
        })];
        cache.update_module_cache(module_path, 9999, &items);
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        cache.save(utf8_dir);

        let loaded = TestCache::load(utf8_dir);
        let cached = loaded.cached_module_items(module_path, 9999).unwrap();
        assert_eq!(cached[0].fn_name, "test_a");
        assert_eq!(cached[0].lineno, 3);
    }

    #[test]
    fn cache_json_serialization_is_deterministic() {
        use ahash::AHashMap;

        let mut timings: AHashMap<String, CacheEntry> = AHashMap::new();
        timings.insert(
            "z_test".to_string(),
            CacheEntry {
                duration_ms: 10.0,
                age: 0,
                last_outcome: None,
                flaky_count: 0,
            },
        );
        timings.insert(
            "a_test".to_string(),
            CacheEntry {
                duration_ms: 20.0,
                age: 1,
                last_outcome: None,
                flaky_count: 0,
            },
        );
        timings.insert(
            "m_test".to_string(),
            CacheEntry {
                duration_ms: 30.0,
                age: 2,
                last_outcome: None,
                flaky_count: 0,
            },
        );

        let cache = CacheFile {
            version: 1,
            timings,
            modules: AHashMap::new(),
        };

        let json = serde_json::to_string(&cache).unwrap();

        // Keys must appear in alphabetical order
        let a_pos = json.find("\"a_test\"").unwrap();
        let m_pos = json.find("\"m_test\"").unwrap();
        let z_pos = json.find("\"z_test\"").unwrap();
        assert!(a_pos < m_pos, "a_test should appear before m_test");
        assert!(m_pos < z_pos, "m_test should appear before z_test");
    }

    // ── merge_timings ───────────────────────────────────────────────────

    fn make_timing(node_id: &str, ms: f64, outcome: OutcomeKind) -> crate::types::TestTiming {
        crate::types::TestTiming {
            node_id: NodeId::from_raw(node_id),
            duration_ms: crate::types::DurationMs::new(ms),
            outcome,
        }
    }

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

    // ── record_timing_outcomes ──────────────────────────────────────────

    #[test]
    fn record_timing_outcomes_sets_last_outcome_on_known_entries() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Failed,
        )];
        cache.record_timing_outcomes(&timings);
        assert_eq!(
            cache.inner.timings["tests/test_foo.py::test_a"].last_outcome,
            Some(OutcomeKind::Failed)
        );
        assert!(cache.dirty);
    }

    #[test]
    fn record_timing_outcomes_ignores_unknown_node_ids() {
        let mut cache = TestCache::empty();
        let timings = vec![make_timing(
            "tests/test_foo.py::test_unknown",
            10.0,
            OutcomeKind::Failed,
        )];
        cache.record_timing_outcomes(&timings);
        assert!(cache.inner.timings.is_empty());
        assert!(!cache.dirty);
    }

    #[test]
    fn record_timing_outcomes_sets_dirty() {
        let mut cache = cache_with_entries(&[("tests/test_foo.py::test_a", 10.0)]);
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            10.0,
            OutcomeKind::Passed,
        )];
        cache.record_timing_outcomes(&timings);
        assert!(cache.dirty);
    }

    #[test]
    fn test_flaky_count_increments_on_flaky_outcome() {
        use crate::types::{DurationMs, TestTiming};
        let mut cache = TestCache::empty();
        let timings = vec![TestTiming {
            node_id: NodeId::from_raw("tests/test_a.py::test_x"),
            duration_ms: DurationMs::new(100.0),
            outcome: OutcomeKind::Flaky,
        }];
        cache.merge_timings(&timings, 50);
        cache.record_timing_outcomes(&timings);
        let failed_ids = cache.last_failed_ids();
        assert!(
            !failed_ids.contains("tests/test_a.py::test_x"),
            "flaky should not be in last_failed_ids"
        );
        assert_eq!(
            cache.inner.timings["tests/test_a.py::test_x"].flaky_count, 1,
            "flaky_count should be incremented"
        );
    }
}
