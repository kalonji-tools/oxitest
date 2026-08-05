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

/// Filter `no_message_lines` into tips (positive values only).
///
/// Converts `Vec<i64>` (from JSON wire protocol) to `Option<Box<[usize]>>`,
/// dropping non-positive values. The PyO3 path extracts `Vec<usize>` directly
/// and needs no filtering — [`RawOutcome::into_test_outcome`] boxes it
/// directly (empty becomes `None`).
pub(super) fn filter_tips(lines: Vec<i64>) -> Vec<usize> {
    lines
        .iter()
        .filter(|&&n| n > 0)
        .map(|&n| usize::try_from(n).unwrap_or(0))
        .collect()
}

/// Shared construction of `FailureDiagnostic` from raw fields.
///
/// Called by [`RawOutcome::into_test_outcome()`] for the Failed and Error
/// variants — the single conversion path shared by both the JSON worker
/// and PyO3 bridge callers.
const fn build_diagnostic(
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

/// Unified intermediate outcome produced by both the JSON (wire) and PyO3
/// (bridge) paths before final conversion to [`TestOutcome`](types::TestOutcome).
///
/// Each variant carries exactly the fields needed for that outcome kind, using
/// Rust-native types (`usize`, `Vec<Frame>`) — all wire/PyO3 type conversions
/// happen *before* constructing a `RawOutcome`.
pub enum RawOutcome {
    Passed {
        no_message_lines: Vec<usize>,
    },
    Failed {
        message: String,
        file: Utf8PathBuf,
        lineno: LineNo,
        source_line: String,
        frames: Vec<Frame>,
        comparison: ComparisonDetail,
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
}

impl RawOutcome {
    /// Convert into a typed [`TestOutcome`](types::TestOutcome).
    ///
    /// Single conversion path shared by both the JSON worker and PyO3 bridge
    /// callers. Uses [`build_diagnostic`] for Failed/Error variants and converts
    /// `no_message_lines` into boxed-slice tips.
    pub(crate) fn into_test_outcome(self) -> types::TestOutcome {
        match self {
            Self::Passed { no_message_lines } => {
                let tips = if no_message_lines.is_empty() {
                    None
                } else {
                    Some(no_message_lines.into_boxed_slice())
                };
                types::TestOutcome::Passed { tips }
            }
            Self::Failed {
                message,
                file,
                lineno,
                source_line,
                frames,
                comparison,
            } => types::TestOutcome::Failed(Box::new(build_diagnostic(
                message,
                file,
                lineno,
                source_line,
                frames,
                Some(comparison),
            ))),
            Self::Error {
                message,
                file,
                lineno,
                source_line,
                frames,
            } => types::TestOutcome::Error(Box::new(build_diagnostic(
                message,
                file,
                lineno,
                source_line,
                frames,
                None,
            ))),
            Self::Skipped { reason } => types::TestOutcome::Skipped { reason },
            Self::Warned {
                reason,
                no_message_lines,
            } => {
                let tips = if no_message_lines.is_empty() {
                    None
                } else {
                    Some(no_message_lines.into_boxed_slice())
                };
                types::TestOutcome::Warned { reason, tips }
            }
            Self::XFailed { reason } => types::TestOutcome::XFailed { reason },
            Self::XPassed { strict } => types::TestOutcome::XPassed { strict },
            Self::Timeout { message } => types::TestOutcome::Timeout { message },
        }
    }
}

impl WireResult {
    /// Convert the typed wire representation into a [`ResolvedOutcome`](types::ResolvedOutcome).
    ///
    /// Consumes self — `WireResult` is a transient deserialization target.
    /// Each variant is destructured, pre-converted to Rust-native types via
    /// `wire_lineno()` / `wire_frames()` / `filter_tips()`, then funnelled
    /// through [`RawOutcome::into_test_outcome()`].
    pub(crate) fn into_outcome(self) -> types::ResolvedOutcome {
        match self {
            Self::Passed {
                node_id,
                duration_ms,
                no_message_lines,
                ..
            } => {
                let raw = RawOutcome::Passed {
                    no_message_lines: filter_tips(no_message_lines),
                };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
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
            } => {
                let raw = RawOutcome::Failed {
                    message,
                    file: Utf8PathBuf::from(file),
                    lineno: wire_lineno(lineno),
                    source_line,
                    frames: wire_frames(frames),
                    comparison: ComparisonDetail {
                        left,
                        right,
                        op,
                        field_diffs,
                    },
                };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::Error {
                node_id,
                duration_ms,
                message,
                file,
                lineno,
                source_line,
                frames,
                ..
            } => {
                let raw = RawOutcome::Error {
                    message,
                    file: Utf8PathBuf::from(file),
                    lineno: wire_lineno(lineno),
                    source_line,
                    frames: wire_frames(frames),
                };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::Skipped {
                node_id,
                duration_ms,
                message,
                ..
            } => {
                let raw = RawOutcome::Skipped { reason: message };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::Warned {
                node_id,
                duration_ms,
                message,
                no_message_lines,
                ..
            } => {
                let raw = RawOutcome::Warned {
                    reason: message,
                    no_message_lines: filter_tips(no_message_lines),
                };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::XFailed {
                node_id,
                duration_ms,
                message,
                ..
            } => {
                let raw = RawOutcome::XFailed { reason: message };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::XPassed {
                node_id,
                duration_ms,
                strict,
                ..
            } => {
                let raw = RawOutcome::XPassed { strict };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
            Self::Timeout {
                node_id,
                duration_ms,
                message,
                ..
            } => {
                let raw = RawOutcome::Timeout { message };
                types::ResolvedOutcome {
                    node_id: types::NodeId::from_raw(&node_id),
                    duration_ms: types::DurationMs::new(duration_ms),
                    outcome: raw.into_test_outcome(),
                }
            }
        }
    }
}
