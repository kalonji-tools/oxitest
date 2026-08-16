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

use ahash::AHashMap;
use camino::{Utf8Path, Utf8PathBuf};

use crate::types::{DurationMs, OutcomeKind};

/// Bumped 2 → 3 by #2067.
///
/// The item cache serves a file's `CollectedItem`s without importing it, so a
/// cache written before a *collection guard* existed keeps serving items the
/// guard would now refuse. mtime does not change on an oxitest upgrade, so the
/// stale entry survives indefinitely. Bumping this discards every existing
/// cache once, which is the only point at which the new guard can see the file.
///
/// Bump this whenever a change makes collection refuse something it previously
/// accepted.
///
/// Bumped 3 → 4 by #2068. A `@oxi.fixture` on a class method is now refused at
/// registration, and registration happens at import. The item cache is gated on
/// `has_fixture_shaped_decorator` (`prescan.rs:957`), which scans **top-level**
/// statements only and sends `ClassDef` to its `_ => return false` arm — so a
/// file whose only fixture decorator sits inside a class stays cache-eligible,
/// and a warm entry written before this change serves its items without ever
/// importing the file. The refusal would then not fire.
///
/// One bump is sufficient rather than widening the prescan predicate: mtime
/// covers the steady state, because adding a method fixture edits the file, and
/// a refused file writes no entry. Only the pre-upgrade entry is unreachable by
/// mtime, and that is exactly what this discards.
///
/// Bumped 4 → 5 by #2145, for a different reason from the three above: the
/// entry's **shape** changed. `mtime_secs: u64` became a [`FileFingerprint`],
/// so an existing entry cannot deserialize. Without the bump `load()` would
/// still return an empty cache — `serde_json::from_str` fails and the `Err` arm
/// already does that — but it would do so by accident of the parse rather than
/// by the version check, and a later field that happens to stay compatible
/// would then be served against the wrong comparison.
///
/// Bumped 5 → 6 by #2169, the second shape change and for the same reason as
/// the first: [`FileFingerprint`] lost a field, so a two-field entry written at
/// version 5 cannot deserialize into a one-field one. The bump is what makes
/// that a cold start by decision rather than by a parse failure.
const CACHE_VERSION: u32 = 6;

/// Timing and outcome record for a single test, stored by node ID.
///
/// `age` counts how many runs have elapsed since this test last executed.
/// Entries with `age > cache_max_age` are pruned during [`TestCache::merge_timings`].
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub struct CacheEntry {
    duration_ms: DurationMs,
    age: u32,
    #[serde(default)]
    last_outcome: Option<OutcomeKind>,
    #[serde(default)]
    flaky_count: u32,
}

/// What the module item cache compares to decide a file is unchanged.
///
/// **The content, not the clock.** The key was the modification time truncated
/// to whole seconds, so an edit inside the same wall-clock second as the
/// previous collection left it unchanged: a new Test Item did not run, the run
/// reported a lower count, and it exited 0 (#2145). Measured on ext4 at 3 of 6
/// attempts with natural timing.
///
/// Nanoseconds were tried first and are **not enough**. On Windows two writes
/// microseconds apart receive the same timestamp — measured byte-identical on
/// CI, `mtime_nanos: 1786875318097772800` on both sides — because the value
/// advances with the system timer tick rather than continuously. A timestamp
/// key is therefore only as fine as the platform's clock, and the platform is
/// not something a test suite gets to choose.
///
/// A content hash has no such dependency, and it costs no extra read: the
/// prescan already reads and parses every test file on every run, before this
/// comparison happens. Only the hashing is new.
///
/// **The guard is the hash, and nothing else.** A source length rode along as a
/// second field until #2169 removed it. It could not be tested: the type is
/// compared and never inspected, so any *consistent* change to that field
/// shifted both sides of the comparison and changed no outcome. A mutant
/// turning it into `len() + 1` SURVIVED, and `* 2` and `= 0` were equally
/// unobservable — it shipped untested by construction rather than by omission.
///
/// Dropping it weakens the collision guard from "equal hash **and** equal
/// length" to "equal hash", and a collision serves one stale item list. That is
/// a real if tiny cost, accepted deliberately: the length was never what made
/// the rate small, 64 bits over one project's test files was.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
pub struct FileFingerprint(u64);

impl FileFingerprint {
    /// Fingerprint a module from the source the prescan already read.
    #[must_use]
    pub fn from_source(source: &str) -> Self {
        Self(fnv1a(source.as_bytes()))
    }

    /// Build a fingerprint from a raw hash. For tests.
    #[cfg(test)]
    #[must_use]
    pub(crate) const fn from_parts(source_hash: u64) -> Self {
        Self(source_hash)
    }
}

/// FNV-1a, written out rather than taken from a hasher.
///
/// This value is **persisted** to `.oxitest_cache/timings.json` and compared
/// against a later run's. `ahash` documents that its output is not stable
/// across versions, and `std::hash::DefaultHasher` is not stable across Rust
/// releases — either one silently changes every key on an upgrade, which reads
/// as a mysterious cold start rather than as a change anyone made. Eight lines
/// that cannot drift are cheaper than a stability promise nobody gave.
fn fnv1a(bytes: &[u8]) -> u64 {
    const OFFSET_BASIS: u64 = 0xcbf2_9ce4_8422_2325;
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut hash = OFFSET_BASIS;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

/// Per-module collection cache keyed by file path.
///
/// Stores the list of [`TestItem`](crate::types::TestItem) collected from a module, tagged
/// with the file's [`FileFingerprint`] at collection time. When the fingerprint no
/// longer matches the file on disk, the cache entry is stale and Python collection
/// runs again.
///
/// `node_id` and `module_path` are reconstructed from the map key on deserialization
/// (they are `#[serde(skip)]` on `TestItem`).
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub struct ModuleCacheEntry {
    fingerprint: FileFingerprint,
    items: Vec<crate::types::TestItem>,
}

/// On-disk representation of the timing cache, written to `.oxitest_cache/timings.json`.
///
/// The `version` field guards against incompatible format changes — mismatches
/// cause `load()` to silently return an empty cache rather than failing. Both
/// `timings` and `modules` maps are serialized in sorted key order for
/// deterministic output (see [`serde::serialize_sorted`]).
#[derive(Debug, ::serde::Serialize, ::serde::Deserialize)]
pub struct CacheFile {
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
    use super::*;

    // ── #2145 ───────────────────────────────────────────────────────────────
    // These three tests read no clock and touch no filesystem, which is the
    // point. The first two forms of this key did both, and the second one
    // failed on Windows CI: two writes microseconds apart received the same
    // timestamp, `mtime_nanos: 1786875318097772800` on each side, because that
    // value advances with the system timer tick. A key derived from the source
    // has no platform in it to vary.

    #[test]
    fn two_sources_of_equal_length_fingerprint_differently() {
        let first = FileFingerprint::from_source("def test_one(): pass\n");
        let second = FileFingerprint::from_source("def test_uno(): pass\n");

        assert_ne!(
            first, second,
            "these two differ by one character and are the same length, so \
             neither a timestamp nor a size separates them. The item cache \
             would serve the old item list and the edited test would not run \
             (#2145)"
        );
    }

    #[test]
    fn adding_a_test_changes_the_fingerprint() {
        let before = FileFingerprint::from_source("def test_one(): pass\n");
        let after = FileFingerprint::from_source("def test_one(): pass\ndef test_two(): pass\n");

        assert_ne!(
            before, after,
            "this is the defect's own shape: a test added to a module must make \
             the cached item list stale, whatever the clock says"
        );
    }

    #[test]
    fn the_same_source_fingerprints_identically() {
        let first = FileFingerprint::from_source("def test_one(): pass\n");
        let second = FileFingerprint::from_source("def test_one(): pass\n");

        assert_eq!(
            first, second,
            "an unchanged file must still hit the cache. A key that never \
             matches passes every staleness test above and silently turns the \
             cache off"
        );
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
            &format!(
                r#"{{"version":{CACHE_VERSION},"timings":{{"tests/test_foo.py::test_a":{{"duration_ms":42.5,"age":0}}}}}}"#
            )
        ).unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::load(utf8_dir);
        assert_eq!(cache.inner.timings.len(), 1);
        assert!(
            (cache.inner.timings["tests/test_foo.py::test_a"]
                .duration_ms
                .as_f64()
                - 42.5)
                .abs()
                < 0.01
        );
    }

    #[test]
    fn save_noop_when_not_dirty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let cache = TestCache::empty(); // dirty = false
        cache.save(utf8_dir);
        assert!(
            !dir.path()
                .join(".oxitest_cache")
                .join("timings.json")
                .exists()
        );
    }

    #[test]
    fn save_writes_file_when_dirty() {
        let dir = assert_fs::TempDir::new().unwrap();
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        let mut cache = TestCache::empty();
        cache.dirty = true;
        cache.save(utf8_dir);
        assert!(
            dir.path()
                .join(".oxitest_cache")
                .join("timings.json")
                .exists()
        );
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
            (loaded.inner.timings["tests/test_foo.py::test_a"]
                .duration_ms
                .as_f64()
                - 77.5)
                .abs()
                < 0.01
        );
    }

    #[test]
    fn load_file_without_last_outcome_parses_as_none() {
        let dir = assert_fs::TempDir::new().unwrap();
        let cache_dir = dir.child(".oxitest_cache");
        cache_dir.create_dir_all().unwrap();
        cache_dir.child("timings.json").write_str(
            &format!(
                r#"{{"version":{CACHE_VERSION},"timings":{{"tests/test_foo.py::test_a":{{"duration_ms":10.0,"age":0}}}}}}"#
            )
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
                duration_ms: DurationMs::new(10.0),
                age: 0,
                last_outcome: None,
                flaky_count: 0,
            },
        );
        timings.insert(
            "a_test".to_string(),
            CacheEntry {
                duration_ms: DurationMs::new(20.0),
                age: 1,
                last_outcome: None,
                flaky_count: 0,
            },
        );
        timings.insert(
            "m_test".to_string(),
            CacheEntry {
                duration_ms: DurationMs::new(30.0),
                age: 2,
                last_outcome: None,
                flaky_count: 0,
            },
        );

        let cache = CacheFile {
            version: CACHE_VERSION,
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
