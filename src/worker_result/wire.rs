//! Wire protocol types for the worker ↔ coordinator JSON channel.

use crate::types::{FieldDiff, Frame, LineNo, LocalVar};
use camino::Utf8PathBuf;

/// Wire protocol version for the worker ↔ coordinator JSON channel.
///
/// Bump when adding, removing, or changing fields in [`WorkerTask`] or
/// [`WireResult`]. The coordinator warns on version mismatch.
pub(crate) const PROTOCOL_VERSION: u32 = 2;

/// A JSON task sent to a worker subprocess over stdin.
///
/// One task describes a single module group: the module file to import, the
/// list of test items to run, the conftest files to load, and an optional
/// per-test timeout.  The worker deserializes this from a single JSON line.
#[derive(serde::Serialize)]
pub(crate) struct WorkerTask<'a> {
    pub module_path: &'a str,
    pub items: Vec<WorkerTaskItem<'a>>,
    pub conftest_paths: &'a serde_json::value::RawValue,
    pub timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub keep_tmp: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_locals: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_internals: Option<bool>,
}

/// One test item within a [`WorkerTask`].
#[derive(serde::Serialize)]
pub(crate) struct WorkerTaskItem<'a> {
    pub fn_name: &'a str,
    pub param_id: &'a str,
    pub node_id: &'a str,
    pub markers: Vec<&'a str>,
}

/// Unified intermediate frame type used by both the JSON worker path
/// (serde deserialize) and the PyO3 bridge path (FromPyObject impl in bridge.rs).
#[derive(Debug, Clone, serde::Deserialize)]
pub(crate) struct RawFrame {
    pub file: String,
    pub lineno: u64,
    pub name: String,
    pub line: String,
    #[serde(default)]
    pub locals: Vec<LocalVar>,
}

impl From<RawFrame> for Frame {
    fn from(f: RawFrame) -> Self {
        Frame {
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
pub(crate) enum WireResult {
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
/// Used by the drain loop to extract node_id and duration_ms from results
/// that fail full `WireResult` deserialization (e.g., unknown outcome strings).
#[derive(serde::Deserialize)]
pub(crate) struct WireMinimal {
    pub node_id: String,
    pub duration_ms: f64,
}

impl WireResult {
    /// Extract the protocol version from any variant.
    pub(crate) fn protocol_version(&self) -> u32 {
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
