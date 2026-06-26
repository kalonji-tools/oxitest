//! Conversion from wire types to resolved outcomes.

use super::wire::{RawFrame, WireResult};
use crate::types::{self, ComparisonDetail, FailureDiagnostic, Frame, LineNo};
use camino::Utf8PathBuf;

fn wire_lineno(lineno: Option<u64>) -> LineNo {
    LineNo::new(lineno.map_or(0, |n| usize::try_from(n).unwrap_or(0)))
}

fn wire_frames(raw: Vec<RawFrame>) -> Vec<Frame> {
    raw.into_iter().map(Into::into).collect()
}

/// Raw diagnostic fields from the wire, before conversion to typed values.
struct WireDiagnostic {
    message: String,
    file: String,
    lineno: Option<u64>,
    source_line: String,
    frames: Vec<RawFrame>,
    comparison: Option<ComparisonDetail>,
}

fn diagnostic_outcome(
    node_id: String,
    duration_ms: f64,
    diag: WireDiagnostic,
    make_outcome: fn(Box<FailureDiagnostic>) -> types::TestOutcome,
) -> types::ResolvedOutcome {
    types::ResolvedOutcome {
        node_id: types::NodeId::from_raw(&node_id),
        duration_ms: types::DurationMs::new(duration_ms),
        outcome: make_outcome(Box::new(build_diagnostic(
            diag.message,
            Utf8PathBuf::from(diag.file),
            wire_lineno(diag.lineno),
            diag.source_line,
            wire_frames(diag.frames),
            diag.comparison,
        ))),
    }
}

/// Filter no_message_lines into tips (positive values only).
pub(super) fn filter_tips(lines: Vec<i64>) -> Option<Box<[usize]>> {
    let filtered: Vec<usize> = lines
        .iter()
        .filter(|&&n| n > 0)
        .map(|&n| usize::try_from(n).unwrap_or(0))
        .collect();
    if filtered.is_empty() {
        None
    } else {
        Some(filtered.into_boxed_slice())
    }
}

/// Shared construction of `FailureDiagnostic` from raw fields.
///
/// Used by both the JSON worker path (`into_outcome`) and the PyO3 bridge
/// path (`convert_test_result`) to prevent drift between the two.
pub(crate) fn build_diagnostic(
    message: String,
    file: Utf8PathBuf,
    lineno: LineNo,
    source_line: String,
    frames: Vec<Frame>,
    comparison: Option<ComparisonDetail>,
) -> FailureDiagnostic {
    FailureDiagnostic {
        message,
        file,
        lineno,
        source_line,
        frames,
        comparison,
    }
}

impl WireResult {
    /// Convert the typed wire representation into a [`ResolvedOutcome`](types::ResolvedOutcome).
    ///
    /// Consumes self — `WireResult` is a transient deserialization target.
    pub(crate) fn into_outcome(self) -> types::ResolvedOutcome {
        match self {
            Self::Passed {
                node_id,
                duration_ms,
                no_message_lines,
                ..
            } => {
                let tips = filter_tips(no_message_lines);
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: types::TestOutcome::Passed { tips },
                }
            }
            Self::Failed {
                node_id,
                duration_ms,
                message,
                file,
                lineno,
                source_line,
                frames,
                left,
                right,
                op,
                field_diffs,
                ..
            } => diagnostic_outcome(
                node_id,
                duration_ms,
                WireDiagnostic {
                    message,
                    file,
                    lineno,
                    source_line,
                    frames,
                    comparison: Some(ComparisonDetail {
                        left,
                        right,
                        op,
                        field_diffs,
                    }),
                },
                types::TestOutcome::Failed,
            ),
            Self::Error {
                node_id,
                duration_ms,
                message,
                file,
                lineno,
                source_line,
                frames,
                ..
            } => diagnostic_outcome(
                node_id,
                duration_ms,
                WireDiagnostic {
                    message,
                    file,
                    lineno,
                    source_line,
                    frames,
                    comparison: None,
                },
                types::TestOutcome::Error,
            ),
            Self::Skipped {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::Skipped { reason: message },
            },
            Self::Warned {
                node_id,
                duration_ms,
                message,
                no_message_lines,
                ..
            } => {
                let tips = filter_tips(no_message_lines);
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: types::TestOutcome::Warned {
                        reason: message,
                        tips,
                    },
                }
            }
            Self::XFailed {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::XFailed { reason: message },
            },
            Self::XPassed {
                node_id,
                duration_ms,
                strict,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::XPassed { strict },
            },
            Self::Timeout {
                node_id,
                duration_ms,
                message,
                ..
            } => types::ResolvedOutcome {
                node_id: types::NodeId::from_raw(&node_id),
                duration_ms: types::DurationMs::new(duration_ms),
                outcome: types::TestOutcome::Timeout { message },
            },
        }
    }
}
