//! Wire protocol types for the worker ↔ coordinator JSON channel.

use crate::types::{FieldDiff, Frame, LineNo, LocalVar};
use camino::Utf8PathBuf;

/// Wire protocol version for the worker ↔ coordinator JSON channel.
///
/// Bump when adding, removing, or changing fields in [`WorkerTask`] or
/// [`WireResult`]. The coordinator warns on version mismatch.
///
/// Stays `pub(crate)` against `clippy::redundant_pub_crate`, which would widen it
/// to `pub` like the rest of the crate. `scripts/check_bridge_sync.py:341` matches
/// this declaration with `r"pub\(crate\)\s+const\s+PROTOCOL_VERSION…"` to check it
/// against `result.py`, and `python/tests/test_check_protocol_version.py` rewrites
/// it with the same literal. Widening it makes both silently find nothing — the
/// prek `bridge-sync` hook reports "`PROTOCOL_VERSION` not found", and the test's own
/// assertion message says every downstream check becomes meaningless. The coupling
/// is a Python script parsing Rust source as text; the qualifier is load-bearing.
#[allow(clippy::redundant_pub_crate)]
pub(crate) const PROTOCOL_VERSION: u32 = 8;

/// A JSON task sent to a worker subprocess over stdin.
///
/// One task describes a group of modules: the module files to import, their
/// test items and an optional per-test timeout.
/// The worker deserializes this from a single JSON line.
///
/// The coordinator sends exactly one module per task today; #1710 makes a
/// package's whole subtree a single task so a package-lifetime fixture can be
/// instantiated exactly once per run.
#[derive(serde::Serialize)]
pub struct WorkerTask<'a> {
    /// Lets a stale worker reject a task it cannot parse instead of failing
    /// with a `KeyError` deep inside `run()` — which would emit no result line,
    /// so the coordinator's result-side version warning never fires.
    pub protocol_version: u32,
    pub modules: Vec<WorkerTaskModule<'a>>,
    /// `[{"module": ..., "anchor": ...}]` — see `types::FixtureModule` (#1732).
    pub fixture_modules: &'a serde_json::value::RawValue,
    /// `{"modules": [...], "settings": {...}}` — what a worker needs to
    /// activate the run's plugins for itself.
    ///
    /// Workers rebuild their own `FixtureSession` and never inherit the
    /// coordinator's, so before this field a worker had **no plugins at all**:
    /// both `FixtureProvider` fixtures and plugin `__fixtures__.py`
    /// declarations were invisible under `-n`, while passing serially (#1717).
    pub plugins: &'a serde_json::value::RawValue,
    pub timeout_secs: Option<u64>,
    pub keep_tmp: &'a str,
    /// Project rootdir, appended to the worker's `sys.path` so test modules can
    /// import sibling utility modules (#1780). Always sent — the Python side
    /// tolerates its absence only for in-process unit tests.
    pub rootdir: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_locals: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_internals: Option<bool>,
}

/// One module and its test items within a [`WorkerTask`].
///
/// Items nest under their module rather than carrying a `module_path` each:
/// a flat list would make item *ordering* load-bearing, since the worker would
/// have to detect module transitions to know where `end_module` fires.
#[derive(serde::Serialize)]
pub struct WorkerTaskModule<'a> {
    pub module_path: &'a str,
    pub items: Vec<WorkerTaskItem<'a>>,
}

/// One test item within a [`WorkerTaskModule`].
#[derive(serde::Serialize)]
pub struct WorkerTaskItem<'a> {
    pub fn_name: &'a str,
    pub param_id: Option<&'a str>,
    pub node_id: &'a str,
    pub markers: Vec<&'a str>,
}

/// Unified intermediate frame type used by both the JSON worker path
/// (serde deserialize) and the PyO3 bridge path (`FromPyObject` impl in bridge.rs).
#[derive(Debug, Clone, serde::Deserialize)]
pub struct RawFrame {
    pub file: String,
    pub lineno: u64,
    pub name: String,
    pub line: String,
    #[serde(default)]
    pub locals: Vec<LocalVar>,
}

impl From<RawFrame> for Frame {
    fn from(f: RawFrame) -> Self {
        Self {
            file: Utf8PathBuf::from(f.file),
            lineno: LineNo::new(usize::try_from(f.lineno).unwrap_or(0)),
            name: f.name,
            line: f.line,
            locals: f.locals,
        }
    }
}

/// Deserialized JSON result for a single test, written by a worker subprocess.
///
/// Workers print one `WireResult` line per test to stdout. The `outcome` field
/// drives serde variant selection via `#[serde(tag = "outcome")]`. Each variant
/// carries only the fields relevant to that outcome kind.
///
/// Use [`into_outcome`](WireResult::into_outcome) to convert into a
/// typed [`ResolvedOutcome`](crate::types::ResolvedOutcome).
#[derive(Debug, serde::Deserialize)]
#[serde(tag = "outcome")]
pub enum WireResult {
    #[serde(rename = "passed")]
    Passed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        no_message_lines: Vec<i64>,
    },
    #[serde(rename = "failed")]
    Failed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        file: String,
        #[serde(default)]
        lineno: Option<u64>,
        #[serde(default)]
        source_line: String,
        #[serde(default)]
        frames: Vec<RawFrame>,
        #[serde(default)]
        left: String,
        #[serde(default)]
        right: String,
        #[serde(default)]
        op: String,
        #[serde(default)]
        field_diffs: Vec<FieldDiff>,
    },
    #[serde(rename = "error")]
    Error {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        file: String,
        #[serde(default)]
        lineno: Option<u64>,
        #[serde(default)]
        source_line: String,
        #[serde(default)]
        frames: Vec<RawFrame>,
        /// The suite is wired wrong rather than the test having failed (#1761).
        ///
        /// Absent on any worker built before #1761. `default` is what makes a
        /// version skew fail to a *failure*: no key means `false`, so the run
        /// stays at exit 1 rather than dropping to 0. A fourth
        /// `DiagnosticSeverity` would have failed the other way — `from_wire`
        /// maps an unknown severity to a notice, and a run of notices exits 0.
        #[serde(default)]
        usage_error: bool,
    },
    #[serde(rename = "skipped")]
    Skipped {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
    #[serde(rename = "warned")]
    Warned {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
        #[serde(default)]
        no_message_lines: Vec<i64>,
    },
    #[serde(rename = "xfailed")]
    XFailed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
    #[serde(rename = "xpassed")]
    XPassed {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        strict: bool,
    },
    #[serde(rename = "timeout")]
    Timeout {
        node_id: String,
        duration_ms: f64,
        #[serde(default)]
        protocol_version: u32,
        #[serde(default)]
        message: String,
    },
}

/// Minimal deserialization target for unknown/malformed wire results.
///
/// Used by the drain loop to extract `node_id` and `duration_ms` from results
/// that fail full `WireResult` deserialization (e.g., unknown outcome strings).
#[derive(serde::Deserialize)]
pub struct WireMinimal {
    pub node_id: String,
    pub duration_ms: f64,
}

/// Envelope for dispatching worker stdout lines by message type.
///
/// Deserialized first to determine which full type to parse.
/// Missing `type` field defaults to "result" for backwards compatibility.
#[derive(serde::Deserialize)]
pub struct WireEnvelope {
    #[serde(rename = "type", default = "default_result_type")]
    pub(crate) msg_type: String,
}

fn default_result_type() -> String {
    "result".to_string()
}

/// A diagnostic message from a worker subprocess.
///
/// Every field is consumed by `parallel::drain::forward_diagnostic`, which
/// turns it into a `DiagnosticEntry` for the reporter (#1840). The follow-up
/// PR the old `dead_code` expectation was waiting for is that one.
#[derive(serde::Deserialize)]
pub struct WireDiagnostic {
    pub severity: String,
    pub context: String,
    pub message: String,
    #[serde(default)]
    pub file: String,
    #[serde(default)]
    pub lineno: u32,
}

/// A developer trace from a worker subprocess.
#[derive(serde::Deserialize)]
pub struct WireTrace {
    pub level: String,
    pub module: String,
    pub message: String,
}

impl WireResult {
    /// Extract the protocol version from any variant.
    pub(crate) const fn protocol_version(&self) -> u32 {
        match self {
            Self::Passed {
                protocol_version, ..
            }
            | Self::Failed {
                protocol_version, ..
            }
            | Self::Error {
                protocol_version, ..
            }
            | Self::Skipped {
                protocol_version, ..
            }
            | Self::Warned {
                protocol_version, ..
            }
            | Self::XFailed {
                protocol_version, ..
            }
            | Self::XPassed {
                protocol_version, ..
            }
            | Self::Timeout {
                protocol_version, ..
            } => *protocol_version,
        }
    }
}
