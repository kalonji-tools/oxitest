use camino::Utf8PathBuf;
use indexmap::IndexMap;

use crate::types::{CollectError, TestItem};

// Marker names (not conditions) are collected here at collection time.
// The names populate TestItem::markers and are used for -m expression filtering.
// Marker *conditions* — skipif(condition), xfail — are evaluated at execution time
// by the mark handler registry in python/oxitest/_bridge/marks.py (_MARK_REGISTRY).
// Both phases must agree on which names are built-in (BUILTIN_MARKERS below).
const BUILTIN_MARKERS: &[&str] = &["skip", "skipif", "xfail", "usefixtures", "timeout"];

pub fn validate_markers(
    items: &[TestItem],
    registered: &std::collections::HashSet<&str>,
) -> Vec<CollectError> {
    items
        .iter()
        .flat_map(|item| {
            item.markers.iter().filter_map(|name| {
                if !BUILTIN_MARKERS.contains(&name.as_str())
                    && !registered.contains(name.as_str())
                {
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

pub fn filter_items(items: Vec<TestItem>, keyword: Option<&str>) -> Vec<TestItem> {
    items
        .into_iter()
        .filter(|item| {
            // fn_name is always a substring of node_id ("path::fn_name[param]"),
            // so checking node_id alone is sufficient.
            keyword.is_none_or(|kw| item.node_id.contains(kw))
        })
        .collect()
}

/// Keep only items whose node_id is in `failed_ids`.
/// Used by `--lf` (last-failed) mode.
pub fn filter_last_failed(
    items: Vec<TestItem>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<TestItem> {
    items
        .into_iter()
        .filter(|item| failed_ids.contains(item.node_id.as_ref()))
        .collect()
}

/// Move items whose node_id is in `failed_ids` to the front; preserve relative order within each group.
/// Used by `--ff` (failed-first) mode.
pub fn sort_failed_first(
    items: Vec<TestItem>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<TestItem> {
    let (mut failed, rest): (Vec<_>, Vec<_>) = items
        .into_iter()
        .partition(|item| failed_ids.contains(item.node_id.as_ref()));
    failed.extend(rest);
    failed
}

/// Group items by module path, preserving insertion order within each group.
pub fn group_by_module(items: Vec<TestItem>) -> Vec<(Utf8PathBuf, Vec<TestItem>)> {
    let mut groups: IndexMap<Utf8PathBuf, Vec<TestItem>> = IndexMap::new();
    for item in items {
        groups
            .entry(item.module_path.clone())
            .or_default()
            .push(item);
    }
    groups.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::NodeId;
    use camino::Utf8PathBuf;
    use std::collections::HashSet;

    fn make_item(name: &str) -> TestItem {
        TestItem {
            node_id: NodeId::new("tests/test_mod.py", name, None),
            module_path: Utf8PathBuf::from("tests/test_mod.py"),
            fn_name: name.to_string(),
            lineno: 0,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        }
    }

    fn make_item_in(name: &str, module: &str) -> TestItem {
        TestItem {
            node_id: NodeId::new(module, name, None),
            module_path: Utf8PathBuf::from(module),
            fn_name: name.to_string(),
            lineno: 0,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        }
    }

    #[test]
    fn test_filter_items_no_keyword_returns_all() {
        let items = vec![make_item("test_a"), make_item("test_b")];
        let filtered = filter_items(items, None);
        assert_eq!(filtered.len(), 2);
    }

    #[test]
    fn test_filter_items_with_keyword() {
        let items = vec![make_item("test_foo"), make_item("test_bar")];
        let filtered = filter_items(items, Some("foo"));
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_foo");
    }

    #[test]
    fn test_filter_items_no_match_returns_empty() {
        let items = vec![make_item("test_foo")];
        let filtered = filter_items(items, Some("xyz"));
        assert!(filtered.is_empty());
    }

    #[test]
    fn test_filter_items_by_path_component_in_node_id() {
        // node_id = "tests/test_mod.py::test_a" — "test_mod" is in the path
        // but NOT in fn_name ("test_a"). The filter must still match.
        let items = vec![make_item("test_a"), make_item("test_b")];
        let filtered = filter_items(items, Some("test_mod"));
        assert_eq!(
            filtered.len(),
            2,
            "path component in node_id must match keyword filter"
        );
    }

    #[test]
    fn test_group_by_module_single_module() {
        let items = vec![make_item("test_a"), make_item("test_b")];
        let groups = group_by_module(items);
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].1.len(), 2);
    }

    #[test]
    fn test_group_by_module_multiple_modules() {
        let items = vec![
            make_item_in("test_a", "tests/test_x.py"),
            make_item_in("test_b", "tests/test_y.py"),
            make_item_in("test_c", "tests/test_x.py"),
        ];
        let groups = group_by_module(items);
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[0].1.len(), 2);
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/test_y.py"));
        assert_eq!(groups[1].1.len(), 1);
    }

    #[test]
    fn test_group_by_module_preserves_order() {
        let items = vec![
            make_item_in("test_a", "tests/test_x.py"),
            make_item_in("test_b", "tests/test_y.py"),
        ];
        let groups = group_by_module(items);
        assert_eq!(groups[0].0, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[1].0, Utf8PathBuf::from("tests/test_y.py"));
    }

    #[test]
    fn test_group_by_module_empty_input() {
        let groups = group_by_module(vec![]);
        assert!(groups.is_empty());
    }

    #[test]
    fn test_builtin_markers_contains_expected_set() {
        // These names must match the keys of _MARK_REGISTRY in
        // python/oxitest/_bridge/marks.py. If you add a MarkHandler subclass
        // there, add its mark_name here. If you add a name here, add a handler
        // there. Both lists must agree or validate_markers() will emit false
        // "unknown marker" errors.
        assert!(
            BUILTIN_MARKERS.contains(&"skip"),
            "'skip' missing — add SkipHandler to marks.py _MARK_REGISTRY"
        );
        assert!(
            BUILTIN_MARKERS.contains(&"skipif"),
            "'skipif' missing — add SkipIfHandler to marks.py _MARK_REGISTRY"
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
        let expected: std::collections::HashSet<&str> =
            ["skip", "skipif", "xfail", "usefixtures", "timeout"]
                .into_iter()
                .collect();
        let actual: std::collections::HashSet<&str> = BUILTIN_MARKERS.iter().copied().collect();
        assert_eq!(
            actual,
            expected,
            "BUILTIN_MARKERS must exactly match the keys in \
             python/oxitest/_bridge/marks.py _MARK_REGISTRY.\n\
             Extra in BUILTIN_MARKERS:    {:?}\n\
             Missing from BUILTIN_MARKERS: {:?}",
            actual.difference(&expected).collect::<Vec<_>>(),
            expected.difference(&actual).collect::<Vec<_>>(),
        );
    }

    #[test]
    fn test_filter_last_failed_keeps_only_failed_items() {
        let items = vec![
            make_item_in("test_a", "tests/test_x.py"),
            make_item_in("test_b", "tests/test_x.py"),
            make_item_in("test_c", "tests/test_y.py"),
        ];
        let mut failed: HashSet<String> = HashSet::new();
        failed.insert("tests/test_x.py::test_a".to_string());
        let filtered = filter_last_failed(items, &failed);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_a");
    }

    #[test]
    fn test_filter_last_failed_empty_set_returns_empty() {
        let items = vec![make_item("test_a")];
        let failed: HashSet<String> = HashSet::new();
        let filtered = filter_last_failed(items, &failed);
        assert!(filtered.is_empty());
    }

    #[test]
    fn test_sort_failed_first_moves_failed_to_front() {
        let items = vec![
            make_item_in("test_a", "tests/test_x.py"),
            make_item_in("test_b", "tests/test_x.py"),
            make_item_in("test_c", "tests/test_y.py"),
        ];
        let mut failed: HashSet<String> = HashSet::new();
        failed.insert("tests/test_x.py::test_b".to_string());
        let sorted = sort_failed_first(items, &failed);
        assert_eq!(sorted[0].fn_name, "test_b");
    }

    #[test]
    fn test_sort_failed_first_no_failures_preserves_order() {
        let items = vec![
            make_item_in("test_a", "tests/test_x.py"),
            make_item_in("test_b", "tests/test_y.py"),
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
        let item = make_item("test_a");
        let _s: &str = item.module_path.as_str();
    }
}
