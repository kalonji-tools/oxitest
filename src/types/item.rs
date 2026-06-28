//! Collected test metadata: [`TestItem`], [`MarkerSet`], and supporting types.

use std::collections::HashSet;
use std::sync::Arc;

use super::{LineNo, NodeId};

/// A single parameter name-value pair from `@parametrize`.
///
/// Serializes as a JSON array `[name, value]` for wire/cache compatibility.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(into = "(String, String)", from = "(String, String)")]
pub struct ParamPair {
    pub name: String,
    pub value: String,
}

impl From<(String, String)> for ParamPair {
    fn from((name, value): (String, String)) -> Self {
        Self { name, value }
    }
}

impl From<ParamPair> for (String, String) {
    fn from(p: ParamPair) -> Self {
        (p.name, p.value)
    }
}

/// A local variable captured from a traceback frame.
///
/// Serializes as a JSON array `[name, repr]` for wire compatibility.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(into = "(String, String)", from = "(String, String)")]
pub struct LocalVar {
    pub name: String,
    pub repr: String,
}

impl From<(String, String)> for LocalVar {
    fn from((name, repr): (String, String)) -> Self {
        Self { name, repr }
    }
}

impl From<LocalVar> for (String, String) {
    fn from(v: LocalVar) -> Self {
        (v.name, v.repr)
    }
}

/// Per-field diff for dataclass/object comparison assertions.
///
/// Serializes as a JSON array `[field, left, right]` for wire compatibility.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(into = "(String, String, String)", from = "(String, String, String)")]
pub struct FieldDiff {
    pub field: String,
    pub left: String,
    pub right: String,
}

impl From<(String, String, String)> for FieldDiff {
    fn from((field, left, right): (String, String, String)) -> Self {
        Self { field, left, right }
    }
}

impl From<FieldDiff> for (String, String, String) {
    fn from(d: FieldDiff) -> Self {
        (d.field, d.left, d.right)
    }
}

// Builtin marker bitflags
const MARKER_SKIP: u8 = 1;
const MARKER_XFAIL: u8 = 2;
const MARKER_USEFIXTURES: u8 = 4;
const MARKER_TIMEOUT: u8 = 8;
const MARKER_INPROCESS: u8 = 16;

/// Type-safe marker set with O(1) access for builtin markers.
///
/// Stores the 5 builtin markers as bitflags and custom markers in a HashSet.
/// Replaces `Vec<String>` for marker storage in `TestItem`.
#[derive(Debug, Clone, PartialEq)]
pub struct MarkerSet {
    builtins: u8,
    custom: HashSet<Arc<str>>,
}

#[allow(dead_code)]
impl MarkerSet {
    pub fn new() -> Self {
        Self {
            builtins: 0,
            custom: HashSet::new(),
        }
    }

    pub fn has_skip(&self) -> bool {
        self.builtins & MARKER_SKIP != 0
    }
    pub fn has_xfail(&self) -> bool {
        self.builtins & MARKER_XFAIL != 0
    }
    pub fn has_usefixtures(&self) -> bool {
        self.builtins & MARKER_USEFIXTURES != 0
    }
    pub fn has_timeout(&self) -> bool {
        self.builtins & MARKER_TIMEOUT != 0
    }
    pub fn has_inprocess(&self) -> bool {
        self.builtins & MARKER_INPROCESS != 0
    }

    /// Check if any marker with the given name exists (O(1) for builtins, O(1) avg for custom).
    pub fn has(&self, name: &str) -> bool {
        match name {
            "skip" => self.has_skip(),
            "xfail" => self.has_xfail(),
            "usefixtures" => self.has_usefixtures(),
            "timeout" => self.has_timeout(),
            "inprocess" => self.has_inprocess(),
            _ => self.custom.contains(name),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.builtins == 0 && self.custom.is_empty()
    }

    pub fn len(&self) -> usize {
        self.builtins.count_ones() as usize + self.custom.len()
    }

    /// Iterate all marker names (builtins first, then custom).
    pub fn iter(&self) -> impl Iterator<Item = &str> {
        let builtins = [
            (MARKER_SKIP, "skip"),
            (MARKER_XFAIL, "xfail"),
            (MARKER_USEFIXTURES, "usefixtures"),
            (MARKER_TIMEOUT, "timeout"),
            (MARKER_INPROCESS, "inprocess"),
        ];
        let b = self.builtins;
        builtins
            .into_iter()
            .filter(move |(bit, _)| b & bit != 0)
            .map(|(_, name)| name)
            .chain(self.custom.iter().map(|s| s.as_ref()))
    }

    /// Convert to `Vec<String>` for serialization/wire compat.
    pub fn to_vec(&self) -> Vec<String> {
        self.iter().map(|s| s.to_string()).collect()
    }

    /// Join all marker names with a separator.
    pub fn join(&self, sep: &str) -> String {
        let parts: Vec<&str> = self.iter().collect();
        parts.join(sep)
    }
}

impl Default for MarkerSet {
    fn default() -> Self {
        Self::new()
    }
}

impl From<Vec<String>> for MarkerSet {
    fn from(names: Vec<String>) -> Self {
        let mut set = Self::new();
        for name in names {
            match name.as_str() {
                "skip" => set.builtins |= MARKER_SKIP,
                "xfail" => set.builtins |= MARKER_XFAIL,
                "usefixtures" => set.builtins |= MARKER_USEFIXTURES,
                "timeout" => set.builtins |= MARKER_TIMEOUT,
                "inprocess" => set.builtins |= MARKER_INPROCESS,
                _ => {
                    set.custom.insert(Arc::from(name.as_str()));
                }
            }
        }
        set
    }
}

// Serde: serialize as Vec<String> for cache compatibility
impl serde::Serialize for MarkerSet {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.to_vec().serialize(serializer)
    }
}

impl<'de> serde::Deserialize<'de> for MarkerSet {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let names = Vec::<String>::deserialize(deserializer)?;
        Ok(Self::from(names))
    }
}

/// A collected test with all metadata needed for execution and reporting.
///
/// Produced by `bridge::collect_module` after Python imports the test file.
/// Fields are `pub(crate)` — external code interacts with tests via [`NodeId`].
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TestItem {
    #[serde(skip, default)]
    pub(crate) node_id: NodeId,
    pub(crate) fn_name: Arc<str>,
    pub(crate) lineno: LineNo,
    pub(crate) markers: MarkerSet,
    pub(crate) param_id: Option<String>,
    pub(crate) param_values: Vec<ParamPair>,
    #[serde(default)]
    pub(crate) is_async: bool,
    #[serde(default)]
    pub(crate) fixture_deps: Vec<(String, String)>,
    #[serde(default)]
    pub(crate) fixref_deps: Vec<(String, String)>,
}

impl TestItem {
    /// Derive the module path from the node_id.
    ///
    /// The node_id format is `"module_path::fn_name"` or
    /// `"module_path::fn_name[param_id]"`. This extracts the prefix before `::`.
    pub(crate) fn module_path(&self) -> &str {
        self.node_id.module_path().unwrap_or("")
    }
}

#[cfg(test)]
use super::test_support::TestItemBuilder;
#[cfg(test)]
use camino::Utf8PathBuf;

#[cfg(test)]
impl TestItem {
    /// Create a builder with a module path and function name.
    /// `node_id` is auto-computed as `"{module}::{fn_name}"` (with `[param_id]` if set).
    pub(crate) fn builder(module_path: &str, fn_name: &str) -> TestItemBuilder {
        TestItemBuilder {
            node_id: None,
            module_path: Utf8PathBuf::from(module_path),
            fn_name: fn_name.to_string(),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
        }
    }

    /// Create a builder from a pre-formatted node_id string.
    /// `module_path` defaults to `"tests/test_foo.py"` and `fn_name` to the full node_id.
    pub(crate) fn builder_raw(node_id: &str) -> TestItemBuilder {
        TestItemBuilder {
            node_id: Some(NodeId::from_raw(node_id)),
            module_path: Utf8PathBuf::from("tests/test_foo.py"),
            fn_name: node_id.to_string(),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
        }
    }
}

#[cfg(test)]
mod item_tests {
    use super::*;

    #[test]
    fn test_item_has_param_fields() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_add", Some("basic")),
            fn_name: Arc::from("test_add"),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: Some("basic".to_string()),
            param_values: vec![ParamPair {
                name: "x".to_string(),
                value: "1".to_string(),
            }],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
        };
        assert_eq!(item.param_id, Some("basic".to_string()));
        assert_eq!(item.param_values.len(), 1);
    }

    #[test]
    fn test_item_non_parametrize_has_none_param_id() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_foo", None),
            fn_name: Arc::from("test_foo"),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
        };
        assert!(item.param_id.is_none());
        assert!(item.param_values.is_empty());
    }

    #[test]
    fn test_item_has_is_async_field() {
        let sync_item = TestItem {
            node_id: NodeId::new("test.py", "test_sync", None),
            fn_name: Arc::from("test_sync"),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_deps: vec![],
            fixref_deps: vec![],
        };
        assert!(!sync_item.is_async);

        let async_item = TestItem {
            node_id: NodeId::new("test.py", "test_async", None),
            fn_name: Arc::from("test_async"),
            lineno: LineNo::new(1),
            markers: MarkerSet::new(),
            param_id: None,
            param_values: vec![],
            is_async: true,
            fixture_deps: vec![],
            fixref_deps: vec![],
        };
        assert!(async_item.is_async);
    }

    #[test]
    fn builder_defaults() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").build();
        assert_eq!(item.node_id.to_string(), "tests/test_foo.py::test_add");
        assert_eq!(item.module_path(), "tests/test_foo.py");
        assert_eq!(&*item.fn_name, "test_add");
        assert_eq!(item.lineno, LineNo::new(1));
        assert!(item.markers.is_empty());
        assert!(item.param_id.is_none());
        assert!(item.param_values.is_empty());
        assert!(!item.is_async);
        assert!(item.fixture_deps.is_empty());
    }

    #[test]
    fn builder_with_overrides() {
        let item = TestItem::builder("tests/test_foo.py", "test_add")
            .lineno(42)
            .markers(vec!["slow".to_string()])
            .async_fn(true)
            .fixture_deps(vec![("db".to_string(), "MyDB".to_string())])
            .fixref_deps(vec![("backend".to_string(), "Backend".to_string())])
            .build();
        assert_eq!(item.lineno, LineNo::new(42));
        assert_eq!(item.markers.to_vec(), vec!["slow"]);
        assert!(item.is_async);
        assert_eq!(
            item.fixture_deps,
            vec![("db".to_string(), "MyDB".to_string())]
        );
        assert_eq!(
            item.fixref_deps,
            vec![("backend".to_string(), "Backend".to_string())]
        );
    }

    #[test]
    fn builder_raw_node_id() {
        let item = TestItem::builder_raw("tests/test_foo.py::test_fn").build();
        assert_eq!(item.node_id.to_string(), "tests/test_foo.py::test_fn");
        assert_eq!(&*item.fn_name, "tests/test_foo.py::test_fn");
    }

    #[test]
    fn builder_with_param_id() {
        let item = TestItem::builder("tests/test_foo.py", "test_add")
            .param_id("case0".to_string())
            .build();
        assert_eq!(
            item.node_id.to_string(),
            "tests/test_foo.py::test_add[case0]"
        );
        assert_eq!(item.param_id, Some("case0".to_string()));
    }

    #[test]
    fn builder_arc_returns_arc_wrapped_item() {
        let item: Arc<TestItem> = TestItem::builder("tests/test_foo.py", "test_add").arc();
        assert_eq!(&*item.fn_name, "test_add");
    }

    #[test]
    fn builder_defaults_lineno_to_one() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").build();
        assert_eq!(item.lineno, LineNo::new(1));
    }

    #[test]
    fn builder_raw_defaults_lineno_to_one() {
        let item = TestItem::builder_raw("tests/test_foo.py::test_add").build();
        assert_eq!(item.lineno, LineNo::new(1));
    }
}

#[cfg(test)]
mod marker_set_tests {
    use super::*;

    /// Builtins are stored as bitflags and custom markers in HashSet — both
    /// must be correctly partitioned from a single input Vec.
    #[test]
    fn from_vec_partitions_builtins_and_custom() {
        let set = MarkerSet::from(vec![
            "skip".to_string(),
            "timeout".to_string(),
            "slow".to_string(),
            "integration".to_string(),
        ]);
        // Builtins detected
        assert!(set.has_skip());
        assert!(set.has_timeout());
        // Non-builtins NOT in bitflags
        assert!(!set.has_xfail());
        assert!(!set.has_inprocess());
        // Custom markers stored
        assert!(set.has("slow"));
        assert!(set.has("integration"));
        // Total count correct
        assert_eq!(set.len(), 4);
    }

    /// The `has()` method must route to bitflags for builtins and HashSet for custom,
    /// returning false for markers not present in either.
    #[test]
    fn has_returns_false_for_absent_markers() {
        let set = MarkerSet::from(vec!["xfail".to_string()]);
        assert!(set.has("xfail"));
        assert!(!set.has("skip"));
        assert!(!set.has("nonexistent"));
    }

    /// Serde roundtrip must preserve all markers — this is the cache compatibility contract.
    /// If this breaks, existing `.oxitest_cache/timings.json` files become unreadable.
    #[test]
    fn serde_roundtrip_preserves_all_markers() {
        let original = MarkerSet::from(vec![
            "skip".to_string(),
            "inprocess".to_string(),
            "custom_mark".to_string(),
        ]);
        let json = serde_json::to_string(&original).unwrap();
        let deserialized: MarkerSet = serde_json::from_str(&json).unwrap();
        // Must contain same markers regardless of ordering
        assert_eq!(original.len(), deserialized.len());
        assert!(deserialized.has_skip());
        assert!(deserialized.has_inprocess());
        assert!(deserialized.has("custom_mark"));
    }

    /// Serde format must be a JSON array of strings — this is the wire/cache format contract.
    #[test]
    fn serde_format_is_string_array() {
        let set = MarkerSet::from(vec!["timeout".to_string()]);
        let json = serde_json::to_string(&set).unwrap();
        // Must deserialize as a plain JSON array, not an object
        let raw: Vec<String> = serde_json::from_str(&json).unwrap();
        assert_eq!(raw, vec!["timeout"]);
    }

    /// iter() is consumed by filter.rs for marker validation — it must yield all markers
    /// and builtins must come first (before custom) for deterministic output.
    #[test]
    fn iter_yields_builtins_before_custom() {
        let set = MarkerSet::from(vec![
            "slow".to_string(),
            "skip".to_string(),
            "xfail".to_string(),
        ]);
        let names: Vec<&str> = set.iter().collect();
        // Builtins (skip, xfail) must appear before custom (slow)
        let skip_pos = names.iter().position(|&n| n == "skip").unwrap();
        let xfail_pos = names.iter().position(|&n| n == "xfail").unwrap();
        let slow_pos = names.iter().position(|&n| n == "slow").unwrap();
        assert!(skip_pos < slow_pos);
        assert!(xfail_pos < slow_pos);
    }

    /// Duplicate builtins in input must not inflate the count — bitflags are idempotent.
    #[test]
    fn duplicate_builtins_are_deduplicated() {
        let set = MarkerSet::from(vec![
            "skip".to_string(),
            "skip".to_string(),
            "skip".to_string(),
        ]);
        assert_eq!(set.len(), 1);
        assert!(set.has_skip());
    }

    /// Empty marker set is the default for most tests — must behave correctly.
    #[test]
    fn empty_set_has_nothing() {
        let set = MarkerSet::new();
        assert!(set.is_empty());
        assert_eq!(set.len(), 0);
        assert!(!set.has("skip"));
        assert!(!set.has("anything"));
        assert_eq!(set.join(","), "");
        assert_eq!(set.to_vec(), Vec::<String>::new());
    }
}

#[cfg(test)]
mod wire_format_tests {
    use super::*;

    /// ParamPair serializes as `[name, value]` — the Python bridge reads this format
    /// from the wire and cache. Breaking this breaks parametrized test collection.
    #[test]
    fn param_pair_serde_format_is_two_element_array() {
        let pair = ParamPair {
            name: "x".to_string(),
            value: "1".to_string(),
        };
        let json = serde_json::to_string(&pair).expect("ParamPair must be serializable to JSON");
        assert_eq!(
            json, r#"["x","1"]"#,
            "ParamPair wire format must be a two-element JSON array [name, value]"
        );
    }

    /// ParamPair round-trip: serialize then deserialize must yield the original value.
    #[test]
    fn param_pair_serde_roundtrip() {
        let original = ParamPair {
            name: "x".to_string(),
            value: "1".to_string(),
        };
        let json = serde_json::to_string(&original).expect("ParamPair must be serializable");
        let deserialized: ParamPair = serde_json::from_str(&json)
            .expect("ParamPair must be deserializable from its own serialized form");
        assert_eq!(
            deserialized, original,
            "ParamPair serde round-trip must preserve name and value"
        );
    }

    /// LocalVar serializes as `[name, repr]` — the Python bridge sends locals in
    /// traceback frames using this format. Breaking this breaks failure diagnostics.
    #[test]
    fn local_var_serde_format_is_two_element_array() {
        let var = LocalVar {
            name: "result".to_string(),
            repr: "42".to_string(),
        };
        let json = serde_json::to_string(&var).expect("LocalVar must be serializable to JSON");
        assert_eq!(
            json, r#"["result","42"]"#,
            "LocalVar wire format must be a two-element JSON array [name, repr]"
        );
    }

    /// LocalVar round-trip: serialize then deserialize must yield the original value.
    #[test]
    fn local_var_serde_roundtrip() {
        let original = LocalVar {
            name: "result".to_string(),
            repr: "42".to_string(),
        };
        let json = serde_json::to_string(&original).expect("LocalVar must be serializable");
        let deserialized: LocalVar = serde_json::from_str(&json)
            .expect("LocalVar must be deserializable from its own serialized form");
        assert_eq!(
            deserialized, original,
            "LocalVar serde round-trip must preserve name and repr"
        );
    }

    /// FieldDiff serializes as `[field, left, right]` — the Python bridge sends
    /// per-field assertion diffs in this format. Breaking this breaks dataclass
    /// comparison reporting.
    #[test]
    fn field_diff_serde_format_is_three_element_array() {
        let diff = FieldDiff {
            field: "email".to_string(),
            left: "a@b".to_string(),
            right: "c@d".to_string(),
        };
        let json = serde_json::to_string(&diff).expect("FieldDiff must be serializable to JSON");
        assert_eq!(
            json, r#"["email","a@b","c@d"]"#,
            "FieldDiff wire format must be a three-element JSON array [field, left, right]"
        );
    }

    /// FieldDiff round-trip: serialize then deserialize must yield the original value.
    #[test]
    fn field_diff_serde_roundtrip() {
        let original = FieldDiff {
            field: "email".to_string(),
            left: "a@b".to_string(),
            right: "c@d".to_string(),
        };
        let json = serde_json::to_string(&original).expect("FieldDiff must be serializable");
        let deserialized: FieldDiff = serde_json::from_str(&json)
            .expect("FieldDiff must be deserializable from its own serialized form");
        assert_eq!(
            deserialized, original,
            "FieldDiff serde round-trip must preserve field, left, and right"
        );
    }
}
