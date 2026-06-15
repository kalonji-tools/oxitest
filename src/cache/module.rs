use std::sync::Arc;

use camino::Utf8Path;

use super::{ModuleCacheEntry, TestCache};
use crate::types::{NodeId, TestItem};

/// Cache for mtime-based module collection results.
pub trait ModuleCache {
    fn cached_module_items(
        &self,
        path: &Utf8Path,
        current_mtime_secs: u64,
    ) -> Option<Vec<Arc<TestItem>>>;
    fn update_module_cache(&mut self, path: &Utf8Path, mtime_secs: u64, items: &[Arc<TestItem>]);
    fn invalidate_modules(&mut self);
}

impl ModuleCache for TestCache {
    /// Returns cached TestItems for `path` if the file's mtime matches `current_mtime_secs`.
    /// Returns None on mtime mismatch or unknown path (caller must run Python collection).
    fn cached_module_items(
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
                let mut item = d.clone();
                item.node_id = NodeId::new(key, &item.fn_name, item.param_id.as_deref());
                Arc::new(item)
            })
            .collect();
        Some(items)
    }

    /// Store the collection result for `path` with the given mtime.
    /// Sets dirty = true.
    fn update_module_cache(&mut self, path: &Utf8Path, mtime_secs: u64, items: &[Arc<TestItem>]) {
        let key = path.as_str().to_string();
        let cached_items = items.iter().map(|item| item.as_ref().clone()).collect();
        self.inner.modules.insert(
            key,
            ModuleCacheEntry {
                mtime_secs,
                items: cached_items,
            },
        );
        self.dirty = true;
    }

    /// Remove module cache entries for paths that no longer exist on disk.
    /// Sets dirty = true if any entries were pruned.
    fn invalidate_modules(&mut self) {
        let before = self.inner.modules.len();
        self.inner
            .modules
            .retain(|key, _| Utf8Path::new(key).exists());
        if self.inner.modules.len() != before {
            self.dirty = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use camino::Utf8Path;

    use super::*;
    use crate::types::{LineNo, MarkerSet};

    #[test]
    fn update_and_retrieve_module_cache_roundtrip() {
        let _dir = assert_fs::TempDir::new().unwrap();
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![
            Arc::new(TestItem {
                node_id: NodeId::new("tests/test_foo.py", "test_a", None),

                fn_name: Arc::from("test_a"),
                lineno: LineNo::new(5),
                markers: MarkerSet::from(vec!["slow".to_string()]),
                param_id: None,
                param_values: vec![],
                is_async: false,
                fixture_names: vec![],
                fixref_names: vec![],
            }),
            Arc::new(TestItem {
                node_id: NodeId::new("tests/test_foo.py", "test_b", Some("x0")),

                fn_name: Arc::from("test_b"),
                lineno: LineNo::new(10),
                markers: MarkerSet::new(),
                param_id: Some("x0".to_string()),
                param_values: vec![crate::types::ParamPair {
                    name: "x".to_string(),
                    value: "0".to_string(),
                }],
                is_async: false,
                fixture_names: vec![],
                fixref_names: vec![],
            }),
        ];
        cache.update_module_cache(module_path, 12345, &items);

        let cached = cache.cached_module_items(module_path, 12345).unwrap();
        assert_eq!(cached.len(), 2);
        assert_eq!(&*cached[0].fn_name, "test_a");
        assert_eq!(cached[0].lineno, LineNo::new(5));
        assert_eq!(cached[0].markers.to_vec(), vec!["slow".to_string()]);
        assert_eq!(&*cached[1].fn_name, "test_b");
        assert_eq!(cached[1].param_id, Some("x0".to_string()));
        assert_eq!(
            cached[1].param_values,
            vec![crate::types::ParamPair {
                name: "x".to_string(),
                value: "0".to_string()
            }]
        );
    }

    #[test]
    fn cached_module_items_returns_none_on_mtime_mismatch() {
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![Arc::new(TestItem {
            node_id: NodeId::new("tests/test_foo.py", "test_a", None),
            fn_name: Arc::from("test_a"),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
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
        use assert_fs::prelude::*;

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
        cache.invalidate_modules(); // path_a exists -> not pruned
        assert!(!cache.dirty); // nothing pruned
    }

    #[test]
    fn save_then_load_preserves_module_cache() {
        let dir = assert_fs::TempDir::new().unwrap();
        let mut cache = TestCache::empty();
        let module_path = Utf8Path::new("tests/test_foo.py");
        let items: Vec<Arc<TestItem>> = vec![Arc::new(TestItem {
            node_id: NodeId::new("tests/test_foo.py", "test_a", None),
            fn_name: Arc::from("test_a"),
            lineno: LineNo::new(3),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
        })];
        cache.update_module_cache(module_path, 9999, &items);
        let utf8_dir = Utf8Path::from_path(dir.path()).unwrap();
        cache.save(utf8_dir);

        let loaded = TestCache::load(utf8_dir);
        let cached = loaded.cached_module_items(module_path, 9999).unwrap();
        assert_eq!(&*cached[0].fn_name, "test_a");
        assert_eq!(cached[0].lineno, LineNo::new(3));
    }
}
