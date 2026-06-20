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

use crate::prescan::PrescanItem;
use crate::scheduler::ModuleGroup;
use crate::types::{CollectError, TestItem};

// Marker names (not conditions) are collected here at collection time.
// The names populate TestItem::markers and are used for query DSL filtering.
// Marker *conditions* — skip(when=condition), xfail — are evaluated at execution time
// by the mark handler registry in python/oxitest/_bridge/_mark_registry.py (_MARK_REGISTRY).
// Both phases must agree on which names are built-in (BUILTIN_MARKERS below).
/// Single source of truth for built-in marker names.
///
/// Must stay in sync with `_BUILTIN_HANDLER_NAMES` in
/// `python/oxitest/_bridge/_mark_registry.py`; enforced by `test_int_marker_sync.py`.
pub(crate) const BUILTIN_MARKERS: &[&str] =
    &["skip", "xfail", "usefixtures", "timeout", "inprocess"];

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
                if !BUILTIN_MARKERS.contains(&name) && !registered.contains(name) {
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

/// Result of splitting items by last-run failure status.
struct FailedPartition {
    failed: Vec<Arc<TestItem>>,
    remaining: Vec<Arc<TestItem>>,
}

fn partition_by_failed(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> FailedPartition {
    let (failed, remaining) = items
        .into_iter()
        .partition(|item| failed_ids.contains(item.node_id.as_ref()));
    FailedPartition { failed, remaining }
}

/// Keep only items whose node_id is in `failed_ids`.
/// Used by `--lf` (last-failed) mode.
#[must_use = "returns filtered items; original is consumed"]
pub fn filter_last_failed(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<Arc<TestItem>> {
    partition_by_failed(items, failed_ids).failed
}

/// Move items whose node_id is in `failed_ids` to the front; preserve relative order within each group.
/// Used by `--ff` (failed-first) mode.
#[must_use = "returns reordered items; original is consumed"]
pub fn sort_failed_first(
    items: Vec<Arc<TestItem>>,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<Arc<TestItem>> {
    let FailedPartition {
        mut failed,
        remaining,
    } = partition_by_failed(items, failed_ids);
    failed.extend(remaining);
    failed
}

/// Returns true if the string contains glob metacharacters (`*`, `?`, `[`).
pub(crate) fn contains_glob_chars(s: &str) -> bool {
    s.contains('*') || s.contains('?') || s.contains('[')
}

/// Pre-compile glob matchers from a slice of glob ID strings.
///
/// Escapes `[`/`]` brackets in each ID so globset treats them as literals
/// (node IDs use brackets as structural delimiters, not character classes),
/// then compiles each into a [`globset::GlobMatcher`].
fn build_glob_matchers(glob_ids: &[impl AsRef<str>]) -> Vec<globset::GlobMatcher> {
    glob_ids
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
        .collect()
}

/// Escape `[` and `]` in a node ID so globset treats them as literals.
///
/// Node IDs use `[param_id]` brackets as structural delimiters, not glob
/// character classes. Replacing `[` → `[[]` and `]` → `[]]` in a single pass
/// preserves `*` and `?` as wildcards while making brackets literal.
fn escape_node_id_brackets(id: &str) -> String {
    let mut out = String::with_capacity(id.len() + 4);
    for c in id.chars() {
        match c {
            '[' => out.push_str("[[]"),
            ']' => out.push_str("[]]"),
            other => out.push(other),
        }
    }
    out
}

/// Check whether a collected item matches any of the given node IDs.
///
/// Returns `true` if the item's node ID matches a literal ID (via prefix
/// matching for parametrize/class) or any of the pre-compiled glob matchers.
/// Items from bare-path files (not in `source_files`) always pass through.
fn item_matches_node_ids(
    item: &TestItem,
    literal_ids: &[&crate::types::NodeId],
    glob_matchers: &[globset::GlobMatcher],
    source_files: &std::collections::HashSet<Utf8PathBuf>,
) -> bool {
    // Items from bare-path files (not node ID sources) pass through.
    if !source_files.is_empty() && !source_files.contains(camino::Utf8Path::new(item.module_path()))
    {
        return true;
    }
    let item_id: &str = item.node_id.as_ref();

    // Check literal IDs (prefix logic).
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
/// Items from files NOT in the source files set pass through unfiltered
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
    let glob_matchers = build_glob_matchers(&glob_ids);

    items
        .into_iter()
        .filter(|item| {
            item_matches_node_ids(item, &literal_ids, &glob_matchers, node_id_source_files)
        })
        .collect()
}

/// Group items by module path, preserving insertion order within each group.
#[must_use = "returns grouped items; original is consumed"]
pub fn group_by_module(items: &[Arc<TestItem>]) -> Vec<ModuleGroup> {
    let mut groups: IndexMap<Utf8PathBuf, Vec<Arc<TestItem>>> = IndexMap::new();
    for item in items {
        groups
            .entry(Utf8PathBuf::from(item.module_path()))
            .or_default()
            .push(Arc::clone(item));
    }
    groups
        .into_iter()
        .map(|(module_path, items)| ModuleGroup::new(module_path, items))
        .collect()
}

// ── Prescan-level filtering ───────────────────────────────────────────────────

/// Build a node ID string from prescan data.
///
/// With a class: `"{file_path}::{class}::{fn_name}"`,
/// without:      `"{file_path}::{fn_name}"`.
fn prescan_node_id(file_path: &str, item: &PrescanItem) -> String {
    if let Some(ref class) = item.class_name {
        format!("{file_path}::{class}::{}", item.fn_name)
    } else {
        format!("{file_path}::{}", item.fn_name)
    }
}

/// Check whether a prescan item matches any of the given node IDs.
///
/// Returns `true` if the item's reconstructed node ID matches a literal ID
/// (via bidirectional prefix matching for parametrize/class) or any of the
/// pre-compiled glob matchers.
fn prescan_item_matches_node_ids(
    item: &PrescanItem,
    file_path: &str,
    literal_ids: &[&String],
    glob_matchers: &[globset::GlobMatcher],
) -> bool {
    let id = prescan_node_id(file_path, item);
    // Check literal node IDs with prefix matching in both directions.
    let literal_match = literal_ids.iter().any(|target| {
        id == **target
            || id.starts_with(&format!("{target}["))
            || id.starts_with(&format!("{target}::"))
            // Target is a parametrized case of this item (e.g., target
            // is "path::test_mul[case]" and prescan id is "path::test_mul")
            || target.starts_with(&format!("{id}["))
            || target.starts_with(&format!("{id}::"))
    });
    if literal_match {
        return true;
    }
    // Check glob node IDs against the prescan ID.
    glob_matchers.iter().any(|m| m.is_match(&id))
}

/// Filter prescan items by node ID prefixes.
///
/// A node ID like `path::test_name` matches items with that fn_name.
/// A node ID like `path::ClassName` matches all methods in that class.
/// A node ID like `path::ClassName::test_name` matches a specific class method.
#[allow(dead_code)]
fn filter_prescan_by_node_ids<'a>(
    items: &'a [PrescanItem],
    file_path: &str,
    node_ids: &[String],
) -> Vec<&'a PrescanItem> {
    if node_ids.is_empty() {
        return items.iter().collect();
    }

    // For prescan filtering, only `*` and `?` trigger glob mode.
    // Brackets in node IDs are structural (parametrize case IDs), not glob chars.
    let has_glob = |s: &str| s.contains('*') || s.contains('?');
    let (glob_ids, literal_ids): (Vec<_>, Vec<_>) = node_ids.iter().partition(|id| has_glob(id));

    // Pre-compile glob matchers for wildcard node IDs.
    let glob_matchers = build_glob_matchers(&glob_ids);

    items
        .iter()
        .filter(|item| prescan_item_matches_node_ids(item, file_path, &literal_ids, &glob_matchers))
        .collect()
}

/// Convert a [`PrescanItem`] to a [`QueryEntry`](crate::query::resource::QueryEntry) for DSL evaluation.
fn prescan_item_to_query_entry(
    item: &PrescanItem,
    file_path: &str,
) -> crate::query::resource::QueryEntry {
    let mut fields = std::collections::HashMap::new();
    fields.insert("name".to_string(), prescan_node_id(file_path, item));
    fields.insert("source".to_string(), file_path.to_string());
    fields.insert(
        "mark".to_string(),
        item.markers
            .iter()
            .map(|m| m.name.as_str())
            .collect::<Vec<_>>()
            .join(","),
    );
    fields.insert("async".to_string(), item.is_async.to_string());
    crate::query::resource::QueryEntry { fields }
}

/// Filter prescan items by a query DSL expression.
///
/// Supports: `name(pattern)`, `mark(name)`, `source(pattern)`, `async()`.
/// On parse error, returns all items (fall through to eager).
#[allow(dead_code)]
fn filter_prescan_by_expression<'a>(
    items: &'a [PrescanItem],
    file_path: &str,
    expression: &str,
) -> Vec<&'a PrescanItem> {
    let tokens = match crate::query::compile::lex(expression) {
        Ok(t) => t,
        Err(_) => return items.iter().collect(),
    };
    let parsed = match crate::query::compile::parse(tokens) {
        Ok(p) => p,
        Err(_) => return items.iter().collect(),
    };
    items
        .iter()
        .filter(|item| {
            let entry = prescan_item_to_query_entry(item, file_path);
            crate::query::eval::eval(&parsed, &entry)
        })
        .collect()
}

/// Filter prescan items by last-failed node IDs.
///
/// Checks exact match and parametrize prefix match.
#[allow(dead_code)]
fn filter_prescan_last_failed<'a>(
    items: &'a [PrescanItem],
    file_path: &str,
    failed_ids: &std::collections::HashSet<String>,
) -> Vec<&'a PrescanItem> {
    items
        .iter()
        .filter(|item| {
            let id = prescan_node_id(file_path, item);
            let prefix = format!("{id}[");
            failed_ids
                .iter()
                .any(|fid| fid == &id || fid.starts_with(&prefix))
        })
        .collect()
}

/// Returns true if any prescan item in the file matches any of the given node IDs.
pub(crate) fn file_matches_node_ids(
    items: &[PrescanItem],
    file_path: &str,
    node_ids: &[String],
) -> bool {
    if node_ids.is_empty() {
        return true;
    }
    let has_glob = |s: &str| s.contains('*') || s.contains('?');
    let (glob_ids, literal_ids): (Vec<_>, Vec<_>) = node_ids.iter().partition(|id| has_glob(id));
    let glob_matchers = build_glob_matchers(&glob_ids);
    items
        .iter()
        .any(|item| prescan_item_matches_node_ids(item, file_path, &literal_ids, &glob_matchers))
}

/// Returns true if any prescan item in the file matches the expression.
///
/// When `module_marks` is provided, each item is temporarily augmented with
/// those marks before evaluation. Treats `None` as an empty mark list.
pub(crate) fn file_matches_expression(
    items: &[PrescanItem],
    file_path: &str,
    expression: &str,
    module_marks: Option<&[String]>,
) -> bool {
    let marks = module_marks.unwrap_or(&[]);
    let tokens = match crate::query::compile::lex(expression) {
        Ok(t) => t,
        Err(_) => return true, // fall through to eager on parse error
    };
    let parsed = match crate::query::compile::parse(tokens) {
        Ok(p) => p,
        Err(_) => return true,
    };
    items.iter().any(|item| {
        let entry = prescan_item_to_query_entry_with_module_marks(item, file_path, marks);
        crate::query::eval::eval(&parsed, &entry)
    })
}

/// Returns true if any prescan item in the file is in the last-failed set.
pub(crate) fn file_matches_last_failed(
    items: &[PrescanItem],
    file_path: &str,
    failed_ids: &std::collections::HashSet<String>,
) -> bool {
    items.iter().any(|item| {
        let id = prescan_node_id(file_path, item);
        let prefix = format!("{id}[");
        failed_ids
            .iter()
            .any(|fid| fid == &id || fid.starts_with(&prefix))
    })
}

/// Like [`prescan_item_to_query_entry`] but augments the mark field with module-level marks.
fn prescan_item_to_query_entry_with_module_marks(
    item: &PrescanItem,
    file_path: &str,
    module_marks: &[String],
) -> crate::query::resource::QueryEntry {
    let mut entry = prescan_item_to_query_entry(item, file_path);
    let existing = entry.fields.get("mark").cloned().unwrap_or_default();
    let item_marks: Vec<&str> = if existing.is_empty() {
        vec![]
    } else {
        existing.split(',').collect()
    };
    let mut all_marks: Vec<&str> = item_marks;
    for m in module_marks {
        if !all_marks.contains(&m.as_str()) {
            all_marks.push(m.as_str());
        }
    }
    entry.fields.insert("mark".to_string(), all_marks.join(","));
    entry
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
        assert_eq!(groups[0].items.len(), 2);
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
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[0].items.len(), 2);
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/test_y.py"));
        assert_eq!(groups[1].items.len(), 1);
    }

    #[test]
    fn test_group_by_module_preserves_order() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_y.py", "test_b").arc(),
        ];
        let groups = group_by_module(&items);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("tests/test_x.py"));
        assert_eq!(groups[1].module_path, Utf8PathBuf::from("tests/test_y.py"));
    }

    #[test]
    fn test_group_by_module_empty_input() {
        let groups = group_by_module(&[]);
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
        assert_eq!(&*filtered[0].fn_name, "test_a");
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
        assert_eq!(&*sorted[0].fn_name, "test_b");
    }

    #[test]
    fn test_sort_failed_first_no_failures_preserves_order() {
        let items = vec![
            TestItem::builder("tests/test_x.py", "test_a").arc(),
            TestItem::builder("tests/test_y.py", "test_b").arc(),
        ];
        let failed: HashSet<String> = HashSet::new();
        let sorted = sort_failed_first(items, &failed);
        assert_eq!(&*sorted[0].fn_name, "test_a");
        assert_eq!(&*sorted[1].fn_name, "test_b");
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
        assert_eq!(&*filtered[0].fn_name, "test_foo");
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
        assert!(filtered.iter().all(|i| &*i.fn_name == "test_foo"));
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
        let names: Vec<_> = filtered.iter().map(|i| &*i.fn_name).collect();
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
        assert!(
            filtered
                .iter()
                .all(|i| i.fn_name.starts_with("TestSuite::"))
        );
    }

    #[test]
    fn module_path_accessor_returns_str() {
        // module_path() derives from node_id — no field, no unwrap.
        let item: Arc<TestItem> = TestItem::builder("tests/test_mod.py", "test_a").arc();
        let _s: &str = item.module_path();
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
        assert_eq!(&*filtered[0].fn_name, "test_add");
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
        assert!(
            filtered
                .iter()
                .all(|i| i.param_id.as_ref().unwrap().starts_with("case"))
        );
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
        assert!(
            filtered
                .iter()
                .all(|i| i.module_path() == "tests/test_a.py")
        );
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
        let names: Vec<_> = filtered.iter().map(|i| &*i.fn_name).collect();
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
        assert_eq!(filtered[0].module_path(), "tests/test_math.py");
    }

    #[test]
    fn filter_by_node_ids_glob_exact_param_with_brackets() {
        // Exercises both `[` and `]` escaping — the pattern contains literal brackets
        // that must not be interpreted as glob character classes.
        let items = vec![
            TestItem::builder("tests/test_a.py", "test_add")
                .param_id("case_basic".to_string())
                .arc(),
            TestItem::builder("tests/test_a.py", "test_add")
                .param_id("case_edge".to_string())
                .arc(),
        ];
        let ids = vec![crate::types::NodeId::from_raw(
            "tests/test_a.py::test_add[case_basic]",
        )];
        let source_files = HashSet::new();
        let filtered = filter_by_node_ids(items, &ids, &source_files);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].param_id.as_deref(), Some("case_basic"));
    }

    // ── Prescan filter helpers ───────────────────────────────────────────────

    use crate::prescan::{PrescanItem, PrescanMarker};

    fn make_item(fn_name: &str) -> PrescanItem {
        PrescanItem {
            fn_name: fn_name.to_string(),
            lineno: crate::types::LineNo::new(1),
            is_async: false,
            markers: vec![],
            param_ids: vec![],
            fixture_params: vec![],
            is_class_method: false,
            class_name: None,
            body_weight: crate::types::DurationMs::ZERO,
        }
    }

    fn make_item_with_marker(fn_name: &str, marker: &str) -> PrescanItem {
        PrescanItem {
            markers: vec![PrescanMarker {
                name: marker.to_string(),
                has_dynamic_args: false,
            }],
            ..make_item(fn_name)
        }
    }

    fn make_item_in_class(fn_name: &str, class: &str) -> PrescanItem {
        PrescanItem {
            is_class_method: true,
            class_name: Some(class.to_string()),
            ..make_item(fn_name)
        }
    }

    #[test]
    fn prescan_node_id_matches_exact_function() {
        let items = vec![make_item("test_foo"), make_item("test_bar")];
        let ids = vec!["tests/test_a.py::test_foo".to_string()];
        let filtered = filter_prescan_by_node_ids(&items, "tests/test_a.py", &ids);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_foo");
    }

    #[test]
    fn prescan_node_id_matches_class_method() {
        let items = vec![
            make_item_in_class("test_one", "TestSuite"),
            make_item_in_class("test_two", "TestSuite"),
            make_item("test_standalone"),
        ];
        let ids = vec!["tests/test_a.py::TestSuite::test_one".to_string()];
        let filtered = filter_prescan_by_node_ids(&items, "tests/test_a.py", &ids);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_one");
    }

    #[test]
    fn prescan_node_id_matches_all_class_methods() {
        let items = vec![
            make_item_in_class("test_one", "TestSuite"),
            make_item_in_class("test_two", "TestSuite"),
            make_item("test_standalone"),
        ];
        let ids = vec!["tests/test_a.py::TestSuite".to_string()];
        let filtered = filter_prescan_by_node_ids(&items, "tests/test_a.py", &ids);
        assert_eq!(filtered.len(), 2);
        assert!(
            filtered
                .iter()
                .all(|i| i.class_name.as_deref() == Some("TestSuite"))
        );
    }

    #[test]
    fn prescan_expression_filter_by_name() {
        let items = vec![make_item("test_foo"), make_item("test_bar")];
        let filtered = filter_prescan_by_expression(&items, "tests/test_a.py", "name(test_foo)");
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_foo");
    }

    #[test]
    fn prescan_expression_filter_by_mark() {
        let items = vec![
            make_item_with_marker("test_slow", "slow"),
            make_item("test_fast"),
        ];
        let filtered = filter_prescan_by_expression(&items, "tests/test_a.py", "mark(slow)");
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_slow");
    }

    #[test]
    fn prescan_expression_filter_by_async() {
        let mut async_item = make_item("test_async_fn");
        async_item.is_async = true;
        let items = vec![async_item, make_item("test_sync_fn")];
        let filtered = filter_prescan_by_expression(&items, "tests/test_a.py", "async()");
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].fn_name, "test_async_fn");
    }

    #[test]
    fn prescan_last_failed_filters_by_cached_ids() {
        let items = vec![
            make_item("test_pass"),
            make_item("test_fail"),
            make_item("test_param"),
        ];
        let mut failed = HashSet::new();
        failed.insert("tests/test_a.py::test_fail".to_string());
        // Also test parametrize prefix matching
        failed.insert("tests/test_a.py::test_param[case1]".to_string());
        let filtered = filter_prescan_last_failed(&items, "tests/test_a.py", &failed);
        assert_eq!(filtered.len(), 2);
        let names: Vec<_> = filtered.iter().map(|i| &*i.fn_name).collect();
        assert!(names.contains(&"test_fail"));
        assert!(names.contains(&"test_param"));
    }

    mod prescan_file_predicates {
        use super::*;

        fn make_prescan_item(fn_name: &str) -> PrescanItem {
            PrescanItem {
                fn_name: fn_name.to_string(),
                lineno: crate::types::LineNo::new(1),
                is_async: false,
                markers: vec![],
                param_ids: vec![],
                fixture_params: vec![],
                is_class_method: false,
                class_name: None,
                body_weight: crate::types::DurationMs::new(1.0),
            }
        }

        // ── file_matches_node_ids ────────────────────────────────────────────

        #[test]
        fn file_matches_node_ids_exact_match() {
            let items = vec![make_prescan_item("test_foo"), make_prescan_item("test_bar")];
            let node_ids = vec!["tests/test_a.py::test_foo".to_string()];
            assert!(file_matches_node_ids(&items, "tests/test_a.py", &node_ids));
        }

        #[test]
        fn file_matches_node_ids_no_match() {
            let items = vec![make_prescan_item("test_foo"), make_prescan_item("test_bar")];
            let node_ids = vec!["tests/test_a.py::test_baz".to_string()];
            assert!(!file_matches_node_ids(&items, "tests/test_a.py", &node_ids));
        }

        // ── file_matches_expression ──────────────────────────────────────────

        #[test]
        fn file_matches_expression_by_mark() {
            let items = vec![
                PrescanItem {
                    markers: vec![PrescanMarker {
                        name: "slow".to_string(),
                        has_dynamic_args: false,
                    }],
                    ..make_prescan_item("test_slow")
                },
                make_prescan_item("test_fast"),
            ];
            assert!(file_matches_expression(
                &items,
                "tests/test_a.py",
                "mark(slow)",
                None
            ));
        }

        #[test]
        fn file_matches_expression_with_module_marks() {
            // Items have no marks of their own; module_marks provides "slow".
            let items = vec![make_prescan_item("test_foo")];
            let module_marks = vec!["slow".to_string()];
            assert!(file_matches_expression(
                &items,
                "tests/test_a.py",
                "mark(slow)",
                Some(&module_marks),
            ));
        }

        #[test]
        fn file_matches_expression_no_match() {
            let items = vec![make_prescan_item("test_foo")];
            assert!(!file_matches_expression(
                &items,
                "tests/test_a.py",
                "mark(slow)",
                None
            ));
        }

        // ── file_matches_last_failed ─────────────────────────────────────────

        #[test]
        fn file_matches_last_failed_hit() {
            let items = vec![
                make_prescan_item("test_pass"),
                make_prescan_item("test_fail"),
            ];
            let mut failed = HashSet::new();
            failed.insert("tests/test_a.py::test_fail".to_string());
            assert!(file_matches_last_failed(&items, "tests/test_a.py", &failed));
        }

        #[test]
        fn file_matches_last_failed_miss() {
            let items = vec![make_prescan_item("test_pass")];
            let mut failed = HashSet::new();
            failed.insert("tests/test_a.py::test_other".to_string());
            assert!(!file_matches_last_failed(
                &items,
                "tests/test_a.py",
                &failed
            ));
        }
    }
}
