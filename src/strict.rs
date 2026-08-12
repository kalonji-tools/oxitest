//! Strict-mode violation detection and reporting.
//!
//! When `--strict` is enabled, oxitest checks for code quality issues at
//! collection time: bare `assert` statements without messages, dict-based
//! parametrize (instead of frozen dataclasses), missing mark reasons,
//! missing fixture return annotations, unused fixtures, and unregistered
//! markers without descriptions.
//!
//! Violations are either warnings (enforce mode) or hard errors (abort mode)
//! depending on the [`StrictMode`](crate::config::StrictMode) setting.

use crate::bridge::{RawViolation, ViolationKind};
use crate::config::Config;
use crate::types::{NodeId, TestOutcome};

/// Render a line list as `line 3` or `lines 3, 5`.
///
/// Four violation renderings need this — two for `BareAssert` and two for
/// `TestReturnsValue` — and they were four verbatim copies (#2067 stage 8).
fn lines_phrase(lines: &[usize]) -> String {
    let nums = lines
        .iter()
        .map(std::string::ToString::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    let label = if lines.len() == 1 { "line" } else { "lines" };
    format!("{label} {nums}")
}

// ── Violation types ───────────────────────────────────────────────────────────

/// A strict-mode violation that is tied to a specific test item.
///
/// Reported alongside the test result when `strict = "enforce"`, or causes the
/// run to abort before execution when `strict = "abort"`.
#[derive(Debug, PartialEq)]
pub enum PerTestViolation {
    BareAssert {
        node_id: NodeId,
        lines: Vec<usize>,
    },
    BroadFixtureType {
        node_id: NodeId,
        detail: String,
    },
    DictParametrize {
        node_id: NodeId,
    },
    InvalidModuleMark {
        node_id: NodeId,
        detail: String,
    },
    MissingMarkReason {
        node_id: NodeId,
        mark_name: String,
    },
    ModuleLevelDef {
        node_id: NodeId,
        fn_name: String,
        lineno: usize,
    },
    MissingReturnAnnotation {
        node_id: NodeId,
        fixture_name: String,
    },
    SingleCaseParametrize {
        node_id: NodeId,
    },
    TestReturnsValue {
        node_id: NodeId,
        lines: Vec<usize>,
    },
    UnusedFixture {
        node_id: NodeId,
        fixture_name: String,
    },
}

impl PerTestViolation {
    pub const fn node_id(&self) -> &NodeId {
        match self {
            Self::BareAssert { node_id, .. }
            | Self::BroadFixtureType { node_id, .. }
            | Self::DictParametrize { node_id }
            | Self::InvalidModuleMark { node_id, .. }
            | Self::MissingMarkReason { node_id, .. }
            | Self::MissingReturnAnnotation { node_id, .. }
            | Self::ModuleLevelDef { node_id, .. }
            | Self::SingleCaseParametrize { node_id }
            | Self::TestReturnsValue { node_id, .. }
            | Self::UnusedFixture { node_id, .. } => node_id,
        }
    }
}

/// A strict-mode violation that applies to the entire suite, not a single test.
///
/// Currently only `MarkerNoDescription` — a marker registered without a description
/// string in `[tool.oxitest] markers`. Suite violations are shown in the `STRICT`
/// section after all tests finish, not inlined into test output.
#[derive(Debug, PartialEq)]
pub enum SuiteViolation {
    MarkerNoDescription { marker_name: String },
}

/// A strict-mode violation, either per-test or suite-level.
///
/// The split into [`PerTestViolation`] and [`SuiteViolation`] eliminates the
/// `unreachable!()` that would be needed if both were flattened into a single enum.
/// Use [`StrictViolation::node_id`] to distinguish at call sites.
#[derive(Debug, PartialEq)]
pub enum StrictViolation {
    PerTest(PerTestViolation),
    Suite(SuiteViolation),
}

impl StrictViolation {
    pub const fn node_id(&self) -> Option<&NodeId> {
        match self {
            Self::PerTest(v) => Some(v.node_id()),
            Self::Suite(_) => None,
        }
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Check for suite-level violations derivable from the config alone.
///
/// Currently produces one [`SuiteViolation::MarkerNoDescription`] per marker in
/// `config.markers.markers_without_description`. Called before collection begins.
pub fn check_config(config: &Config) -> Vec<StrictViolation> {
    config
        .markers
        .markers_without_description
        .iter()
        .map(|name| {
            StrictViolation::Suite(SuiteViolation::MarkerNoDescription {
                marker_name: name.clone(),
            })
        })
        .collect()
}

/// Convert raw violation data from Python collection into typed [`StrictViolation`]s.
///
/// Called after `collect_module` returns with `collect_violations = true`. Each
/// [`RawViolation`] carries a `ViolationKind` enum and a `detail` string whose
/// format depends on the kind (e.g. space-separated line numbers for `BareAssert`).
/// Unknown kinds are silently discarded.
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
                ViolationKind::BroadFixtureType => Some(StrictViolation::PerTest(
                    PerTestViolation::BroadFixtureType {
                        node_id,
                        detail: r.detail,
                    },
                )),
                ViolationKind::DictParametrize => Some(StrictViolation::PerTest(
                    PerTestViolation::DictParametrize { node_id },
                )),
                ViolationKind::InvalidModuleMark => Some(StrictViolation::PerTest(
                    PerTestViolation::InvalidModuleMark {
                        node_id,
                        detail: r.detail,
                    },
                )),
                ViolationKind::ModuleLevelDef => {
                    // detail is "<name> <lineno>" -- split from the right, the
                    // line number is the only field that cannot contain a space.
                    let (fn_name, lineno) = r.detail.rsplit_once(' ')?;
                    Some(StrictViolation::PerTest(PerTestViolation::ModuleLevelDef {
                        node_id,
                        fn_name: fn_name.to_string(),
                        lineno: lineno.parse().ok()?,
                    }))
                }
                ViolationKind::MissingMarkReason => Some(StrictViolation::PerTest(
                    PerTestViolation::MissingMarkReason {
                        node_id,
                        mark_name: r.detail,
                    },
                )),
                ViolationKind::MissingReturnAnnotation => Some(StrictViolation::PerTest(
                    PerTestViolation::MissingReturnAnnotation {
                        node_id,
                        fixture_name: r.detail,
                    },
                )),
                ViolationKind::SingleCaseParametrize => Some(StrictViolation::PerTest(
                    PerTestViolation::SingleCaseParametrize { node_id },
                )),
                ViolationKind::TestReturnsValue => {
                    // Same `detail` shape as BareAssert: space-separated line
                    // numbers, one per offending `return`.
                    let lines: Vec<usize> = r
                        .detail
                        .split_whitespace()
                        .filter_map(|s| s.parse().ok())
                        .collect();
                    Some(StrictViolation::PerTest(
                        PerTestViolation::TestReturnsValue { node_id, lines },
                    ))
                }
                ViolationKind::UnusedFixture => {
                    Some(StrictViolation::PerTest(PerTestViolation::UnusedFixture {
                        node_id,
                        fixture_name: r.detail,
                    }))
                }
                ViolationKind::Unknown => None,
            }
        })
        .collect()
}

/// Extract only the suite-level violations from a mixed slice.
///
/// Used to separate violations that belong in the `STRICT` summary section
/// (suite-level) from those reported inline with each test (per-test).
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
                    write!(
                        f,
                        "{:<60}  bare-assert        {}",
                        node_id.as_ref(),
                        lines_phrase(lines)
                    )
                }
            }
            Self::BroadFixtureType { node_id, detail } => {
                write!(
                    f,
                    "{:<60}  broad-fixture-type   {}",
                    node_id.as_ref(),
                    detail
                )
            }
            Self::DictParametrize { node_id } => {
                write!(f, "{:<60}  dict-parametrize", node_id.as_ref())
            }
            Self::InvalidModuleMark { node_id, detail } => {
                write!(
                    f,
                    "{:<60}  invalid-module-mark   {}",
                    node_id.as_ref(),
                    detail
                )
            }
            Self::MissingMarkReason { node_id, mark_name } => {
                write!(
                    f,
                    "{:<60}  missing-mark-reason   {}",
                    node_id.as_ref(),
                    mark_name
                )
            }
            Self::MissingReturnAnnotation {
                node_id,
                fixture_name,
            } => {
                write!(
                    f,
                    "{:<60}  missing-return-annotation   {}",
                    node_id.as_ref(),
                    fixture_name
                )
            }
            Self::ModuleLevelDef {
                node_id,
                fn_name,
                lineno,
            } => {
                write!(
                    f,
                    "{:<60}  module-level-def   {fn_name} line {lineno}",
                    node_id.as_ref()
                )
            }
            Self::SingleCaseParametrize { node_id } => {
                write!(f, "{:<60}  single-case-parametrize", node_id.as_ref())
            }
            Self::TestReturnsValue { node_id, lines } => {
                write!(
                    f,
                    "{:<60}  test-returns-value   {}",
                    node_id.as_ref(),
                    lines_phrase(lines)
                )
            }
            Self::UnusedFixture {
                node_id,
                fixture_name,
            } => {
                write!(
                    f,
                    "{:<60}  unused-fixture   {}",
                    node_id.as_ref(),
                    fixture_name
                )
            }
        }
    }
}

impl std::fmt::Display for SuiteViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MarkerNoDescription { marker_name } => {
                write!(f, "markers[\"{marker_name}\"]   no description")
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

/// Format a single violation as a fixed-width tabular line for the `STRICT` output block.
///
/// Format: `<node_id padded to 60 chars>  <violation-kind>  [extra detail]`.
/// Delegates to the `Display` impl on each variant.
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
                format!("strict: bare assert on {}", lines_phrase(lines))
            }
        }
        PerTestViolation::BroadFixtureType { detail, .. } => {
            format!("strict: broad fixture type — {detail}")
        }
        PerTestViolation::DictParametrize { .. } => {
            "strict: use a frozen dataclass instead of dict for parametrize cases".to_string()
        }
        PerTestViolation::InvalidModuleMark { detail, .. } => {
            format!("strict: invalid module-level mark {detail}")
        }
        PerTestViolation::MissingMarkReason { mark_name, .. } => {
            format!("strict: @mark.{mark_name} requires reason=")
        }
        PerTestViolation::MissingReturnAnnotation { fixture_name, .. } => {
            format!("strict: fixture '{fixture_name}' is missing a return type annotation")
        }
        PerTestViolation::ModuleLevelDef {
            fn_name, lineno, ..
        } => format!(
            "strict: '{fn_name}' (line {lineno}) is a module-level definition in a \
             test file that is neither a test nor a fixture declaration. Move it \
             to a module tests can import, or prefix it with '_' to mark it \
             file-local"
        ),
        PerTestViolation::SingleCaseParametrize { .. } => {
            "strict: @parametrize with a single case — use a plain test instead".to_string()
        }
        PerTestViolation::TestReturnsValue { lines, .. } => {
            format!(
                "strict: a test function returns None, but this one returns a value \
                 ({}). oxitest discards it, so an assertion written as \
                 `return a == b` never runs. Use `assert a == b, \"why it matters\"`",
                lines_phrase(lines)
            )
        }
        PerTestViolation::UnusedFixture { fixture_name, .. } => {
            format!("strict: fixture '{fixture_name}' is defined but never used")
        }
    };
    TestOutcome::Error(Box::new(crate::types::FailureDiagnostic::sentinel(message)))
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
                .map(|s| format!("\"{s}\""))
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
        assert!(matches!(outcome, TestOutcome::Error(d) if d.message.contains("strict")));
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
        if let TestOutcome::Error(d) = outcome {
            let message = &d.message;
            assert!(
                !message.ends_with(' '),
                "message must not have trailing space: {message:?}"
            );
            assert!(
                message.contains("strict"),
                "message must contain 'strict': {message:?}"
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
            "line must not have trailing space: {line:?}"
        );
        assert!(
            line.contains("bare-assert"),
            "line must contain 'bare-assert': {line:?}"
        );
    }

    #[test]
    fn test_check_collected_single_case_parametrize() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py::test_single".to_string(),
            kind: ViolationKind::SingleCaseParametrize,
            detail: String::new(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::SingleCaseParametrize { .. })
        ));
    }

    #[test]
    fn test_per_test_error_single_case_parametrize() {
        let v = PerTestViolation::SingleCaseParametrize {
            node_id: NodeId::from_raw("tests/test_foo.py::test_single"),
        };
        let outcome = per_test_error(&v);
        if let TestOutcome::Error(d) = outcome {
            let message = &d.message;
            assert!(
                message.contains("strict"),
                "message must contain 'strict': {message:?}"
            );
            assert!(
                message.contains("single case"),
                "message must mention single case: {message:?}"
            );
        } else {
            panic!("expected Error outcome");
        }
    }

    #[test]
    fn test_check_collected_missing_return_annotation() {
        let raw = vec![RawViolation {
            node_id: "/project/conftest.py".to_string(),
            kind: ViolationKind::MissingReturnAnnotation,
            detail: "db".to_string(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::MissingReturnAnnotation {
                fixture_name, ..
            }) if fixture_name == "db"
        ));
    }

    #[test]
    fn test_format_violation_line_missing_return_annotation() {
        let v = StrictViolation::PerTest(PerTestViolation::MissingReturnAnnotation {
            node_id: NodeId::from_raw("tests/conftest.py"),
            fixture_name: "db".to_string(),
        });
        let line = format_violation_line(&v);
        assert!(line.contains("missing-return-annotation"));
        assert!(line.contains("db"));
    }

    #[test]
    fn test_per_test_error_missing_return_annotation() {
        let v = PerTestViolation::MissingReturnAnnotation {
            node_id: NodeId::from_raw("/project/conftest.py"),
            fixture_name: "db".to_string(),
        };
        let outcome = per_test_error(&v);
        if let TestOutcome::Error(d) = outcome {
            let message = &d.message;
            assert!(
                message.contains("strict"),
                "message must contain 'strict': {message:?}"
            );
            assert!(
                message.contains("db"),
                "message must mention fixture name 'db': {message:?}"
            );
            assert!(
                message.contains("return type annotation"),
                "message must mention return type annotation: {message:?}"
            );
        } else {
            panic!("expected Error outcome");
        }
    }

    #[test]
    fn test_check_collected_unused_fixture() {
        let raw = vec![RawViolation {
            node_id: "/project/conftest.py".to_string(),
            kind: ViolationKind::UnusedFixture,
            detail: "unused_db".to_string(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::UnusedFixture {
                fixture_name, ..
            }) if fixture_name == "unused_db"
        ));
    }

    #[test]
    fn test_check_collected_invalid_module_mark() {
        let raw = vec![RawViolation {
            node_id: "tests/test_foo.py".to_string(),
            kind: ViolationKind::InvalidModuleMark,
            detail: "skip".to_string(),
        }];
        let violations = check_collected(raw);
        assert_eq!(violations.len(), 1);
        assert!(matches!(
            &violations[0],
            StrictViolation::PerTest(PerTestViolation::InvalidModuleMark { detail, .. })
            if detail == "skip"
        ));
    }

    #[test]
    fn test_per_test_error_unused_fixture() {
        let v = PerTestViolation::UnusedFixture {
            node_id: NodeId::from_raw("/project/conftest.py"),
            fixture_name: "unused_db".to_string(),
        };
        let outcome = per_test_error(&v);
        if let TestOutcome::Error(d) = outcome {
            let message = &d.message;
            assert!(
                message.contains("strict"),
                "message must contain 'strict': {message:?}"
            );
            assert!(
                message.contains("unused_db"),
                "message must mention fixture name 'unused_db': {message:?}"
            );
            assert!(
                message.contains("defined but never used"),
                "message must mention 'defined but never used': {message:?}"
            );
        } else {
            panic!("expected Error outcome");
        }
    }

    #[test]
    fn test_format_violation_line_unused_fixture() {
        let v = StrictViolation::PerTest(PerTestViolation::UnusedFixture {
            node_id: NodeId::from_raw("tests/conftest.py"),
            fixture_name: "unused_db".to_string(),
        });
        let line = format_violation_line(&v);
        assert!(line.contains("unused-fixture"));
        assert!(line.contains("unused_db"));
    }
}
