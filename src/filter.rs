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

/// Returns true if the string contains glob metacharacters (`*`, `?`, `[`).
pub(crate) fn contains_glob_chars(s: &str) -> bool {
    s.contains('*') || s.contains('?') || s.contains('[')
}

/// Keep only items matching the provided node IDs.
///
/// Uses prefix matching for literal IDs: a node ID without `[` matches itself
/// and any parametrized variants. A node ID like `path::ClassName` matches all
/// methods in that class.
///
/// For glob IDs (containing `*`, `?`, or `[`), uses `globset::GlobMatcher`
/// with `literal_separator(false)` so `*` matches across `::` and `/`.
///
/// Items from files NOT in `node_id_source_files` pass through unfiltered
/// (they came from bare paths, not node IDs).
///
/// Returns all items unchanged if `node_ids` is empty.
#[must_use = "returns filtered items; original is consumed"]
pub fn filter_by_node_ids(
    items: Vec<Arc<TestItem>>,
    node_ids: &[crate::types::NodeId],
    node_id_source_files: &std::collections::HashSet<Utf8PathBuf>,
) -> Vec<Arc<TestItem>> {
    if node_ids.is_empty() {
        return items;
    }

    // Partition into literal and glob node IDs.
    let (glob_ids, literal_ids): (Vec<_>, Vec<_>) = node_ids
        .iter()
        .partition(|id| contains_glob_chars(id.as_ref()));

    // Pre-compile glob matchers.
    // Node IDs use `[param_id]` brackets as structural delimiters, not glob character
    // classes. Escape `[` → `[[]` and `]` → `[]]` in a single pass so globset treats
    // them as literals while preserving `*` and `?` as wildcards.
    let glob_matchers: Vec<globset::GlobMatcher> = glob_ids
        .iter()
        .filter_map(|id| {
            let escaped = escape_node_id_brackets(id.as_ref());
            match globset::GlobBuilder::new(&escaped)
                .literal_separator(false)
                .build()
            {
                Ok(glob) => Some(glob.compile_matcher()),
                Err(e) => {
                    eprintln!("warning: invalid glob pattern '{}': {e}", id.as_ref());
                    None
                }
            }
        })
        .collect();

    items
        .into_iter()
        .filter(|item| {
            // Items from bare-path files (not node ID sources) pass through.
            if !node_id_source_files.is_empty() && !node_id_source_files.contains(&item.module_path)
            {
                return true;
            }
            let item_id: &str = item.node_id.as_ref();

            // Check literal IDs (existing prefix logic).
            let literal_match = literal_ids.iter().any(|target| {
                let t: &str = target.as_ref();
                item_id == t
                    || item_id.starts_with(&format!("{}[", t))
                    || item_id.starts_with(&format!("{}::", t))
            });
            if literal_match {
                return true;
            }

            // Check glob IDs.
            glob_matchers.iter().any(|m| m.is_match(item_id))
        })
        .collect()
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
    fn filter_by_node_ids_exact_match() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo").arc(),
            TestItem::builder("tests/test_a.py", "test_bar").arc(),
            TestItem::builder("tests/test_b.py", "test_baz").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_foo")];
        let source_files = std::collections::HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_foo");
    }

    #[test]
    fn filter_by_node_ids_prefix_matches_parametrized() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case1".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case2".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_bar").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_foo")];
        let source_files = std::collections::HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        assert!(filtered.iter().all(|i| i.fn_name == "test_foo"));
    }

    #[test]
    fn filter_by_node_ids_exact_parametrized() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case1".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case2".to_string())
                .arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw(
            "tests/test_a.py::test_foo[case1]",
        )];
        let source_files = std::collections::HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].param_id, Some("case1".to_string()));
    }

    #[test]
    fn filter_by_node_ids_empty_returns_all() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo").arc(),
            TestItem::builder("tests/test_b.py", "test_bar").arc(),
        ];
        let ids: Vec<crate::types::NodeId> = vec![];
        let source_files = std::collections::HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
    }

    #[test]
    fn filter_by_node_ids_bare_path_items_pass_through() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo").arc(),
            TestItem::builder("tests/test_a.py", "test_bar").arc(),
            TestItem::builder("tests/test_b.py", "test_baz").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_foo")];
        let mut source_files = std::collections::HashSet::new();
        source_files.insert(Utf8PathBuf::from("tests/test_a.py"));
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        let names: Vec<_> = filtered.iter().map(|i| i.fn_name.as_str()).collect();
        assert!(names.contains(&"test_foo"));
        assert!(names.contains(&"test_baz"));
        assert!(!names.contains(&"test_bar"));
    }

    #[test]
    fn filter_by_node_ids_class_prefix_selects_all_methods() {
        let items = vec![
            TestItem::builder("tests/test_cls.py", "TestSuite::test_a").arc(),
            TestItem::builder("tests/test_cls.py", "TestSuite::test_b").arc(),
            TestItem::builder("tests/test_cls.py", "test_standalone").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw(
            "tests/test_cls.py::TestSuite",
        )];
        let source_files = std::collections::HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        assert!(filtered
            .iter()
            .all(|i| i.fn_name.starts_with("TestSuite::")));
    }

    #[test]
    fn module_path_as_str_does_not_require_unwrap() {
        // Utf8PathBuf::as_str() returns &str directly — no Option, no unwrap.
        // This fails to compile if module_path is PathBuf (PathBuf has no as_str()).
        let item: Arc<TestItem> = TestItem::builder("tests/test_mod.py", "test_a").arc();
        let _s: &str = item.module_path.as_str();
    }

    #[test]
    fn filter_by_node_ids_glob_function_name() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_add").arc(),
            TestItem::builder("tests/test_a.py", "test_sub").arc(),
            TestItem::builder("tests/test_a.py", "test_mul").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_a*")];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_add");
    }

    #[test]
    fn filter_by_node_ids_glob_param_id() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_add")
                .param_id("case_basic".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_add")
                .param_id("case_edge".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_add")
                .param_id("other".to_string())
                .arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw(
            "tests/test_a.py::test_add[case*]",
        )];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        assert!(filtered
            .iter()
            .all(|i| i.param_id.as_ref().unwrap().starts_with("case")));
    }

    #[test]
    fn filter_by_node_ids_glob_star_selects_all_in_file() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo").arc(),
            TestItem::builder("tests/test_a.py", "test_bar").arc(),
            TestItem::builder("tests/test_b.py", "test_baz").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::*")];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        assert!(filtered
            .iter()
            .all(|i| i.module_path == Utf8PathBuf::from("tests/test_a.py")));
    }

    #[test]
    fn filter_by_node_ids_glob_no_match_returns_empty() {
        let items = vec![TestItem::builder("tests/test_a.py", "test_foo").arc()];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_zzz*")];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert!(filtered.is_empty());
    }

    #[test]
    fn filter_by_node_ids_non_glob_unchanged() {
        // Verify existing prefix behavior is preserved when no glob chars present
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case1".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_foo")
                .param_id("case2".to_string())
                .arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_a.py::test_foo")];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
    }

    #[test]
    fn filter_by_node_ids_mixed_glob_and_literal() {
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_add").arc(),
            TestItem::builder("tests/test_a.py", "test_sub").arc(),
            TestItem::builder("tests/test_b.py", "test_mul").arc(),
        ];
        let ids = vec![
            crate::types::NodeId::from_raw("tests/test_a.py::test_a*"),
            crate::types::NodeId::from_raw("tests/test_b.py::test_mul"),
        ];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 2);
        let names: Vec<_> = filtered.iter().map(|i| i.fn_name.as_str()).collect();
        assert!(names.contains(&"test_add"));
        assert!(names.contains(&"test_mul"));
    }

    #[test]
    fn filter_by_node_ids_glob_path_segment() {
        let items = vec![
            TestItem::builder("tests/test_math.py", "test_foo").arc(),
            TestItem::builder("tests/test_string.py", "test_foo").arc(),
            TestItem::builder("tests/test_string.py", "test_bar").arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw("tests/test_m*::test_foo")];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 1);
        assert_eq!(
            filtered[0].module_path,
            Utf8PathBuf::from("tests/test_math.py")
        );
    }
}
