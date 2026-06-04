//! Test filtering and grouping.
//!
//! Validates marker names against registered markers, handles `--lf`/`--ff`
//! (last-failed / failed-first) logic, and groups items by source module for
//! parallel dispatch.
//!
//! Marker *names* are collected here; marker *conditions* (e.g. `skip(when=...)`) are
//! evaluated at execution time by `python/oxitest/_bridge/_mark_registry.py`.

use std::sync::Arc;

use camino::Utf8PathBuf;
use indexmap::IndexMap;

use crate::types::{CollectError, TestItem};

// Marker names (not conditions) are collected here at collection time.
// The names populate TestItem::markers and are used for query DSL filtering.
// Marker *conditions* — skip(when=condition), xfail — are evaluated at execution time
// by the mark handler registry in python/oxitest/_bridge/_mark_registry.py (_MARK_REGISTRY).
// Both phases must agree on which names are built-in (BUILTIN_MARKERS below).
const BUILTIN_MARKERS: &[&str] = &["skip", "xfail", "usefixtures", "timeout", "inprocess"];

/// Check that every marker name on every item is either a built-in or registered.
///
/// Returns one [`CollectError`] per unknown marker, each with a hint showing the
/// `[tool.oxitest]` TOML snippet needed to register it. Built-in markers
/// (`skip`, `xfail`, `usefixtures`, `timeout`) are always allowed.
pub fn validate_markers(
    items: &[Arc<TestItem>],
    registered: &std::collections::HashSet<&str>,
) -> Vec<CollectError> {
    items
        .iter()
        .flat_map(|item| {
            item.markers.iter().filter_map(|name| {
                let s = name.as_str();
                if !BUILTIN_MARKERS.contains(&s) && !registered.contains(s) {
                    Some(CollectError::PyError(format!(
                        "unknown marker '{}' on {}\nHint: register it in pyproject.toml:\n  [tool.oxitest]\n  markers = [\"{}: <description>\"]",
                        name, item.node_id, name
                    )))
                } else {
                    None
                }
            })
        })
        .collect()
}

fn partition_by_failed(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> (Vec<Arc<TestItem>>, Vec<Arc<TestItem>>) {
    items
        .into_iter()
        .partition(|item| failed_ids.contains(item.node_id.as_ref()))
}

/// Keep only items whose node_id is in `failed_ids`.
/// Used by `--lf` (last-failed) mode.
#[must_use = "returns filtered items; original is consumed"]
pub fn filter_last_failed(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<Arc<TestItem>> {
    partition_by_failed(items, failed_ids).0
}

/// Move items whose node_id is in `failed_ids` to the front; preserve relative order within each group.
/// Used by `--ff` (failed-first) mode.
#[must_use = "returns reordered items; original is consumed"]
pub fn sort_failed_first(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<Arc<TestItem>> {
    let (mut failed, rest) = partition_by_failed(items, failed_ids);
    failed.extend(rest);
    failed
}

/// Group items by module path, preserving insertion order within each group.
#[must_use = "returns grouped items; original is consumed"]
pub fn group_by_module(items: &[Arc<TestItem>]) -> Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)> {
    let mut groups: IndexMap<Utf8PathBuf, Vec<Arc<TestItem>>> = IndexMap::new();
    for item in items {
        groups
            .entry(item.module_path.clone())
            .or_default()
            .push(Arc::clone(item));
    }
    groups.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::TestItem;
    use camino::Utf8PathBuf;
    use std::collections::HashSet;

    #[test]
    fn test_group_by_module_single_module() {
        let items = vec![
            TestItem::builder("tests/test_mod.py", "test_a").arc(),
            TestItem::builder("tests/test_mod.py", "test_b").arc(),
        ];
        let groups = group_by_module(&items);
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].1.len(), 2);
    }

    #[test]
    fn test_group_by_module_multiple_modules() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_y.py", "test_b").arc(),
            TestItem::builder("tests/test_x.py", "test_c").arc(),
        ];
        let groups = group_by_module(&items);
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[0].1.len(), 2);
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/test_y.py"));
        assert_eq!(groups[1].1.len(), 1);
    }

    #[test]
    fn test_group_by_module_preserves_order() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_y.py", "test_b").arc(),
        ];
        let groups = group_by_module(&items);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/test_y.py"));
    }

    #[test]
    fn test_group_by_module_empty_input() {
        let groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)> = group_by_module(&[]);
        assert!(groups.is_empty());
    }

    #[test]
    fn test_builtin_markers_contains_expected_set() {
        // These names must match either:
        // - a handler in python/oxitest/_bridge/marks.py _MARK_REGISTRY, OR
        // - a Rust-only scheduling mark (like "inprocess") with no Python handler.
        // If you add a MarkHandler subclass in Python, add its mark_name here.
        assert!(
            BUILTIN_MARKERS.contains(&"skip"),
            "'skip' missing — add SkipHandler to marks.py _MARK_REGISTRY"
        );
        assert!(
            BUILTIN_MARKERS.contains(&"xfail"),
            "'xfail' missing — add XFailHandler to marks.py _MARK_REGISTRY"
        );
        assert!(
            BUILTIN_MARKERS.contains(&"usefixtures"),
            "'usefixtures' missing — add UsefixturesHandler to marks.py _MARK_REGISTRY"
        );
        assert!(
            BUILTIN_MARKERS.contains(&"timeout"),
            "'timeout' missing — add TimeoutHandler to marks.py _MARK_REGISTRY"
        );
        assert!(
            BUILTIN_MARKERS.contains(&"inprocess"),
            "'inprocess' missing — scheduling mark for main-process execution"
        );
        let expected: std::collections::HashSet<&str> =
            ["skip", "xfail", "usefixtures", "timeout", "inprocess"]
                .into_iter()
                .collect();
        let actual: std::collections::HashSet<&str> = BUILTIN_MARKERS.iter().copied().collect();
        assert_eq!(
            actual,
            expected,
            "BUILTIN_MARKERS mismatch.\n\
             Extra in BUILTIN_MARKERS:    {:?}\n\
             Missing from BUILTIN_MARKERS: {:?}",
            actual.difference(&expected).collect::<Vec<_>>(),
            expected.difference(&actual).collect::<Vec<_>>(),
        );
    }

    #[test]
    fn test_filter_last_failed_keeps_only_failed_items() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_x.py", "test_b").arc(),
            TestItem::builder("tests/test_y.py", "test_c").arc(),
        ];
        let mut failed: HashSet<String> = HashSet::new();
        failed.insert("tests/test_x.py::test_a".to_string());
        let filtered = filter_last_failed(items, &failed);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_a");
    }

    #[test]
    fn test_filter_last_failed_empty_set_returns_empty() {
        let items = vec![TestItem::builder("tests/test_mod.py", "test_a").arc()];
        let failed: HashSet<String> = HashSet::new();
        let filtered = filter_last_failed(items, &failed);
        assert!(filtered.is_empty());
    }

    #[test]
    fn test_sort_failed_first_moves_failed_to_front() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_x.py", "test_b").arc(),
            TestItem::builder("tests/test_y.py", "test_c").arc(),
        ];
        let mut failed: HashSet<String> = HashSet::new();
        failed.insert("tests/test_x.py::test_b".to_string());
        let sorted = sort_failed_first(items, &failed);
        assert_eq!(sorted[0].fn_name, "test_b");
    }

    #[test]
    fn test_sort_failed_first_no_failures_preserves_order() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_y.py", "test_b").arc(),
        ];
        let failed: HashSet<String> = HashSet::new();
        let sorted = sort_failed_first(items, &failed);
        assert_eq!(sorted[0].fn_name, "test_a");
        assert_eq!(sorted[1].fn_name, "test_b");
    }

    #[test]
    fn module_path_as_str_does_not_require_unwrap() {
        // Utf8PathBuf::as_str() returns &str directly — no Option, no unwrap.
        // This fails to compile if module_path is PathBuf (PathBuf has no as_str()).
        let item: Arc<TestItem> = TestItem::builder("tests/test_mod.py", "test_a").arc();
        let _s: &str = item.module_path.as_str();
    }
}
