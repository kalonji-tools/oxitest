//! Core data types shared across the runner.
//!
//! Defines [`NodeId`] (stable test identifier), [`TestItem`] (collected test metadata),
//! [`TestOutcome`] (the eight possible results of running a test), [`CollectError`],
//! and [`TestTiming`].

use std::fmt::Write;
use std::sync::Arc;

use camino::Utf8PathBuf;

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

    #[allow(dead_code)]
    pub fn as_usize(self) -> usize {
        self.0
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

/// A single parameter name-value pair from `@parametrize`.
///
/// Serializes as a JSON array `[name, value]` for wire/cache compatibility.
#[derive(Debug, Clone, PartialEq)]
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

impl serde::Serialize for ParamPair {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        (&self.name, &self.value).serialize(serializer)
    }
}

impl<'de> serde::Deserialize<'de> for ParamPair {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let (name, value) = <(String, String)>::deserialize(deserializer)?;
        Ok(Self { name, value })
    }
}

/// A local variable captured from a traceback frame.
///
/// Serializes as a JSON array `[name, repr]` for wire compatibility.
#[derive(Debug, Clone, PartialEq)]
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
    fn from(l: LocalVar) -> Self {
        (l.name, l.repr)
    }
}

impl serde::Serialize for LocalVar {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        (&self.name, &self.repr).serialize(serializer)
    }
}

impl<'de> serde::Deserialize<'de> for LocalVar {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let (name, repr) = <(String, String)>::deserialize(deserializer)?;
        Ok(Self { name, repr })
    }
}

/// Per-field diff for dataclass/object comparison assertions.
///
/// Serializes as a JSON array `[field, left, right]` for wire compatibility.
#[derive(Debug, Clone, PartialEq)]
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

impl serde::Serialize for FieldDiff {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        (&self.field, &self.left, &self.right).serialize(serializer)
    }
}

impl<'de> serde::Deserialize<'de> for FieldDiff {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let (field, left, right) = <(String, String, String)>::deserialize(deserializer)?;
        Ok(Self { field, left, right })
    }
}

/// Process exit code with named variants for each documented exit status.
///
/// - `Success` (0) — all tests passed (or were skipped / xfailed).
/// - `Failure` (1) — at least one hard failure.
/// - `Interrupted` (2) — the run was interrupted (e.g. Ctrl-C).
/// - `CollectError` (3) — one or more collection errors.
/// - `UsageError` (4) — invalid CLI arguments or conflicting flags.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ExitCode {
    Success,
    Failure,
    Interrupted,
    CollectError,
    UsageError,
}

impl ExitCode {
    pub fn as_i32(self) -> i32 {
        match self {
            ExitCode::Success => 0,
            ExitCode::Failure => 1,
            ExitCode::Interrupted => 2,
            ExitCode::CollectError => 3,
            ExitCode::UsageError => 4,
        }
    }
}

impl From<ExitCode> for i32 {
    fn from(code: ExitCode) -> i32 {
        code.as_i32()
    }
}

impl std::fmt::Display for ExitCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_i32())
    }
}

/// A collected test with all metadata needed for execution and reporting.
///
/// Produced by `bridge::collect_module` after Python imports the test file.
/// Fields are `pub(crate)` — external code interacts with tests via [`NodeId`].
#[derive(Debug, Clone)]
pub struct TestItem {
    pub(crate) node_id: NodeId,
    pub(crate) module_path: Utf8PathBuf,
    pub(crate) fn_name: String,
    pub(crate) lineno: LineNo,
    pub(crate) markers: Vec<String>,
    pub(crate) param_id: Option<String>,
    pub(crate) param_values: Vec<ParamPair>,
    pub(crate) is_async: bool,
    pub(crate) fixture_names: Vec<String>,
    pub(crate) fixref_names: Vec<String>,
}

#[cfg(test)]
pub(crate) mod test_support;

#[cfg(test)]
use test_support::TestItemBuilder;

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
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
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
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
        }
    }
}

#[cfg(test)]
use test_support::{ErrorOutcomeBuilder, FailedOutcomeBuilder};

#[cfg(test)]
#[allow(dead_code)]
impl TestOutcome {
    pub(crate) fn failed(msg: &str) -> FailedOutcomeBuilder {
        FailedOutcomeBuilder {
            message: msg.to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
            field_diffs: vec![],
        }
    }
    pub(crate) fn error(msg: &str) -> ErrorOutcomeBuilder {
        ErrorOutcomeBuilder {
            message: msg.to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(1),
            source_line: String::new(),
            frames: vec![],
        }
    }
}

/// Single traceback frame from a test failure or error.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub file: Utf8PathBuf,
    pub lineno: LineNo,
    pub name: String,
    pub line: String,
    pub locals: Vec<LocalVar>,
}

/// Structured diagnostic payload for Failed and Error outcomes.
///
/// Comparison fields (`left`, `right`, `op`, `field_diffs`) are populated
/// for assertion failures and empty for unhandled errors.
#[derive(Debug, Clone)]
pub struct FailureDiagnostic {
    pub message: String,
    pub file: Utf8PathBuf,
    pub lineno: LineNo,
    pub source_line: String,
    pub frames: Vec<Frame>,
    /// Left operand of the comparison (empty for Error outcomes).
    pub left: String,
    /// Right operand of the comparison (empty for Error outcomes).
    pub right: String,
    /// Comparison operator (empty for Error outcomes).
    pub op: String,
    /// Per-field diffs for dataclass/object comparisons (empty for Error outcomes).
    pub field_diffs: Vec<FieldDiff>,
}

impl FailureDiagnostic {
    /// Create an error diagnostic (no comparison fields).
    pub fn error(
        message: String,
        file: Utf8PathBuf,
        lineno: LineNo,
        source_line: String,
        frames: Vec<Frame>,
    ) -> Self {
        Self {
            message,
            file,
            lineno,
            source_line,
            frames,
            left: String::new(),
            right: String::new(),
            op: String::new(),
            field_diffs: vec![],
        }
    }

    /// Sentinel for worker crashes and timeouts (no location data).
    pub fn sentinel(message: String) -> Self {
        Self::error(
            message,
            Utf8PathBuf::default(),
            LineNo::ZERO,
            String::new(),
            vec![],
        )
    }
}

/// The eight possible results of running a single test.
///
/// `Failed` and `Error` variants hold a boxed [`FailureDiagnostic`] to keep the
/// enum small (~32 bytes instead of ~200). Use [`TestOutcome::diagnostic()`] to
/// access the shared payload. [`TestOutcome::is_hard_failure`] determines whether
/// the run exits with code 1.
#[derive(Debug, Clone)]
pub enum TestOutcome {
    Passed {
        no_message_lines: Vec<usize>,
    },
    Failed(Box<FailureDiagnostic>),
    Error(Box<FailureDiagnostic>),
    Skipped {
        reason: String,
    },
    Warned {
        reason: String,
        no_message_lines: Vec<usize>,
    },
    XFailed {
        reason: String,
    },
    XPassed {
        strict: bool,
    },
    Timeout {
        message: String,
    },
    Flaky {
        message: String,
    },
}

impl TestOutcome {
    /// True for outcomes that increment the failure counter and produce exit code 1.
    pub fn is_hard_failure(&self) -> bool {
        matches!(
            self,
            TestOutcome::Failed(..)
                | TestOutcome::Error(..)
                | TestOutcome::Timeout { .. }
                | TestOutcome::XPassed { strict: true }
        )
    }

    /// Canonical lowercase status string. Matches the strings sent by worker subprocesses.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Passed { .. } => "passed",
            Self::Failed(..) => "failed",
            Self::Error(..) => "error",
            Self::Skipped { .. } => "skipped",
            Self::Warned { .. } => "warned",
            Self::XFailed { .. } => "xfailed",
            Self::XPassed { .. } => "xpassed",
            Self::Timeout { .. } => "timeout",
            Self::Flaky { .. } => "flaky",
        }
    }

    /// Returns the message or reason field from any variant that carries one.
    ///
    /// - `Passed` and `XPassed` have no message → `None`.
    /// - For variants with a `message` or `reason` field, returns `Some` only
    ///   when the string is non-empty (empty strings map to `None`).
    pub fn message(&self) -> Option<&str> {
        let s = match self {
            Self::Passed { .. } | Self::XPassed { .. } => return None,
            Self::Failed(d) | Self::Error(d) => d.message.as_str(),
            Self::Timeout { message } | Self::Flaky { message } => message.as_str(),
            Self::Skipped { reason } | Self::Warned { reason, .. } | Self::XFailed { reason } => {
                reason.as_str()
            }
        };
        if s.is_empty() {
            None
        } else {
            Some(s)
        }
    }

    /// Returns the diagnostic payload for Failed and Error outcomes.
    pub fn diagnostic(&self) -> Option<&FailureDiagnostic> {
        match self {
            Self::Failed(d) | Self::Error(d) => Some(d),
            _ => None,
        }
    }

    /// True if this is an Error variant (not Failed).
    pub fn is_error(&self) -> bool {
        matches!(self, Self::Error(..))
    }

    /// Synthesise an error for a test that could not execute.
    pub fn error_sentinel(message: String) -> Self {
        TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(message)))
    }

    /// Synthesise for an unresponsive worker subprocess.
    pub fn timed_out_sentinel(watchdog: std::time::Duration) -> (Self, f64) {
        let outcome = Self::error_sentinel(format!(
            "Worker subprocess unresponsive after {}s",
            watchdog.as_secs()
        ));
        (outcome, watchdog.as_millis() as f64)
    }

    /// Synthesise for a crashed worker subprocess.
    pub fn crashed_sentinel() -> Self {
        Self::error_sentinel("Worker subprocess exited unexpectedly".to_string())
    }
}

impl std::fmt::Display for TestOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(thiserror::Error, Debug)]
pub enum CollectError {
    #[error("collection error in {path}:\n{message}")]
    ImportError { path: Utf8PathBuf, message: String },
    #[error("{0}")]
    PyError(String),
    #[error(transparent)]
    Affected(#[from] crate::affected::AffectedError),
}

/// Lightweight tag for the kind of test outcome — no payload, just the label.
///
/// Used in [`TestTiming`] and [`CacheEntry`](crate::cache) to avoid stringly-typed
/// comparisons. The `#[serde(rename_all = "snake_case")]` attribute ensures round-trip
/// compatibility with the JSON cache and worker protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutcomeKind {
    Passed,
    Failed,
    Error,
    Skipped,
    Warned,
    #[serde(rename = "xfailed")]
    XFailed,
    #[serde(rename = "xpassed")]
    XPassed,
    Timeout,
    Flaky,
    /// Catch-all for unrecognised outcome strings from workers.
    #[serde(other)]
    Unknown,
}

impl OutcomeKind {
    /// True for outcomes that represent a definitive test failure.
    pub fn is_failure(&self) -> bool {
        matches!(self, Self::Failed | Self::Error | Self::Timeout)
    }

    /// Canonical lowercase status string matching [`TestOutcome::as_str()`].
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Passed => "passed",
            Self::Failed => "failed",
            Self::Error => "error",
            Self::Skipped => "skipped",
            Self::Warned => "warned",
            Self::XFailed => "xfailed",
            Self::XPassed => "xpassed",
            Self::Timeout => "timeout",
            Self::Flaky => "flaky",
            Self::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for OutcomeKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl From<&TestOutcome> for OutcomeKind {
    fn from(outcome: &TestOutcome) -> Self {
        match outcome {
            TestOutcome::Passed { .. } => Self::Passed,
            TestOutcome::Failed(..) => Self::Failed,
            TestOutcome::Error(..) => Self::Error,
            TestOutcome::Skipped { .. } => Self::Skipped,
            TestOutcome::Warned { .. } => Self::Warned,
            TestOutcome::XFailed { .. } => Self::XFailed,
            TestOutcome::XPassed { .. } => Self::XPassed,
            TestOutcome::Timeout { .. } => Self::Timeout,
            TestOutcome::Flaky { .. } => Self::Flaky,
        }
    }
}

/// Result record for a single test execution, returned from the run phases.
#[derive(Debug, Clone)]
pub struct TestTiming {
    pub node_id: NodeId,
    pub duration_ms: DurationMs,
    pub outcome: OutcomeKind,
}

/// Tracks hard failures across the test run and implements the `--maxfail` stop condition.
///
/// Every call to [`record`](FailureAccumulator::record) returns `true` once the failure
/// count reaches `maxfail`. A `maxfail` of 0 disables the limit (never stops early).
pub(crate) struct FailureAccumulator {
    count: usize,
    max: usize,
}

impl FailureAccumulator {
    pub(crate) fn new(maxfail: usize) -> Self {
        Self {
            count: 0,
            max: maxfail,
        }
    }

    /// Record an outcome. Returns `true` if execution should stop (maxfail reached).
    #[must_use = "caller must check whether maxfail was reached"]
    pub(crate) fn record(&mut self, outcome: &TestOutcome) -> bool {
        if outcome.is_hard_failure() {
            self.count += 1;
        }
        self.max > 0 && self.count >= self.max
    }
}

#[cfg(test)]
mod failure_accumulator_tests {
    use super::*;

    #[test]
    fn test_no_maxfail_never_stops() {
        let mut acc = FailureAccumulator::new(0);
        let outcome = TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new())));
        assert!(!acc.record(&outcome));
        assert!(!acc.record(&outcome));
    }

    #[test]
    fn test_maxfail_stops_at_threshold() {
        let mut acc = FailureAccumulator::new(2);
        let fail = TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new())));
        let pass = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert!(!acc.record(&pass));
        assert!(!acc.record(&fail));
        assert!(acc.record(&fail)); // 2nd failure = stop
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_outcome_passed_carries_no_message_lines() {
        let o = TestOutcome::Passed {
            no_message_lines: vec![5, 10],
        };
        if let TestOutcome::Passed { no_message_lines } = o {
            assert_eq!(no_message_lines, vec![5, 10]);
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_failed_carries_location() {
        let o = TestOutcome::Failed(Box::new(FailureDiagnostic {
            message: "msg".to_string(),
            file: Utf8PathBuf::from("test_foo.py"),
            lineno: LineNo::new(7),
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![],
            field_diffs: vec![],
        }));
        if let TestOutcome::Failed(d) = o {
            assert_eq!(d.lineno, LineNo::new(7));
            assert_eq!(d.left, "0");
            assert_eq!(d.right, "1");
            assert_eq!(d.op, "==");
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_warned_carries_reason() {
        let o = TestOutcome::Warned {
            reason: "DeprecationWarning: old api".to_string(),
            no_message_lines: vec![3],
        };
        if let TestOutcome::Warned {
            reason,
            no_message_lines,
        } = o
        {
            assert!(reason.contains("DeprecationWarning"));
            assert_eq!(no_message_lines, vec![3]);
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_xfailed_stores_reason() {
        let outcome = TestOutcome::XFailed {
            reason: "known bug".to_string(),
        };
        match outcome {
            TestOutcome::XFailed { reason } => assert_eq!(reason, "known bug"),
            _ => panic!("unexpected variant"),
        }
    }

    #[test]
    fn test_outcome_xpassed_stores_strict() {
        let strict_outcome = TestOutcome::XPassed { strict: true };
        let lenient_outcome = TestOutcome::XPassed { strict: false };
        match strict_outcome {
            TestOutcome::XPassed { strict } => assert!(strict),
            _ => panic!("unexpected variant"),
        }
        match lenient_outcome {
            TestOutcome::XPassed { strict } => assert!(!strict),
            _ => panic!("unexpected variant"),
        }
    }

    #[test]
    fn test_item_has_param_fields() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_add", Some("basic")),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_add".to_string(),
            lineno: LineNo::new(1),
            markers: vec![],
            param_id: Some("basic".to_string()),
            param_values: vec![ParamPair {
                name: "x".to_string(),
                value: "1".to_string(),
            }],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
        };
        assert_eq!(item.param_id, Some("basic".to_string()));
        assert_eq!(item.param_values.len(), 1);
    }

    #[test]
    fn test_item_non_parametrize_has_none_param_id() {
        let item = TestItem {
            node_id: NodeId::new("test.py", "test_foo", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_foo".to_string(),
            lineno: LineNo::new(1),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
        };
        assert!(item.param_id.is_none());
        assert!(item.param_values.is_empty());
    }

    #[test]
    fn test_item_has_is_async_field() {
        let sync_item = TestItem {
            node_id: NodeId::new("test.py", "test_sync", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_sync".to_string(),
            lineno: LineNo::new(1),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: false,
            fixture_names: vec![],
            fixref_names: vec![],
        };
        assert!(!sync_item.is_async);

        let async_item = TestItem {
            node_id: NodeId::new("test.py", "test_async", None),
            module_path: Utf8PathBuf::from("test.py"),
            fn_name: "test_async".to_string(),
            lineno: LineNo::new(1),
            markers: vec![],
            param_id: None,
            param_values: vec![],
            is_async: true,
            fixture_names: vec![],
            fixref_names: vec![],
        };
        assert!(async_item.is_async);
    }

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
    fn test_outcome_timeout_stores_message() {
        let o = TestOutcome::Timeout {
            message: "Timed out after 3s".to_string(),
        };
        match o {
            TestOutcome::Timeout { message } => assert!(message.contains("3s")),
            _ => panic!("wrong variant"),
        }
    }

    // ── as_str ───────────────────────────────────────────────────────────────

    #[test]
    fn as_str_all_variants() {
        let cases: Vec<(TestOutcome, &str)> = vec![
            (
                TestOutcome::Passed {
                    no_message_lines: vec![],
                },
                "passed",
            ),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
                "failed",
            ),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                "error",
            ),
            (
                TestOutcome::Skipped {
                    reason: String::new(),
                },
                "skipped",
            ),
            (
                TestOutcome::Warned {
                    reason: String::new(),
                    no_message_lines: vec![],
                },
                "warned",
            ),
            (
                TestOutcome::XFailed {
                    reason: String::new(),
                },
                "xfailed",
            ),
            (TestOutcome::XPassed { strict: false }, "xpassed"),
            (
                TestOutcome::Timeout {
                    message: String::new(),
                },
                "timeout",
            ),
            (
                TestOutcome::Flaky {
                    message: String::new(),
                },
                "flaky",
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(
                outcome.as_str(),
                expected,
                "as_str mismatch for {outcome:?}"
            );
        }
    }

    // ── is_hard_failure ──────────────────────────────────────────────────────

    #[test]
    fn is_hard_failure_all_variants() {
        let hard: Vec<TestOutcome> = vec![
            TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::XPassed { strict: true },
            TestOutcome::Timeout {
                message: String::new(),
            },
        ];
        for outcome in &hard {
            assert!(
                outcome.is_hard_failure(),
                "expected hard failure for {outcome:?}"
            );
        }

        let not_hard: Vec<TestOutcome> = vec![
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![],
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::XPassed { strict: false },
            TestOutcome::Flaky {
                message: String::new(),
            },
        ];
        for outcome in &not_hard {
            assert!(
                !outcome.is_hard_failure(),
                "expected non-hard-failure for {outcome:?}"
            );
        }
    }

    #[test]
    fn test_flaky_outcome_kind() {
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
        };
        assert_eq!(OutcomeKind::from(&outcome), OutcomeKind::Flaky);
    }

    #[test]
    fn test_flaky_outcome_kind_is_not_failure() {
        assert!(!OutcomeKind::Flaky.is_failure());
    }

    #[test]
    fn test_collect_error_import_display_shows_path_and_traceback() {
        use camino::Utf8PathBuf;
        let err = CollectError::ImportError {
            path: Utf8PathBuf::from("tests/bad.py"),
            message: "Traceback (most recent call last):\n  File \"tests/bad.py\", line 1\nModuleNotFoundError: No module named 'foo'".to_string(),
        };
        let s = format!("{}", err);
        assert!(s.contains("tests/bad.py"), "path must appear in output");
        assert!(s.contains("Traceback"), "traceback must appear in output");
        assert!(s.contains("ModuleNotFoundError"), "error type must appear");
        assert!(
            !s.contains("TestResult("),
            "must not contain TestResult repr"
        );
        assert!(!s.contains("ImportError in"), "old format must be gone");
        // newline between path label and traceback body
        assert!(s.contains('\n'), "output must contain real newlines");
    }

    #[test]
    fn test_collect_error_pyerror_display_shows_message_directly() {
        let err = CollectError::PyError("Failed to load conftest: SyntaxError".to_string());
        let s = format!("{}", err);
        assert_eq!(s, "Failed to load conftest: SyntaxError");
        assert!(!s.starts_with("PyError:"), "PyError prefix must be gone");
    }

    #[test]
    fn test_timing_fields() {
        let t = TestTiming {
            node_id: NodeId::from_raw("tests/test_foo.py::test_a"),
            duration_ms: DurationMs::new(42.5),
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(t.outcome, OutcomeKind::Passed);
        assert!((t.duration_ms.as_f64() - 42.5).abs() < 0.01);
        assert_eq!(t.node_id.to_string(), "tests/test_foo.py::test_a");
    }

    #[test]
    fn test_timing_outcome_is_enum() {
        let timing = TestTiming {
            node_id: NodeId::from_raw("test_mod::test_fn"),
            duration_ms: DurationMs::new(42.0),
            outcome: OutcomeKind::Passed,
        };
        assert_eq!(timing.outcome, OutcomeKind::Passed);
    }

    #[test]
    fn builder_defaults() {
        let item = TestItem::builder("tests/test_foo.py", "test_add").build();
        assert_eq!(item.node_id.to_string(), "tests/test_foo.py::test_add");
        assert_eq!(item.module_path.as_str(), "tests/test_foo.py");
        assert_eq!(item.fn_name, "test_add");
        assert_eq!(item.lineno, LineNo::new(1));
        assert!(item.markers.is_empty());
        assert!(item.param_id.is_none());
        assert!(item.param_values.is_empty());
        assert!(!item.is_async);
        assert!(item.fixture_names.is_empty());
    }

    #[test]
    fn builder_with_overrides() {
        let item = TestItem::builder("tests/test_foo.py", "test_add")
            .lineno(42)
            .markers(vec!["slow".to_string()])
            .async_fn(true)
            .fixture_names(vec!["db".to_string()])
            .fixref_names(vec!["backend".to_string()])
            .build();
        assert_eq!(item.lineno, LineNo::new(42));
        assert_eq!(item.markers, vec!["slow"]);
        assert!(item.is_async);
        assert_eq!(item.fixture_names, vec!["db"]);
        assert_eq!(item.fixref_names, vec!["backend"]);
    }

    #[test]
    fn builder_raw_node_id() {
        let item = TestItem::builder_raw("tests/test_foo.py::test_fn").build();
        assert_eq!(item.node_id.to_string(), "tests/test_foo.py::test_fn");
        assert_eq!(item.fn_name, "tests/test_foo.py::test_fn");
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
        use std::sync::Arc;
        let item: Arc<TestItem> = TestItem::builder("tests/test_foo.py", "test_add").arc();
        assert_eq!(item.fn_name, "test_add");
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

    // ── TestOutcome builders ────────────────────────────────────────────────

    #[test]
    fn outcome_failed_builder_defaults() {
        let outcome = TestOutcome::failed("oops").build();
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.message, "oops");
                assert_eq!(d.file.as_str(), "tests/test_foo.py");
                assert_eq!(d.lineno, LineNo::new(1));
                assert_eq!(d.source_line, "");
                assert_eq!(d.left, "");
                assert_eq!(d.right, "");
                assert_eq!(d.op, "");
                assert!(d.frames.is_empty());
                assert!(d.field_diffs.is_empty());
            }
            other => panic!("expected Failed, got {}", other.as_str()),
        }
    }

    #[test]
    fn outcome_failed_builder_with_comparison() {
        let outcome = TestOutcome::failed("assert x == 42")
            .file("test.py")
            .lineno(8)
            .source("assert x == 42")
            .comparison("41", "==", "42")
            .build();
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.left, "41");
                assert_eq!(d.op, "==");
                assert_eq!(d.right, "42");
                assert_eq!(d.file.as_str(), "test.py");
                assert_eq!(d.lineno, LineNo::new(8));
            }
            other => panic!("expected Failed, got {}", other.as_str()),
        }
    }

    #[test]
    fn outcome_error_builder_defaults() {
        let outcome = TestOutcome::error("RuntimeError").build();
        match outcome {
            TestOutcome::Error(d) => {
                assert_eq!(d.message, "RuntimeError");
                assert_eq!(d.file.as_str(), "tests/test_foo.py");
                assert_eq!(d.lineno, LineNo::new(1));
                assert_eq!(d.source_line, "");
                assert!(d.frames.is_empty());
            }
            other => panic!("expected Error, got {}", other.as_str()),
        }
    }

    #[test]
    fn outcome_error_builder_with_overrides() {
        let outcome = TestOutcome::error("ValueError")
            .file("mod.py")
            .lineno(5)
            .source("int('abc')")
            .build();
        match outcome {
            TestOutcome::Error(d) => {
                assert_eq!(d.message, "ValueError");
                assert_eq!(d.file.as_str(), "mod.py");
                assert_eq!(d.lineno, LineNo::new(5));
                assert_eq!(d.source_line, "int('abc')");
            }
            other => panic!("expected Error, got {}", other.as_str()),
        }
    }

    #[test]
    fn worker_status_strings_match_test_outcome_as_str() {
        use std::collections::HashSet;
        // These are the literal strings emitted as result.status by
        // python/oxitest/_bridge/executor.py and python/oxitest/_bridge/marks.py,
        // then forwarded by python/oxitest/_bridge/worker.py via the "outcome" JSON key.
        // NOTE: "flaky" is NOT in this set — Flaky is a Rust-synthesised outcome
        // produced by the retry logic after a test fails then passes; Python workers
        // never emit it directly.
        let worker_strings: HashSet<&str> = [
            "passed", "failed", "error", "skipped", "warned", "xfailed", "xpassed", "timeout",
        ]
        .into_iter()
        .collect();

        // Exclude Flaky — it is synthesised by Rust, not emitted by workers.
        let outcome_strings: HashSet<&str> = [
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![],
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::XPassed { strict: false },
            TestOutcome::Timeout {
                message: String::new(),
            },
        ]
        .iter()
        .map(TestOutcome::as_str)
        .collect();

        assert_eq!(
            worker_strings, outcome_strings,
            "Worker status strings must exactly match TestOutcome::as_str() values \
             (excluding Rust-synthesised outcomes like Flaky).\n\
             If you add a new outcome from the Python side, update both worker.py and this test."
        );
    }
}

#[cfg(test)]
mod message_tests {
    use super::*;

    #[test]
    fn message_populated_variants() {
        let cases: Vec<(TestOutcome, Option<&str>)> = vec![
            (
                TestOutcome::Passed {
                    no_message_lines: vec![],
                },
                None,
            ),
            (TestOutcome::XPassed { strict: true }, None),
            (TestOutcome::XPassed { strict: false }, None),
            (
                TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(
                    "assertion failed".to_string(),
                ))),
                Some("assertion failed"),
            ),
            (
                TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(
                    "ImportError".to_string(),
                ))),
                Some("ImportError"),
            ),
            (
                TestOutcome::Skipped {
                    reason: "not ready".to_string(),
                },
                Some("not ready"),
            ),
            (
                TestOutcome::Warned {
                    reason: "DeprecationWarning".to_string(),
                    no_message_lines: vec![],
                },
                Some("DeprecationWarning"),
            ),
            (
                TestOutcome::XFailed {
                    reason: "known bug".to_string(),
                },
                Some("known bug"),
            ),
            (
                TestOutcome::Timeout {
                    message: "exceeded 5s".to_string(),
                },
                Some("exceeded 5s"),
            ),
            (
                TestOutcome::Flaky {
                    message: "flaky test".to_string(),
                },
                Some("flaky test"),
            ),
        ];
        for (outcome, expected) in cases {
            assert_eq!(
                outcome.message(),
                expected,
                "message mismatch for {outcome:?}"
            );
        }
    }

    #[test]
    fn message_empty_string_returns_none() {
        let cases: Vec<TestOutcome> = vec![
            TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![],
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::Timeout {
                message: String::new(),
            },
            TestOutcome::Flaky {
                message: String::new(),
            },
        ];
        for outcome in cases {
            assert!(
                outcome.message().is_none(),
                "expected None for empty message/reason in {outcome:?}"
            );
        }
    }
}

#[cfg(test)]
mod diagnostic_tests {
    use super::*;

    #[test]
    fn failed_returns_some_with_all_fields() {
        let outcome = TestOutcome::Failed(Box::new(FailureDiagnostic {
            message: "expected 4".to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(8),
            source_line: "assert add(1, 2) == 4".to_string(),
            left: "3".to_string(),
            right: "4".to_string(),
            op: "==".to_string(),
            frames: vec![],
            field_diffs: vec![],
        }));
        let diag = outcome.diagnostic().expect("Failed should return Some");
        assert_eq!(diag.file.as_str(), "tests/test_foo.py");
        assert_eq!(diag.lineno, LineNo::new(8));
        assert_eq!(diag.source_line, "assert add(1, 2) == 4");
        assert_eq!(diag.message, "expected 4");
        assert_eq!(diag.left, "3");
        assert_eq!(diag.right, "4");
        assert_eq!(diag.op, "==");
        assert!(diag.frames.is_empty());
    }

    #[test]
    fn error_returns_some_with_empty_comparison_fields() {
        let outcome = TestOutcome::Error(Box::new(FailureDiagnostic::error(
            "ValueError: bad".to_string(),
            Utf8PathBuf::from("tests/test_foo.py"),
            LineNo::new(22),
            "result = divide(10, 0)".to_string(),
            vec![],
        )));
        let diag = outcome.diagnostic().expect("Error should return Some");
        assert_eq!(diag.file.as_str(), "tests/test_foo.py");
        assert_eq!(diag.lineno, LineNo::new(22));
        assert_eq!(diag.message, "ValueError: bad");
        assert!(diag.left.is_empty());
        assert!(diag.right.is_empty());
        assert!(diag.op.is_empty());
    }

    #[test]
    fn non_diagnostic_variants_return_none() {
        let cases: Vec<TestOutcome> = vec![
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
            TestOutcome::Skipped {
                reason: "not ready".to_string(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![],
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::XPassed { strict: true },
            TestOutcome::XPassed { strict: false },
            TestOutcome::Timeout {
                message: "exceeded 5s".to_string(),
            },
            TestOutcome::Flaky {
                message: "flaky".to_string(),
            },
        ];
        for outcome in cases {
            assert!(
                outcome.diagnostic().is_none(),
                "expected None for {outcome:?}"
            );
        }
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
