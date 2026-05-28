//! Core data types shared across the runner.
//!
//! Defines [`NodeId`] (stable test identifier), [`TestItem`] (collected test metadata),
//! [`TestOutcome`] (the eight possible results of running a test), [`CollectError`],
//! and [`TestTiming`].

use std::sync::Arc;

use camino::Utf8PathBuf;

/// Stable test identifier used throughout the runner.
/// Format: `module_path::fn_name` or `module_path::fn_name[param_id]`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct NodeId(Arc<str>);

impl NodeId {
    pub fn new(module_path: &str, fn_name: &str, param_id: Option<&str>) -> Self {
        let base = format!("{}::{}", module_path, fn_name);
        match param_id {
            Some(id) => NodeId(format!("{}[{}]", base, id).into()),
            None => NodeId(base.into()),
        }
    }

    /// Create a NodeId from an already-formatted string (e.g. received from a worker subprocess).
    pub fn from_raw(s: &str) -> Self {
        NodeId(Arc::from(s))
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
    pub(crate) param_values: Vec<(String, String)>,
    pub(crate) is_async: bool,
}

/// Single traceback frame from a test failure or error.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub file: Utf8PathBuf,
    pub lineno: LineNo,
    pub name: String,
    pub line: String,
}

/// The eight possible results of running a single test.
///
/// Variants carry structured diagnostic data (file, line, left/right values for diffs,
/// traceback frames) so the reporter can render rich output without re-parsing strings.
/// Use [`TestOutcome::is_hard_failure`] to determine whether the run exits with code 1.
#[derive(Debug, Clone)]
pub enum TestOutcome {
    Passed {
        no_message_lines: Vec<usize>,
    },
    Failed {
        message: String,
        file: Utf8PathBuf,
        lineno: LineNo,
        source_line: String,
        left: String,
        right: String,
        op: String,
        frames: Vec<Frame>,
    },
    Error {
        message: String,
        file: Utf8PathBuf,
        lineno: LineNo,
        source_line: String,
        frames: Vec<Frame>,
    },
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
            TestOutcome::Failed { .. }
                | TestOutcome::Error { .. }
                | TestOutcome::Timeout { .. }
                | TestOutcome::XPassed { strict: true }
        )
    }

    /// Single character for CI dot-progress output.
    pub fn dot_char(&self) -> char {
        match self {
            Self::Passed { no_message_lines } if no_message_lines.is_empty() => '.',
            Self::Passed { .. } => '\u{00B7}', // middot — bare assert passed
            Self::Failed { .. } => 'F',
            Self::Error { .. } => 'E',
            Self::Skipped { .. } => 's',
            Self::Warned { .. } => '.', // same as clean pass — warning shown in summary
            Self::XFailed { .. } => 'x',
            Self::XPassed { .. } => 'X',
            Self::Timeout { .. } => 'T',
            Self::Flaky { .. } => 'f',
        }
    }

    /// Short display label for TTY output. Empty string for passing tests.
    pub fn label(&self) -> &'static str {
        match self {
            Self::Failed { .. } => "FAIL ",
            Self::Error { .. } => "ERROR",
            Self::Skipped { .. } => "SKIP ",
            Self::Warned { .. } => "WARN ",
            Self::XFailed { .. } => "XFAIL",
            Self::XPassed { .. } => "XPASS",
            Self::Timeout { .. } => "TIME ",
            Self::Flaky { .. } => "FLAKY",
            Self::Passed { .. } => "",
        }
    }

    /// Canonical lowercase status string. Matches the strings sent by worker subprocesses.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Passed { .. } => "passed",
            Self::Failed { .. } => "failed",
            Self::Error { .. } => "error",
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
            Self::Failed { message, .. }
            | Self::Error { message, .. }
            | Self::Timeout { message }
            | Self::Flaky { message } => message.as_str(),
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

    /// Classifies the outcome for color/style selection in TTY output.
    pub fn color_category(&self) -> ColorCategory {
        match self {
            Self::Passed { .. } => ColorCategory::Pass,
            Self::Failed { .. } | Self::XPassed { strict: true } => ColorCategory::Fail,
            Self::Error { .. } => ColorCategory::Error,
            Self::Skipped { .. } => ColorCategory::Skip,
            Self::Warned { .. } | Self::XPassed { strict: false } | Self::Flaky { .. } => {
                ColorCategory::Warn
            }
            Self::XFailed { .. } => ColorCategory::Dim,
            Self::Timeout { .. } => ColorCategory::Timeout,
        }
    }

    /// Classifies the outcome for JUnit XML element selection.
    pub fn junit_category(&self) -> JunitCategory {
        match self {
            Self::Passed { .. }
            | Self::Warned { .. }
            | Self::XPassed { strict: false }
            | Self::Flaky { .. } => JunitCategory::Passed,
            Self::Failed { .. } | Self::XPassed { strict: true } => JunitCategory::Failed,
            Self::Error { .. } | Self::Timeout { .. } => JunitCategory::Error,
            Self::Skipped { .. } | Self::XFailed { .. } => JunitCategory::Skipped,
        }
    }

    /// Maps the outcome to a CTRF status string (`"passed"`, `"failed"`, or `"skipped"`).
    ///
    /// CTRF defines only three statuses. This method centralises the mapping so
    /// reporter code does not duplicate match arms.
    pub fn ctrf_status(&self) -> &'static str {
        match self {
            Self::Passed { .. }
            | Self::Warned { .. }
            | Self::XPassed { strict: false }
            | Self::Flaky { .. } => "passed",
            Self::Failed { .. }
            | Self::Error { .. }
            | Self::XPassed { strict: true }
            | Self::Timeout { .. } => "failed",
            Self::Skipped { .. } | Self::XFailed { .. } => "skipped",
        }
    }

    /// Extract structured diagnostic parts for rendering.
    ///
    /// Returns `Some` for `Failed` and `Error` variants (which carry location and
    /// traceback data), `None` for all other variants.
    pub fn diagnostic_parts(&self) -> Option<DiagnosticParts<'_>> {
        match self {
            TestOutcome::Failed {
                message,
                file,
                lineno,
                source_line,
                left,
                right,
                op,
                frames,
            } => Some(DiagnosticParts {
                file: file.as_str(),
                lineno: *lineno,
                source_line,
                message,
                frames,
                left,
                right,
                op,
            }),
            TestOutcome::Error {
                message,
                file,
                lineno,
                source_line,
                frames,
            } => Some(DiagnosticParts {
                file: file.as_str(),
                lineno: *lineno,
                source_line,
                message,
                frames,
                left: "",
                right: "",
                op: "",
            }),
            _ => None,
        }
    }
}

impl std::fmt::Display for TestOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Structured diagnostic data extracted from a `Failed` or `Error` outcome.
///
/// Used by the diagnostic renderer to separate data extraction from formatting.
/// Fields borrow from the parent `TestOutcome` — the lifetime ties them together.
pub struct DiagnosticParts<'a> {
    pub file: &'a str,
    pub lineno: LineNo,
    pub source_line: &'a str,
    pub message: &'a str,
    pub frames: &'a [Frame],
    /// Left operand of the comparison (empty for Error outcomes).
    pub left: &'a str,
    /// Right operand of the comparison (empty for Error outcomes).
    pub right: &'a str,
    /// Comparison operator (empty for Error outcomes or non-comparison assertions).
    pub op: &'a str,
}

/// Classification of a [`TestOutcome`] for color/style selection in TTY output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColorCategory {
    Pass,
    Fail,
    Error,
    Skip,
    Warn,
    Dim,
    Timeout,
}

/// Classification of a [`TestOutcome`] for JUnit XML element selection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JunitCategory {
    Passed,
    Failed,
    Error,
    Skipped,
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
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
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
            TestOutcome::Failed { .. } => Self::Failed,
            TestOutcome::Error { .. } => Self::Error,
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

/// Normalised inputs for constructing a `TestOutcome` from either the serial
/// (`bridge.rs`) or parallel (`parallel.rs`) execution path.
/// Each caller is responsible for mapping its own field types (Option<String>,
/// i64, etc.) into this normalised form before calling `from_raw`.
pub struct RawOutcome<'a> {
    pub status: &'a str,
    pub message: &'a str,
    pub file: &'a str,
    pub lineno: LineNo,
    pub source_line: &'a str,
    pub no_message_lines: &'a [usize],
    pub left: &'a str,
    pub right: &'a str,
    pub op: &'a str,
    pub strict: bool,
    pub frames: &'a [Frame],
}

impl TestOutcome {
    /// Build a `TestOutcome` from normalised raw fields.
    ///
    /// The `_` arm maps any unrecognised status to `Error`. Callers that want
    /// to log a warning for unknown statuses should do so before calling this.
    pub fn from_raw(r: RawOutcome<'_>) -> Self {
        match r.status {
            "passed" => TestOutcome::Passed {
                no_message_lines: r.no_message_lines.to_vec(),
            },
            "warned" => TestOutcome::Warned {
                reason: r.message.to_owned(),
                no_message_lines: r.no_message_lines.to_vec(),
            },
            "failed" => TestOutcome::Failed {
                message: r.message.to_owned(),
                file: Utf8PathBuf::from(r.file),
                lineno: r.lineno,
                source_line: r.source_line.to_owned(),
                left: r.left.to_owned(),
                right: r.right.to_owned(),
                op: r.op.to_owned(),
                frames: r.frames.to_vec(),
            },
            "skipped" => TestOutcome::Skipped {
                reason: r.message.to_owned(),
            },
            "xfailed" => TestOutcome::XFailed {
                reason: r.message.to_owned(),
            },
            "xpassed" => TestOutcome::XPassed { strict: r.strict },
            "timeout" => TestOutcome::Timeout {
                message: r.message.to_owned(),
            },
            _ => TestOutcome::Error {
                message: r.message.to_owned(),
                file: Utf8PathBuf::from(r.file),
                lineno: r.lineno,
                source_line: r.source_line.to_owned(),
                frames: r.frames.to_vec(),
            },
        }
    }
}

#[cfg(test)]
mod failure_accumulator_tests {
    use super::*;

    #[test]
    fn test_no_maxfail_never_stops() {
        let mut acc = FailureAccumulator::new(0);
        let outcome = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert!(!acc.record(&outcome));
        assert!(!acc.record(&outcome));
    }

    #[test]
    fn test_maxfail_stops_at_threshold() {
        let mut acc = FailureAccumulator::new(2);
        let fail = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
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
        let o = TestOutcome::Failed {
            message: "msg".to_string(),
            file: Utf8PathBuf::from("test_foo.py"),
            lineno: LineNo::new(7),
            source_line: "assert x == 1".to_string(),
            left: "0".to_string(),
            right: "1".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        if let TestOutcome::Failed {
            lineno,
            left,
            right,
            op,
            ..
        } = o
        {
            assert_eq!(lineno, LineNo::new(7));
            assert_eq!(left, "0");
            assert_eq!(right, "1");
            assert_eq!(op, "==");
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
            param_values: vec![("x".to_string(), "1".to_string())],
            is_async: false,
        };
        assert_eq!(item.param_id, Some("basic".to_string()));
        assert_eq!(item.param_values.len(), 1);
    }

    #[test]
    fn test_is_hard_failure_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert!(o.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert!(o.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_xpassed_strict() {
        assert!(TestOutcome::XPassed { strict: true }.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_xpassed_lenient() {
        assert!(!TestOutcome::XPassed { strict: false }.is_hard_failure());
    }

    #[test]
    fn test_is_hard_failure_passed() {
        assert!(!TestOutcome::Passed {
            no_message_lines: vec![]
        }
        .is_hard_failure());
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
    fn test_outcome_timeout_is_hard_failure() {
        let o = TestOutcome::Timeout {
            message: "Timed out after 5s".to_string(),
        };
        assert!(o.is_hard_failure());
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

    // ── dot_char ─────────────────────────────────────────────────────────────

    #[test]
    fn test_dot_char_passed_no_bare_assert() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .dot_char(),
            '.'
        );
    }

    #[test]
    fn test_dot_char_passed_bare_assert() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![5]
            }
            .dot_char(),
            '\u{00B7}'
        );
    }

    #[test]
    fn test_dot_char_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.dot_char(), 'F');
    }

    #[test]
    fn test_dot_char_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.dot_char(), 'E');
    }

    #[test]
    fn test_dot_char_skipped() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .dot_char(),
            's'
        );
    }

    #[test]
    fn test_dot_char_warned() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .dot_char(),
            '.'
        );
    }

    #[test]
    fn test_dot_char_xfailed() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .dot_char(),
            'x'
        );
    }

    #[test]
    fn test_dot_char_xpassed() {
        assert_eq!(TestOutcome::XPassed { strict: true }.dot_char(), 'X');
        assert_eq!(TestOutcome::XPassed { strict: false }.dot_char(), 'X');
    }

    #[test]
    fn test_dot_char_timeout() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .dot_char(),
            'T'
        );
    }

    // ── label ────────────────────────────────────────────────────────────────

    #[test]
    fn test_label_passed_is_empty() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .label(),
            ""
        );
    }

    #[test]
    fn test_label_passed_bare_assert_is_empty() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![5]
            }
            .label(),
            ""
        );
    }

    #[test]
    fn test_label_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.label(), "FAIL ");
    }

    #[test]
    fn test_label_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.label(), "ERROR");
    }

    #[test]
    fn test_label_skipped() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .label(),
            "SKIP "
        );
    }

    #[test]
    fn test_label_warned() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .label(),
            "WARN "
        );
    }

    #[test]
    fn test_label_xfailed() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .label(),
            "XFAIL"
        );
    }

    #[test]
    fn test_label_xpassed() {
        // Both strict variants return same text; color differs in tty.rs
        assert_eq!(TestOutcome::XPassed { strict: true }.label(), "XPASS");
        assert_eq!(TestOutcome::XPassed { strict: false }.label(), "XPASS");
    }

    #[test]
    fn test_label_timeout() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .label(),
            "TIME "
        );
    }

    // ── as_str ───────────────────────────────────────────────────────────────

    #[test]
    fn test_as_str_all_variants() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .as_str(),
            "passed"
        );
        assert_eq!(
            TestOutcome::Failed {
                message: String::new(),
                file: Utf8PathBuf::new(),
                lineno: LineNo::ZERO,
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
                frames: vec![],
            }
            .as_str(),
            "failed"
        );
        assert_eq!(
            TestOutcome::Error {
                message: String::new(),
                file: Utf8PathBuf::new(),
                lineno: LineNo::ZERO,
                source_line: String::new(),
                frames: vec![],
            }
            .as_str(),
            "error"
        );
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .as_str(),
            "skipped"
        );
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .as_str(),
            "warned"
        );
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .as_str(),
            "xfailed"
        );
        assert_eq!(TestOutcome::XPassed { strict: false }.as_str(), "xpassed");
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .as_str(),
            "timeout"
        );
    }

    #[test]
    fn test_flaky_outcome_is_not_hard_failure() {
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
        };
        assert!(!outcome.is_hard_failure());
    }

    #[test]
    fn test_flaky_outcome_as_str() {
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
        };
        assert_eq!(outcome.as_str(), "flaky");
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

    fn raw(status: &str) -> RawOutcome<'_> {
        RawOutcome {
            status,
            message: "msg",
            file: "test.py",
            lineno: LineNo::new(5),
            source_line: "assert x",
            no_message_lines: &[],
            left: "0",
            right: "1",
            op: "==",
            strict: false,
            frames: &[],
        }
    }

    #[test]
    fn from_raw_passed_returns_passed_variant() {
        assert!(matches!(
            TestOutcome::from_raw(raw("passed")),
            TestOutcome::Passed { .. }
        ));
    }

    #[test]
    fn from_raw_failed_carries_fields() {
        let o = TestOutcome::from_raw(RawOutcome {
            status: "failed",
            message: "oops",
            file: "test.py",
            lineno: LineNo::new(7),
            source_line: "assert x == 1",
            no_message_lines: &[],
            left: "0",
            right: "1",
            op: "==",
            strict: false,
            frames: &[],
        });
        match o {
            TestOutcome::Failed {
                message,
                lineno,
                left,
                right,
                op,
                ..
            } => {
                assert_eq!(message, "oops");
                assert_eq!(lineno, LineNo::new(7));
                assert_eq!(left, "0");
                assert_eq!(right, "1");
                assert_eq!(op, "==");
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn from_raw_unknown_status_becomes_error() {
        assert!(matches!(
            TestOutcome::from_raw(raw("nonsense")),
            TestOutcome::Error { .. }
        ));
    }

    #[test]
    fn from_raw_xpassed_strict_true() {
        let o = TestOutcome::from_raw(RawOutcome {
            status: "xpassed",
            strict: true,
            ..raw("xpassed")
        });
        match o {
            TestOutcome::XPassed { strict } => assert!(strict),
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn from_raw_all_known_statuses_do_not_return_error() {
        for status in &[
            "passed", "warned", "failed", "skipped", "xfailed", "xpassed", "timeout",
        ] {
            let o = TestOutcome::from_raw(raw(status));
            assert!(
                !matches!(o, TestOutcome::Error { .. }),
                "status '{}' should not map to Error",
                status
            );
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
            TestOutcome::Failed {
                message: String::new(),
                file: Utf8PathBuf::new(),
                lineno: LineNo::ZERO,
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
                frames: vec![],
            },
            TestOutcome::Error {
                message: String::new(),
                file: Utf8PathBuf::new(),
                lineno: LineNo::ZERO,
                source_line: String::new(),
                frames: vec![],
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
    fn passed_returns_none() {
        let o = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert!(o.message().is_none());
    }

    #[test]
    fn failed_returns_message() {
        let o = TestOutcome::Failed {
            message: "assertion failed".to_string(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.message(), Some("assertion failed"));
    }

    #[test]
    fn failed_empty_message_returns_none() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert!(o.message().is_none());
    }

    #[test]
    fn error_returns_message() {
        let o = TestOutcome::Error {
            message: "ImportError".to_string(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.message(), Some("ImportError"));
    }

    #[test]
    fn error_empty_message_returns_none() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert!(o.message().is_none());
    }

    #[test]
    fn skipped_returns_reason() {
        let o = TestOutcome::Skipped {
            reason: "not ready".to_string(),
        };
        assert_eq!(o.message(), Some("not ready"));
    }

    #[test]
    fn skipped_empty_reason_returns_none() {
        let o = TestOutcome::Skipped {
            reason: String::new(),
        };
        assert!(o.message().is_none());
    }

    #[test]
    fn warned_returns_reason() {
        let o = TestOutcome::Warned {
            reason: "DeprecationWarning".to_string(),
            no_message_lines: vec![],
        };
        assert_eq!(o.message(), Some("DeprecationWarning"));
    }

    #[test]
    fn xfailed_returns_reason() {
        let o = TestOutcome::XFailed {
            reason: "known bug".to_string(),
        };
        assert_eq!(o.message(), Some("known bug"));
    }

    #[test]
    fn xfailed_empty_reason_returns_none() {
        let o = TestOutcome::XFailed {
            reason: String::new(),
        };
        assert!(o.message().is_none());
    }

    #[test]
    fn xpassed_returns_none() {
        assert!(TestOutcome::XPassed { strict: true }.message().is_none());
        assert!(TestOutcome::XPassed { strict: false }.message().is_none());
    }

    #[test]
    fn timeout_returns_message() {
        let o = TestOutcome::Timeout {
            message: "exceeded 5s".to_string(),
        };
        assert_eq!(o.message(), Some("exceeded 5s"));
    }

    #[test]
    fn flaky_returns_message() {
        let o = TestOutcome::Flaky {
            message: "flaky test".to_string(),
        };
        assert_eq!(o.message(), Some("flaky test"));
    }

    #[test]
    fn flaky_empty_message_returns_none() {
        let o = TestOutcome::Flaky {
            message: String::new(),
        };
        assert!(o.message().is_none());
    }
}

#[cfg(test)]
mod ctrf_status_tests {
    use super::*;

    #[test]
    fn passed_is_passed() {
        let o = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert_eq!(o.ctrf_status(), "passed");
    }

    #[test]
    fn warned_is_passed() {
        let o = TestOutcome::Warned {
            reason: String::new(),
            no_message_lines: vec![],
        };
        assert_eq!(o.ctrf_status(), "passed");
    }

    #[test]
    fn failed_is_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.ctrf_status(), "failed");
    }

    #[test]
    fn error_is_failed() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.ctrf_status(), "failed");
    }

    #[test]
    fn skipped_is_skipped() {
        let o = TestOutcome::Skipped {
            reason: String::new(),
        };
        assert_eq!(o.ctrf_status(), "skipped");
    }

    #[test]
    fn xfailed_is_skipped() {
        let o = TestOutcome::XFailed {
            reason: String::new(),
        };
        assert_eq!(o.ctrf_status(), "skipped");
    }

    #[test]
    fn xpassed_strict_is_failed() {
        assert_eq!(
            TestOutcome::XPassed { strict: true }.ctrf_status(),
            "failed"
        );
    }

    #[test]
    fn xpassed_lenient_is_passed() {
        assert_eq!(
            TestOutcome::XPassed { strict: false }.ctrf_status(),
            "passed"
        );
    }

    #[test]
    fn timeout_is_failed() {
        let o = TestOutcome::Timeout {
            message: String::new(),
        };
        assert_eq!(o.ctrf_status(), "failed");
    }

    #[test]
    fn flaky_is_passed() {
        let o = TestOutcome::Flaky {
            message: String::new(),
        };
        assert_eq!(o.ctrf_status(), "passed");
    }
}

#[cfg(test)]
mod diagnostic_parts_tests {
    use super::*;

    #[test]
    fn failed_returns_some_with_all_fields() {
        let outcome = TestOutcome::Failed {
            message: "expected 4".to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(8),
            source_line: "assert add(1, 2) == 4".to_string(),
            left: "3".to_string(),
            right: "4".to_string(),
            op: "==".to_string(),
            frames: vec![],
        };
        let parts = outcome
            .diagnostic_parts()
            .expect("Failed should return Some");
        assert_eq!(parts.file, "tests/test_foo.py");
        assert_eq!(parts.lineno, LineNo::new(8));
        assert_eq!(parts.source_line, "assert add(1, 2) == 4");
        assert_eq!(parts.message, "expected 4");
        assert_eq!(parts.left, "3");
        assert_eq!(parts.right, "4");
        assert_eq!(parts.op, "==");
        assert!(parts.frames.is_empty());
    }

    #[test]
    fn error_returns_some_with_empty_comparison_fields() {
        let outcome = TestOutcome::Error {
            message: "ValueError: bad".to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(22),
            source_line: "result = divide(10, 0)".to_string(),
            frames: vec![],
        };
        let parts = outcome
            .diagnostic_parts()
            .expect("Error should return Some");
        assert_eq!(parts.file, "tests/test_foo.py");
        assert_eq!(parts.lineno, LineNo::new(22));
        assert_eq!(parts.message, "ValueError: bad");
        assert!(parts.left.is_empty());
        assert!(parts.right.is_empty());
        assert!(parts.op.is_empty());
    }

    #[test]
    fn passed_returns_none() {
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert!(outcome.diagnostic_parts().is_none());
    }

    #[test]
    fn skipped_returns_none() {
        let outcome = TestOutcome::Skipped {
            reason: "not ready".to_string(),
        };
        assert!(outcome.diagnostic_parts().is_none());
    }

    #[test]
    fn timeout_returns_none() {
        let outcome = TestOutcome::Timeout {
            message: "exceeded 5s".to_string(),
        };
        assert!(outcome.diagnostic_parts().is_none());
    }

    #[test]
    fn flaky_returns_none() {
        let outcome = TestOutcome::Flaky {
            message: "flaky".to_string(),
        };
        assert!(outcome.diagnostic_parts().is_none());
    }
}

#[cfg(test)]
mod color_category_tests {
    use super::*;

    #[test]
    fn passed_is_pass() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .color_category(),
            ColorCategory::Pass
        );
    }

    #[test]
    fn failed_is_fail() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.color_category(), ColorCategory::Fail);
    }

    #[test]
    fn error_is_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.color_category(), ColorCategory::Error);
    }

    #[test]
    fn skipped_is_skip() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .color_category(),
            ColorCategory::Skip
        );
    }

    #[test]
    fn warned_is_warn() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .color_category(),
            ColorCategory::Warn
        );
    }

    #[test]
    fn xfailed_is_dim() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .color_category(),
            ColorCategory::Dim
        );
    }

    #[test]
    fn xpassed_strict_is_fail() {
        assert_eq!(
            TestOutcome::XPassed { strict: true }.color_category(),
            ColorCategory::Fail
        );
    }

    #[test]
    fn xpassed_lenient_is_warn() {
        assert_eq!(
            TestOutcome::XPassed { strict: false }.color_category(),
            ColorCategory::Warn
        );
    }

    #[test]
    fn timeout_is_timeout() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .color_category(),
            ColorCategory::Timeout
        );
    }

    #[test]
    fn flaky_is_warn() {
        assert_eq!(
            TestOutcome::Flaky {
                message: String::new()
            }
            .color_category(),
            ColorCategory::Warn
        );
    }
}

#[cfg(test)]
mod junit_category_tests {
    use super::*;

    #[test]
    fn passed_is_passed() {
        assert_eq!(
            TestOutcome::Passed {
                no_message_lines: vec![]
            }
            .junit_category(),
            JunitCategory::Passed
        );
    }

    #[test]
    fn failed_is_failed() {
        let o = TestOutcome::Failed {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            left: String::new(),
            right: String::new(),
            op: String::new(),
            frames: vec![],
        };
        assert_eq!(o.junit_category(), JunitCategory::Failed);
    }

    #[test]
    fn error_is_error() {
        let o = TestOutcome::Error {
            message: String::new(),
            file: Utf8PathBuf::new(),
            lineno: LineNo::ZERO,
            source_line: String::new(),
            frames: vec![],
        };
        assert_eq!(o.junit_category(), JunitCategory::Error);
    }

    #[test]
    fn skipped_is_skipped() {
        assert_eq!(
            TestOutcome::Skipped {
                reason: String::new()
            }
            .junit_category(),
            JunitCategory::Skipped
        );
    }

    #[test]
    fn warned_is_passed() {
        assert_eq!(
            TestOutcome::Warned {
                reason: String::new(),
                no_message_lines: vec![]
            }
            .junit_category(),
            JunitCategory::Passed
        );
    }

    #[test]
    fn xfailed_is_skipped() {
        assert_eq!(
            TestOutcome::XFailed {
                reason: String::new()
            }
            .junit_category(),
            JunitCategory::Skipped
        );
    }

    #[test]
    fn xpassed_strict_is_failed() {
        assert_eq!(
            TestOutcome::XPassed { strict: true }.junit_category(),
            JunitCategory::Failed
        );
    }

    #[test]
    fn xpassed_lenient_is_passed() {
        assert_eq!(
            TestOutcome::XPassed { strict: false }.junit_category(),
            JunitCategory::Passed
        );
    }

    #[test]
    fn timeout_is_error() {
        assert_eq!(
            TestOutcome::Timeout {
                message: String::new()
            }
            .junit_category(),
            JunitCategory::Error
        );
    }

    #[test]
    fn flaky_is_passed() {
        assert_eq!(
            TestOutcome::Flaky {
                message: String::new()
            }
            .junit_category(),
            JunitCategory::Passed
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
