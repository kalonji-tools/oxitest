//! Identity and measurement primitives: [`NodeId`], [`DurationMs`], [`LineNo`].

use std::fmt::Write;
use std::sync::Arc;

/// Stable test identifier used throughout the runner.
/// Format: `module_path::fn_name` or `module_path::fn_name[param_id]`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct NodeId(Arc<str>);

impl NodeId {
    pub fn new(module_path: &str, fn_name: &str, param_id: Option<&str>) -> Self {
        let extra = param_id.map_or(0, |id| id.len() + 2); // "[" + id + "]"
        let mut s = String::with_capacity(module_path.len() + 2 + fn_name.len() + extra);
        let _ = write!(s, "{}::{}", module_path, fn_name);
        if let Some(id) = param_id {
            let _ = write!(s, "[{}]", id);
        }
        NodeId(s.into())
    }

    /// Create a NodeId from an already-formatted string (e.g. received from a worker subprocess).
    pub fn from_raw(s: &str) -> Self {
        NodeId(Arc::from(s))
    }

    /// Extract the module path (file path before the first `::`) from a node ID.
    ///
    /// Returns `None` if the node ID contains no `::` separator.
    pub fn module_path(&self) -> Option<&str> {
        self.0.split_once("::").map(|(path, _)| path)
    }
}

impl Default for NodeId {
    fn default() -> Self {
        NodeId(Arc::from(""))
    }
}

impl std::fmt::Display for NodeId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// Exposes the full rendered node-id string (`"module_path::fn_name"` or
/// `"module_path::fn_name[param_id]"`), giving access to all `str` methods.
/// Use `str::contains` to match against the complete identifier including the path.
impl std::ops::Deref for NodeId {
    type Target = str;
    fn deref(&self) -> &str {
        &self.0
    }
}

impl AsRef<str> for NodeId {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl std::borrow::Borrow<str> for NodeId {
    fn borrow(&self) -> &str {
        &self.0
    }
}

/// Duration in milliseconds, used throughout the runner for test timings.
///
/// Wraps `f64` to prevent accidental unit confusion (milliseconds vs seconds).
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd, serde::Serialize, serde::Deserialize)]
pub struct DurationMs(f64);

impl DurationMs {
    pub const ZERO: DurationMs = DurationMs(0.0);

    pub fn new(ms: f64) -> Self {
        DurationMs(ms)
    }

    pub fn as_f64(self) -> f64 {
        self.0
    }
}

impl std::fmt::Display for DurationMs {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:.1}ms", self.0)
    }
}

impl std::ops::Add for DurationMs {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        DurationMs(self.0 + rhs.0)
    }
}

impl std::ops::AddAssign for DurationMs {
    fn add_assign(&mut self, rhs: Self) {
        self.0 += rhs.0;
    }
}

impl std::ops::Sub for DurationMs {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        DurationMs(self.0 - rhs.0)
    }
}

/// Source line number, used throughout the runner for test locations and tracebacks.
///
/// Wraps `usize` to prevent accidental confusion with loop counters or counts.
/// A value of `0` means "unknown / not available" (convention from Python sentinels).
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct LineNo(usize);

impl LineNo {
    pub const ZERO: LineNo = LineNo(0);

    pub fn new(n: usize) -> Self {
        LineNo(n)
    }

    pub fn from_u32(n: u32) -> Self {
        LineNo(n as usize)
    }
}

impl std::ops::Deref for LineNo {
    type Target = usize;
    fn deref(&self) -> &usize {
        &self.0
    }
}

impl std::fmt::Display for LineNo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

/// Split a pytest-node-ID-shaped string into `(file_part, chain_segments)`.
///
/// Format: `file::seg1::seg2::…`. The file part is everything before the first
/// `::`; the chain is the remaining segments split on subsequent `::`.
///
/// **Empty segments are preserved, not rejected.** Callers decide the policy:
/// - The CLI positional parser treats any string with `::` as a node ID and
///   lets downstream validation surface malformed inputs like `"file.py::"`.
/// - The `[tool.oxitest.doctest].scope` deserializer applies strict validation
///   on the chain (rejects empty segments, checks segment count, etc.).
///
/// Shared between CLI positional parsing (`partition_positionals`) and
/// `[tool.oxitest.doctest].scope` entry deserialization.
pub fn split_node_id_str(s: &str) -> (&str, Vec<&str>) {
    let Some((file, rest)) = s.split_once("::") else {
        return (s, Vec::new());
    };
    (file, rest.split("::").collect())
}

#[cfg(test)]
mod node_id_tests {
    use super::*;

    #[test]
    fn test_node_id_new_without_param() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert_eq!(id.to_string(), "tests/test_foo.py::test_add");
    }

    #[test]
    fn test_node_id_new_with_param() {
        let id = NodeId::new("tests/test_foo.py", "test_add", Some("basic"));
        assert_eq!(id.to_string(), "tests/test_foo.py::test_add[basic]");
    }

    #[test]
    fn test_node_id_deref_contains() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert!(id.contains("test_add"));
    }

    #[test]
    fn test_node_id_clone_equals_original() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert_eq!(id, id.clone());
    }

    #[test]
    fn test_node_id_from_raw_preserves_string() {
        let id = NodeId::from_raw("tests/test_foo.py::test_bar[p0]");
        assert_eq!(id.to_string(), "tests/test_foo.py::test_bar[p0]");
    }

    #[test]
    fn test_node_id_module_path_standalone() {
        let id = NodeId::new("tests/test_foo.py", "test_add", None);
        assert_eq!(id.module_path(), Some("tests/test_foo.py"));
    }

    #[test]
    fn test_node_id_module_path_class_method() {
        let id = NodeId::new("tests/test_foo.py", "TestSuite::test_add", None);
        assert_eq!(id.module_path(), Some("tests/test_foo.py"));
    }

    #[test]
    fn test_node_id_module_path_parametrized() {
        let id = NodeId::new("tests/test_foo.py", "test_add", Some("case1"));
        assert_eq!(id.module_path(), Some("tests/test_foo.py"));
    }

    #[test]
    fn test_node_id_module_path_no_separator() {
        let id = NodeId::from_raw("bare_name");
        assert_eq!(id.module_path(), None);
    }

    #[test]
    fn split_node_id_str_bare_path_returns_empty_chain() {
        let (file, chain) = split_node_id_str("path/to/mod.py");
        assert_eq!(
            file, "path/to/mod.py",
            "bare path returns full input as file"
        );
        assert!(chain.is_empty(), "bare path has no chain segments");
    }

    #[test]
    fn split_node_id_str_single_segment_returns_one_chain() {
        let (file, chain) = split_node_id_str("path/to/mod.py::foo");
        assert_eq!(file, "path/to/mod.py", "file part precedes ::");
        assert_eq!(chain, vec!["foo"], "one segment after ::");
    }

    #[test]
    fn split_node_id_str_two_segments_returns_two_chain() {
        let (file, chain) = split_node_id_str("path/to/mod.py::Cls::method");
        assert_eq!(file, "path/to/mod.py", "file part precedes first ::");
        assert_eq!(chain, vec!["Cls", "method"], "two segments after ::");
    }

    #[test]
    fn split_node_id_str_preserves_empty_trailing_segment() {
        let (file, chain) = split_node_id_str("path/to/mod.py::");
        assert_eq!(file, "path/to/mod.py", "file part precedes trailing ::");
        assert_eq!(
            chain,
            vec![""],
            "trailing :: yields one empty chain segment — helper is non-validating; callers apply their own policy",
        );
    }
}

#[cfg(test)]
mod duration_ms_tests {
    use super::*;

    #[test]
    fn test_add() {
        let a = DurationMs::new(10.0);
        let b = DurationMs::new(20.0);
        assert_eq!((a + b).as_f64(), 30.0);
    }

    #[test]
    fn test_display() {
        assert_eq!(format!("{}", DurationMs::new(42.5)), "42.5ms");
    }

    #[test]
    fn test_zero() {
        assert_eq!(DurationMs::ZERO.as_f64(), 0.0);
    }

    #[test]
    fn test_ord() {
        assert!(DurationMs::new(10.0) < DurationMs::new(20.0));
    }

    #[test]
    fn test_add_assign() {
        let mut d = DurationMs::new(10.0);
        d += DurationMs::new(5.0);
        assert_eq!(d.as_f64(), 15.0);
    }

    #[test]
    fn test_sub() {
        let a = DurationMs::new(30.0);
        let b = DurationMs::new(10.0);
        assert_eq!((a - b).as_f64(), 20.0);
    }
}
