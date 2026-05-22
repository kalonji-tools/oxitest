//! Strict-mode violation detection and reporting.
//!
//! When `--strict` is enabled, oxitest checks for code quality issues at
//! collection time: bare `assert` statements without messages, dict-based
//! parametrize (instead of frozen dataclasses), missing mark reasons, and
//! unregistered markers without descriptions.
//!
//! Violations are either warnings (enforce mode) or hard errors (abort mode)
//! depending on the [`StrictMode`](crate::config::StrictMode) setting.

use crate::bridge::{RawViolation, ViolationKind};
use crate::config::Config;
use crate::types::{NodeId, TestOutcome};

// ── Violation types ───────────────────────────────────────────────────────────

#[derive(Debug, PartialEq)]
pub enum PerTestViolation {
    BareAssert { node_id: NodeId, lines: Vec<usize> },
    DictParametrize { node_id: NodeId },
    MissingMarkReason { node_id: NodeId, mark_name: String },
}

impl PerTestViolation {
    pub fn node_id(&self) -> &NodeId {
        match self {
            Self::BareAssert { node_id, .. } => node_id,
            Self::DictParametrize { node_id } => node_id,
            Self::MissingMarkReason { node_id, .. } => node_id,
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum SuiteViolation {
    MarkerNoDescription { marker_name: String },
}

#[derive(Debug, PartialEq)]
pub enum StrictViolation {
    PerTest(PerTestViolation),
    Suite(SuiteViolation),
}

impl StrictViolation {
    pub fn node_id(&self) -> Option<&NodeId> {
        match self {
            Self::PerTest(v) => Some(v.node_id()),
            Self::Suite(_) => None,
        }
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

pub fn check_config(config: &Config) -> Vec<StrictViolation> {
    config
        .markers_without_description
        .iter()
        .map(|name| {
            StrictViolation::Suite(SuiteViolation::MarkerNoDescription {
                marker_name: name.clone(),
            })
        })
        .collect()
}

pub fn check_collected(raw: Vec<RawViolation>) -> Vec<StrictViolation> {
    raw.into_iter()
        .filter_map(|r| {
            let node_id = NodeId::from_raw(&r.node_id);
            match r.kind {
                ViolationKind::BareAssert => {
                    let lines: Vec<usize> = r
                        .detail
                        .split_whitespace()
                        .filter_map(|s| s.parse().ok())
                        .collect();
                    Some(StrictViolation::PerTest(PerTestViolation::BareAssert {
                        node_id,
                        lines,
                    }))
                }
                ViolationKind::DictParametrize => Some(StrictViolation::PerTest(
                    PerTestViolation::DictParametrize { node_id },
                )),
                ViolationKind::MissingMarkReason => Some(StrictViolation::PerTest(
                    PerTestViolation::MissingMarkReason {
                        node_id,
                        mark_name: r.detail,
                    },
                )),
                ViolationKind::Unknown => None,
            }
        })
        .collect()
}

pub fn suite_level(violations: &[StrictViolation]) -> Vec<&SuiteViolation> {
    violations
        .iter()
        .filter_map(|v| match v {
            StrictViolation::Suite(sv) => Some(sv),
            StrictViolation::PerTest(_) => None,
        })
        .collect()
}

impl std::fmt::Display for PerTestViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::BareAssert { node_id, lines } => {
                if lines.is_empty() {
                    write!(f, "{:<60}  bare-assert", node_id.as_ref())
                } else {
                    let nums = lines
                        .iter()
                        .map(|n| n.to_string())
                        .collect::<Vec<_>>()
                        .join(", ");
                    let label = if lines.len() == 1 { "line" } else { "lines" };
                    write!(
                        f,
                        "{:<60}  bare-assert        {} {}",
                        node_id.as_ref(),
                        label,
                        nums
                    )
                }
            }
            Self::DictParametrize { node_id } => {
                write!(f, "{:<60}  dict-parametrize", node_id.as_ref())
            }
            Self::MissingMarkReason { node_id, mark_name } => {
                write!(
                    f,
                    "{:<60}  missing-mark-reason   {}",
                    node_id.as_ref(),
                    mark_name
                )
            }
        }
    }
}

impl std::fmt::Display for SuiteViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MarkerNoDescription { marker_name } => {
                write!(f, "markers[\"{}\"]   no description", marker_name)
            }
        }
    }
}

impl std::fmt::Display for StrictViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::PerTest(v) => v.fmt(f),
            Self::Suite(v) => v.fmt(f),
        }
    }
}

pub fn format_violation_line(v: &StrictViolation) -> String {
    v.to_string()
}

/// Returns a `TestOutcome::Error` for a per-test strict violation.
///
/// Accepts only [`PerTestViolation`], so suite-level violations are excluded
/// at compile time — no runtime `unreachable!()` needed.
pub fn per_test_error(v: &PerTestViolation) -> TestOutcome {
    let message = match v {
        PerTestViolation::BareAssert { lines, .. } => {
            if lines.is_empty() {
                "strict: bare assert (no line info)".to_string()
            } else {
                let nums = lines
                    .iter()
                    .map(|n| n.to_string())
                    .collect::<Vec<_>>()
                    .join(", ");
                let label = if lines.len() == 1 { "line" } else { "lines" };
                format!("strict: bare assert on {} {}", label, nums)
            }
        }
        PerTestViolation::DictParametrize { .. } => {
            "strict: use a frozen dataclass instead of dict for parametrize cases".to_string()
        }
        PerTestViolation::MissingMarkReason { mark_name, .. } => {
            format!("strict: @mark.{} requires reason=", mark_name)
        }
    };
    TestOutcome::Error {
        message,
        file: String::new(),
        lineno: 0,
        source_line: String::new(),
        frames: vec![],
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::types::NodeId;

    fn config_with_markers(raw: &[&str]) -> Config {
        let toml = format!(
            "[tool.oxitest]\nmarkers = [{}]\n",
            raw.iter()
                .map(|s| format!("\"{}\"", s))
                .collect::<Vec<_>>()
                .join(", ")
        );
        Config::from_str(&toml).unwrap()
    }

    #[test]
    fn test_check_config_no_violations_when_all_markers_have_description() {
        let cfg = config_with_markers(&["slow: marks slow tests", "db: hits db"]);
        assert!(check_config(&cfg).is_empty());
    }

    #[test]
    fn test_check_config_reports_marker_without_description() {
        let cfg = config_with_markers(&["slow: marks slow", "db"]);
        let violations = check_config(&cfg);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::Suite(SuiteViolation::MarkerNoDescription { marker_name })
            if marker_name == "db"
        ));
    }

    #[test]
    fn test_check_collected_bare_assert() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_add".to_string(),
            kind: ViolationKind::BareAssert,
            detail: "12 18".to_string(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::BareAssert { lines, .. })
            if *lines == vec![12usize, 18]
        ));
    }

    #[test]
    fn test_check_collected_dict_parametrize() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_mul".to_string(),
            kind: ViolationKind::DictParametrize,
            detail: String::new(),
        }];
        let violations = check_collected(raw);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::DictParametrize { .. })
        ));
    }

    #[test]
    fn test_check_collected_missing_mark_reason() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_skip".to_string(),
            kind: ViolationKind::MissingMarkReason,
            detail: "skip".to_string(),
        }];
        let violations = check_collected(raw);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::MissingMarkReason { mark_name, .. })
            if mark_name == "skip"
        ));
    }

    #[test]
    fn test_check_collected_unknown_kind_ignored() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_x".to_string(),
            kind: ViolationKind::Unknown,
            detail: String::new(),
        }];
        let violations = check_collected(raw);
        assert!(violations.is_empty());
    }

    #[test]
    fn test_suite_level_returns_only_marker_no_description() {
        let v1 = StrictViolation::Suite(SuiteViolation::MarkerNoDescription {
            marker_name: "db".to_string(),
        });
        let v2 = StrictViolation::PerTest(PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_x"),
            lines: vec![5],
        });
        let violations = vec![v1, v2];
        let suite = suite_level(&violations);
        assert_eq!(suite.len(), 1);
        assert!(matches!(
            suite[0],
            SuiteViolation::MarkerNoDescription { .. }
        ));
    }

    #[test]
    fn test_format_violation_line_bare_assert() {
        let v = StrictViolation::PerTest(PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_add"),
            lines: vec![12, 18],
        });
        let line = format_violation_line(&v);
        assert!(line.contains("bare-assert"));
        assert!(line.contains("12"));
        assert!(line.contains("18"));
    }

    #[test]
    fn test_format_violation_line_marker_no_description() {
        let v = StrictViolation::Suite(SuiteViolation::MarkerNoDescription {
            marker_name: "db".to_string(),
        });
        let line = format_violation_line(&v);
        assert!(line.contains("db"));
        assert!(line.contains("no description"));
    }

    #[test]
    fn test_per_test_error_returns_error_outcome() {
        let v = PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_x"),
            lines: vec![5],
        };
        let outcome = per_test_error(&v);
        assert!(
            matches!(outcome, TestOutcome::Error { message, .. } if message.contains("strict"))
        );
    }

    #[test]
    fn test_node_id_returns_none_for_suite_level() {
        let v = StrictViolation::Suite(SuiteViolation::MarkerNoDescription {
            marker_name: "db".to_string(),
        });
        assert!(v.node_id().is_none());
    }

    #[test]
    fn test_check_collected_bare_assert_empty_detail() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_x".to_string(),
            kind: ViolationKind::BareAssert,
            detail: String::new(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::BareAssert { lines, .. }) if lines.is_empty()
        ));
    }

    #[test]
    fn test_per_test_error_bare_assert_empty_lines_no_trailing_space() {
        let v = PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_x"),
            lines: vec![],
        };
        let outcome = per_test_error(&v);
        if let TestOutcome::Error { message, .. } = outcome {
            assert!(
                !message.ends_with(' '),
                "message must not have trailing space: {:?}",
                message
            );
            assert!(
                message.contains("strict"),
                "message must contain 'strict': {:?}",
                message
            );
        } else {
            panic!("expected Error outcome");
        }
    }

    #[test]
    fn test_format_violation_line_bare_assert_empty_lines_no_trailing_space() {
        let v = StrictViolation::PerTest(PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_x"),
            lines: vec![],
        });
        let line = format_violation_line(&v);
        assert!(
            !line.ends_with(' '),
            "line must not have trailing space: {:?}",
            line
        );
        assert!(
            line.contains("bare-assert"),
            "line must contain 'bare-assert': {:?}",
            line
        );
    }
}
