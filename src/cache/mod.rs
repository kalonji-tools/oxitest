//! Timing cache and outcome persistence.
//!
//! Manages `.oxitest_cache/timings.json` — stores per-test durations and
//! pass/fail outcomes across runs. Used by the scheduler to sort groups
//! heaviest-first, and by `--lf`/`--ff` to filter or prioritize failed tests.
//!
//! Cache entries age out after `cache_max_age` runs without being refreshed.
//! The file format is versioned ([`CACHE_VERSION`]) to allow future migration.

mod module;
mod outcome;
mod serde;
mod timing;

#[cfg(test)]
mod test_helpers;

pub use module::ModuleCache;
pub use outcome::OutcomeCache;
pub use timing::TimingCache;

use ahash::AHashMap;
use camino::{Utf8Path, Utf8PathBuf};

use crate::types::{LineNo, OutcomeKind};

const CACHE_VERSION: u32 = 1;

/// Timing and outcome record for a single test, stored by node ID.
///
/// `age` counts how many runs have elapsed since this test last executed.
/// Entries with `age > cache_max_age` are pruned during [`TestCache::merge_timings`].
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub(super) struct CacheEntry {
    duration_ms: f64,
    age: u32,
    #[serde(default)]
    last_outcome: Option<OutcomeKind>,
    #[serde(default)]
    flaky_count: u32,
}

/// Serialized representation of a single TestItem (module_path and node_id are derived).
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize, Clone)]
pub(super) struct CachedItemData {
    fn_name: String,
    lineno: LineNo,
    markers: Vec<String>,
    param_id: Option<String>,
    param_values: Vec<(String, String)>,
    #[serde(default)]
    is_async: bool,
    #[serde(default)]
    fixture_names: Vec<String>,
    #[serde(default)]
    fixref_names: Vec<String>,
}

/// Per-module collection cache keyed by file path.
///
/// Stores the list of [`CachedItemData`] items collected from a module, tagged
/// with the file's mtime at collection time. When `mtime_secs` no longer matches
/// the file on disk, the cache entry is stale and Python collection runs again.
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub(super) struct ModuleCacheEntry {
    mtime_secs: u64,
    items: Vec<CachedItemData>,
}

/// On-disk representation of the timing cache, written to `.oxitest_cache/timings.json`.
///
/// The `version` field guards against incompatible format changes — mismatches
/// cause `load()` to silently return an empty cache rather than failing. Both
/// `timings` and `modules` maps are serialized in sorted key order for
/// deterministic output (see [`serde::serialize_sorted`]).
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub(super) struct CacheFile {
    version: u32,
    #[serde(serialize_with = "serde::serialize_sorted")]
    timings: AHashMap<String, CacheEntry>,
    #[serde(default, serialize_with = "serde::serialize_sorted")]
    modules: AHashMap<String, ModuleCacheEntry>,
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
    pub(super) inner: CacheFile,
    pub(super) dirty: bool,
}

impl TestCache {
    fn cache_path(rootdir: &Utf8Path) -> Utf8PathBuf {
        rootdir.join(".oxitest_cache").join("timings.json")
    }

    pub(super) fn empty() -> Self {
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
}

#[cfg(test)]
impl TestCache {
    pub(crate) fn empty_for_test() -> Self {
        Self::empty()
    }
}

#[cfg(test)]
mod tests {
    use assert_fs::prelude::*;

    use super::test_helpers::make_timing;
    use super::timing::TimingCache;
    use super::*;

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
        let timings = vec![make_timing(
            "tests/test_foo.py::test_a",
            77.5,
            OutcomeKind::Passed,
        )];
        let mut cache = TestCache::empty();
        cache.merge_timings(&timings, 50);
        cache.save(utf8_dir);

        let loaded = TestCache::load(utf8_dir);
        assert!(
            (loaded.inner.timings["tests/test_foo.py::test_a"].duration_ms - 77.5).abs() < 0.01
        );
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
    fn cache_json_serialization_is_deterministic() {
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
}
