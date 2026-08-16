//! Test outcomes and diagnostic payloads.

use std::time::Duration;

use camino::Utf8PathBuf;

use super::{DurationMs, LineNo, LocalVar, NodeId};

/// Single traceback frame from a test failure or error.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub file: Utf8PathBuf,
    pub lineno: LineNo,
    pub name: String,
    pub line: String,
    pub locals: Vec<LocalVar>,
}

/// Comparison operands and field diffs for assertion failures.
///
/// Present only in `Failed` outcomes — `Error` outcomes have `comparison: None`.
#[derive(Debug, Clone)]
pub struct ComparisonDetail {
    pub left: String,
    pub right: String,
    pub op: String,
    pub field_diffs: Vec<super::FieldDiff>,
}

/// Structured diagnostic payload for Failed and Error outcomes.
///
/// `comparison` is `Some` for assertion failures and `None` for unhandled errors,
/// making the shape of the diagnostic honest about the outcome kind.
#[derive(Debug, Clone)]
pub struct FailureDiagnostic {
    pub message: String,
    pub file: Utf8PathBuf,
    pub lineno: LineNo,
    pub source_line: String,
    pub frames: Vec<Frame>,
    /// Comparison operands for assertion failures; `None` for errors.
    pub comparison: Option<ComparisonDetail>,
    /// True when this error means the suite is wired wrong rather than that the
    /// test failed — a fixture the test cannot see, or a fixture dependency
    /// whose lifetime cannot hold.
    ///
    /// Raises the run's exit code to
    /// [`ExitCode::UsageError`](crate::types::ExitCode::UsageError) without
    /// stopping the run, so every test still reports (#1761). Exit 4 is defined
    /// by the class of the error, not by when it is detected (ADR-0014).
    ///
    /// Always `false` on a `Failed` outcome: an assertion failure is what the
    /// code exists to be distinguished *from*.
    pub usage_error: bool,
}

impl FailureDiagnostic {
    /// Create an error diagnostic (no comparison fields).
    pub const fn error(
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
            comparison: None,
            usage_error: false,
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

    /// Sentinel that carries the vote, for an error whose class decides the exit code.
    ///
    /// [`sentinel`](Self::sentinel) writes `usage_error: false` and has callers
    /// throughout the crate, so it keeps its signature. A funnel that asks
    /// `is_usage_error` uses this instead, so that the class of the error
    /// reaches the exit code rather than the code of the step that caught it
    /// (#2185).
    pub fn sentinel_with_usage(message: String, usage_error: bool) -> Self {
        Self {
            usage_error,
            ..Self::sentinel(message)
        }
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
        tips: Option<Box<[usize]>>,
    },
    Failed(Box<FailureDiagnostic>),
    Error(Box<FailureDiagnostic>),
    Skipped {
        reason: String,
    },
    Warned {
        reason: String,
        tips: Option<Box<[usize]>>,
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
        /// The outcome kind from the initial failure (before retry passed).
        original: OutcomeKind,
    },
}

impl TestOutcome {
    /// True for outcomes that increment the failure counter and produce exit code 1.
    ///
    /// Includes strict `XPassed` (unexpected pass in strict mode is a CI failure).
    /// See also [`OutcomeKind::is_retryable_failure`] for retry-eligible failures.
    pub const fn is_hard_failure(&self) -> bool {
        matches!(
            self,
            Self::Failed(..)
                | Self::Error(..)
                | Self::Timeout { .. }
                | Self::XPassed { strict: true }
        )
    }

    /// Canonical lowercase status string. Matches the strings sent by worker subprocesses.
    pub const fn as_str(&self) -> &'static str {
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
            Self::Timeout { message } | Self::Flaky { message, .. } => message.as_str(),
            Self::Skipped { reason } | Self::Warned { reason, .. } | Self::XFailed { reason } => {
                reason.as_str()
            }
        };
        if s.is_empty() { None } else { Some(s) }
    }

    /// Returns the diagnostic payload for Failed and Error outcomes.
    pub fn diagnostic(&self) -> Option<&FailureDiagnostic> {
        match self {
            Self::Failed(d) | Self::Error(d) => Some(d),
            _ => None,
        }
    }

    /// Synthesise an error for a test that could not execute.
    pub fn error_sentinel(message: String) -> Self {
        Self::Error(Box::new(FailureDiagnostic::sentinel(message)))
    }

    /// Synthesise for an unresponsive worker subprocess.
    pub fn timed_out_sentinel(watchdog: Duration) -> (Self, DurationMs) {
        let outcome = Self::error_sentinel(format!(
            "Worker subprocess unresponsive after {}s",
            watchdog.as_secs()
        ));
        (outcome, DurationMs::new(watchdog.as_millis() as f64))
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
/// Used in [`TestTiming`](crate::types::TestTiming) and [`CacheEntry`](crate::cache) to avoid stringly-typed
/// comparisons. The `#[serde(rename_all = "snake_case")]` attribute ensures round-trip
/// compatibility with the JSON cache and worker protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum OutcomeKind {
    Passed = 0,
    Failed = 1,
    Error = 2,
    Skipped = 3,
    Warned = 4,
    #[serde(rename = "xfailed")]
    XFailed = 5,
    #[serde(rename = "xpassed")]
    XPassed = 6,
    Timeout = 7,
    Flaky = 8,
    /// Catch-all for unrecognised outcome strings from workers.
    #[serde(other)]
    Unknown = 9,
}

impl OutcomeKind {
    /// Number of variants in the enum (for array indexing).
    pub const COUNT: usize = 10;

    /// True for outcomes eligible for retry — definitive test failures only.
    ///
    /// Excludes `XPassed` (retrying won't change the outcome) and `Unknown`
    /// (protocol error, not a test failure).
    /// See also [`TestOutcome::is_hard_failure`] for exit-code failures.
    pub const fn is_retryable_failure(&self) -> bool {
        matches!(self, Self::Failed | Self::Error | Self::Timeout)
    }

    /// Canonical lowercase status string matching [`TestOutcome::as_str()`].
    pub const fn as_str(&self) -> &'static str {
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

#[cfg(test)]
use super::test_support::{ErrorOutcomeBuilder, FailedOutcomeBuilder};

#[cfg(test)]
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

/// Typed result from wire deserialization or serial execution.
///
/// Replaces the `(String, f64, TestOutcome)` tuple from `WireResult::into_outcome()`.
/// `WorkerResult` in the parallel path extends this with a `worker_id`.
#[derive(Debug)]
pub struct ResolvedOutcome {
    pub node_id: NodeId,
    pub duration_ms: DurationMs,
    pub outcome: TestOutcome,
}

#[cfg(test)]
mod outcome_tests {
    use super::*;
    use camino::Utf8PathBuf;

    #[test]
    fn sentinel_with_usage_carries_the_vote() {
        let voting = FailureDiagnostic::sentinel_with_usage("boom".to_string(), true);
        let silent = FailureDiagnostic::sentinel_with_usage("boom".to_string(), false);

        assert!(
            voting.usage_error,
            "a funnel that asked is_usage_error must be able to record a True \
             answer; without this the class of the error cannot reach the exit code"
        );
        assert!(
            !silent.usage_error,
            "a False answer must stay False, or every error a funnel catches \
             would raise the run to ExitCode::UsageError"
        );
        assert_eq!(
            voting.message, "boom",
            "the constructor must not alter the message it is given"
        );
    }

    #[test]
    fn test_outcome_passed_carries_tips() {
        let o = TestOutcome::Passed {
            tips: Some(vec![5, 10].into_boxed_slice()),
        };
        if let TestOutcome::Passed { tips } = o {
            assert_eq!(tips.as_deref(), Some([5, 10].as_slice()));
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
            frames: vec![],
            comparison: Some(ComparisonDetail {
                left: "0".to_string(),
                right: "1".to_string(),
                op: "==".to_string(),
                field_diffs: vec![],
            }),
            usage_error: false,
        }));
        if let TestOutcome::Failed(d) = o {
            assert_eq!(d.lineno, LineNo::new(7));
            let cmp = d.comparison.as_ref().expect("expected comparison");
            assert_eq!(cmp.left, "0");
            assert_eq!(cmp.right, "1");
            assert_eq!(cmp.op, "==");
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn test_outcome_warned_carries_reason() {
        let o = TestOutcome::Warned {
            reason: "DeprecationWarning: old api".to_string(),
            tips: Some(vec![3].into_boxed_slice()),
        };
        if let TestOutcome::Warned { reason, tips } = o {
            assert!(reason.contains("DeprecationWarning"));
            assert_eq!(tips.as_deref(), Some([3].as_slice()));
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
    fn test_outcome_timeout_stores_message() {
        let o = TestOutcome::Timeout {
            message: "Timed out after 3s".to_string(),
        };
        match o {
            TestOutcome::Timeout { message } => assert!(message.contains("3s")),
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn as_str_all_variants() {
        let cases: Vec<(TestOutcome, &str)> = vec![
            (TestOutcome::Passed { tips: None }, "passed"),
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
                    tips: None,
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
                    original: OutcomeKind::Failed,
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
            TestOutcome::Passed { tips: None },
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                tips: None,
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::XPassed { strict: false },
            TestOutcome::Flaky {
                message: String::new(),
                original: OutcomeKind::Failed,
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
            original: OutcomeKind::Failed,
        };
        assert_eq!(OutcomeKind::from(&outcome), OutcomeKind::Flaky);
    }

    #[test]
    fn test_flaky_outcome_kind_is_not_retryable_failure() {
        assert!(!OutcomeKind::Flaky.is_retryable_failure());
    }

    #[test]
    fn test_collect_error_import_display_shows_path_and_traceback() {
        let err = CollectError::ImportError {
            path: Utf8PathBuf::from("tests/bad.py"),
            message: "Traceback (most recent call last):\n  File \"tests/bad.py\", line 1\nModuleNotFoundError: No module named 'foo'".to_string(),
        };
        let s = format!("{err}");
        assert!(s.contains("tests/bad.py"), "path must appear in output");
        assert!(s.contains("Traceback"), "traceback must appear in output");
        assert!(s.contains("ModuleNotFoundError"), "error type must appear");
        assert!(
            !s.contains("TestResult("),
            "must not contain TestResult repr"
        );
        assert!(!s.contains("ImportError in"), "old format must be gone");
        assert!(s.contains('\n'), "output must contain real newlines");
    }

    #[test]
    fn test_collect_error_pyerror_display_shows_message_directly() {
        let err =
            CollectError::PyError("Failed to load a declaration file: SyntaxError".to_string());
        let s = format!("{err}");
        assert_eq!(s, "Failed to load a declaration file: SyntaxError");
        assert!(!s.starts_with("PyError:"), "PyError prefix must be gone");
    }

    #[test]
    fn outcome_failed_builder_defaults() {
        let outcome = TestOutcome::failed("oops").build();
        match outcome {
            TestOutcome::Failed(d) => {
                assert_eq!(d.message, "oops");
                assert_eq!(d.file.as_str(), "tests/test_foo.py");
                assert_eq!(d.lineno, LineNo::new(1));
                assert_eq!(d.source_line, "");
                assert!(d.frames.is_empty());
                assert!(d.comparison.is_none());
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
                let cmp = d.comparison.as_ref().expect("expected comparison");
                assert_eq!(cmp.left, "41");
                assert_eq!(cmp.op, "==");
                assert_eq!(cmp.right, "42");
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
        let worker_strings: HashSet<&str> = [
            "passed", "failed", "error", "skipped", "warned", "xfailed", "xpassed", "timeout",
        ]
        .into_iter()
        .collect();

        let outcome_strings: HashSet<&str> = [
            TestOutcome::Passed { tips: None },
            TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
            TestOutcome::Skipped {
                reason: String::new(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                tips: None,
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

    #[test]
    fn message_populated_variants() {
        let cases: Vec<(TestOutcome, Option<&str>)> = vec![
            (TestOutcome::Passed { tips: None }, None),
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
                    tips: None,
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
                    original: OutcomeKind::Failed,
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
                tips: None,
            },
            TestOutcome::XFailed {
                reason: String::new(),
            },
            TestOutcome::Timeout {
                message: String::new(),
            },
            TestOutcome::Flaky {
                message: String::new(),
                original: OutcomeKind::Failed,
            },
        ];
        for outcome in cases {
            assert!(
                outcome.message().is_none(),
                "expected None for empty message/reason in {outcome:?}"
            );
        }
    }

    #[test]
    fn failed_returns_some_with_all_fields() {
        let outcome = TestOutcome::Failed(Box::new(FailureDiagnostic {
            message: "expected 4".to_string(),
            file: Utf8PathBuf::from("tests/test_foo.py"),
            lineno: LineNo::new(8),
            source_line: "assert add(1, 2) == 4".to_string(),
            frames: vec![],
            comparison: Some(ComparisonDetail {
                left: "3".to_string(),
                right: "4".to_string(),
                op: "==".to_string(),
                field_diffs: vec![],
            }),
            usage_error: false,
        }));
        let diag = outcome.diagnostic().expect("Failed should return Some");
        assert_eq!(diag.file.as_str(), "tests/test_foo.py");
        assert_eq!(diag.lineno, LineNo::new(8));
        assert_eq!(diag.source_line, "assert add(1, 2) == 4");
        assert_eq!(diag.message, "expected 4");
        let cmp = diag.comparison.as_ref().expect("expected comparison");
        assert_eq!(cmp.left, "3");
        assert_eq!(cmp.right, "4");
        assert_eq!(cmp.op, "==");
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
        assert!(diag.comparison.is_none());
    }

    #[test]
    fn non_diagnostic_variants_return_none() {
        let cases: Vec<TestOutcome> = vec![
            TestOutcome::Passed { tips: None },
            TestOutcome::Skipped {
                reason: "not ready".to_string(),
            },
            TestOutcome::Warned {
                reason: String::new(),
                tips: None,
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
                original: OutcomeKind::Failed,
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
