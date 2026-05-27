//! JUnit XML reporter — writes a JUnit-compatible XML file for CI integration.
//!
//! Compatible with GitHub Actions, Jenkins, GitLab CI, and Azure DevOps.
//! Activated via `--junit-xml <path>`. Can be used alongside `--json`.

use std::io::Write;
use std::time::Instant;

use camino::Utf8PathBuf;
use quick_xml::events::{BytesDecl, BytesEnd, BytesStart, Event};
use quick_xml::Writer;

use crate::types::{CollectError, DurationMs, TestItem, TestOutcome};

fn write_element<W: std::io::Write>(
    writer: &mut Writer<W>,
    tag: &str,
    attrs: &[(&str, &str)],
    write_children: impl FnOnce(&mut Writer<W>) -> quick_xml::Result<()>,
) -> quick_xml::Result<()> {
    let mut elem = BytesStart::new(tag);
    for &(k, v) in attrs {
        elem.push_attribute((k, v));
    }
    writer.write_event(Event::Start(elem))?;
    write_children(writer)?;
    writer.write_event(Event::End(BytesEnd::new(tag)))?;
    Ok(())
}

fn write_empty_element<W: std::io::Write>(
    writer: &mut Writer<W>,
    tag: &str,
    attrs: &[(&str, &str)],
) -> quick_xml::Result<()> {
    let mut elem = BytesStart::new(tag);
    for &(k, v) in attrs {
        elem.push_attribute((k, v));
    }
    writer.write_event(Event::Empty(elem))?;
    Ok(())
}

use super::Reporter;

/// Accumulates test results and writes JUnit XML on `finish()`.
pub struct JunitReporter {
    path: Utf8PathBuf,
    start: Instant,
    results: Vec<JunitResult>,
}

struct JunitResult {
    classname: String,
    name: String,
    time_secs: f64,
    outcome: JunitOutcome,
}

enum JunitOutcome {
    Passed,
    Failed { message: String },
    Error { message: String },
    Skipped { message: String },
}

impl JunitReporter {
    pub fn new(path: Utf8PathBuf) -> Self {
        Self {
            path,
            start: Instant::now(),
            results: Vec::new(),
        }
    }
}

/// Convert `module_path` to a Java-style classname: `tests/test_math.py` → `tests.test_math`.
fn to_classname(module_path: &str) -> String {
    module_path
        .strip_suffix(".py")
        .unwrap_or(module_path)
        .replace('/', ".")
}

/// Build the test name: `fn_name` or `fn_name[param_id]`.
fn to_testname(item: &TestItem) -> String {
    match &item.param_id {
        Some(pid) => format!("{}[{}]", item.fn_name, pid),
        None => item.fn_name.clone(),
    }
}

fn to_junit_outcome(outcome: &TestOutcome) -> JunitOutcome {
    let msg = || outcome.message().unwrap_or_default().to_owned();
    match outcome {
        TestOutcome::Passed { .. }
        | TestOutcome::Warned { .. }
        | TestOutcome::XPassed { strict: false }
        | TestOutcome::Flaky { .. } => JunitOutcome::Passed,
        TestOutcome::Failed { .. } => JunitOutcome::Failed { message: msg() },
        TestOutcome::Error { .. } | TestOutcome::Timeout { .. } => {
            JunitOutcome::Error { message: msg() }
        }
        TestOutcome::Skipped { .. } | TestOutcome::XFailed { .. } => {
            JunitOutcome::Skipped { message: msg() }
        }
        TestOutcome::XPassed { strict: true } => JunitOutcome::Failed {
            message: "expected failure but test passed (strict xfail)".to_string(),
        },
    }
}

impl Reporter for JunitReporter {
    fn test_started(&mut self, _item: &TestItem) {}

    fn test_completed(&mut self, item: &TestItem, outcome: &TestOutcome, duration_ms: DurationMs) {
        self.results.push(JunitResult {
            classname: to_classname(item.module_path.as_str()),
            name: to_testname(item),
            time_secs: duration_ms.as_f64() / 1000.0,
            outcome: to_junit_outcome(outcome),
        });
    }

    fn finish(
        &mut self,
        _collect_errors: &[CollectError],
        _interrupted: bool,
        _stats: &super::RunStats,
    ) -> super::ExitVote {
        if let Err(e) = self.write_xml() {
            eprintln!("error: failed to write JUnit XML to {}: {e}", self.path);
            return super::ExitVote::Code(4);
        }
        super::ExitVote::Abstain
    }
}

impl JunitReporter {
    fn write_xml(&self) -> std::io::Result<()> {
        let mut buf = Vec::new();
        let mut writer = Writer::new_with_indent(&mut buf, b' ', 2);

        writer
            .write_event(Event::Decl(BytesDecl::new("1.0", Some("UTF-8"), None)))
            .map_err(std::io::Error::other)?;

        let total = self.results.len();
        let failures = self
            .results
            .iter()
            .filter(|r| matches!(r.outcome, JunitOutcome::Failed { .. }))
            .count();
        let errors = self
            .results
            .iter()
            .filter(|r| matches!(r.outcome, JunitOutcome::Error { .. }))
            .count();
        let skipped = self
            .results
            .iter()
            .filter(|r| matches!(r.outcome, JunitOutcome::Skipped { .. }))
            .count();
        let total_time = self.start.elapsed().as_secs_f64();

        let total_str = total.to_string();
        let failures_str = failures.to_string();
        let errors_str = errors.to_string();
        let skipped_str = skipped.to_string();
        let time_str = format!("{total_time:.3}");

        write_element(
            &mut writer,
            "testsuites",
            &[
                ("tests", &total_str),
                ("failures", &failures_str),
                ("errors", &errors_str),
                ("time", &time_str),
            ],
            |w| {
                write_element(
                    w,
                    "testsuite",
                    &[
                        ("name", "oxitest"),
                        ("tests", &total_str),
                        ("failures", &failures_str),
                        ("errors", &errors_str),
                        ("skipped", &skipped_str),
                        ("time", &time_str),
                    ],
                    |w| {
                        for result in &self.results {
                            let time_secs = format!("{:.3}", result.time_secs);
                            let tc_attrs: [(&str, &str); 3] = [
                                ("classname", result.classname.as_str()),
                                ("name", result.name.as_str()),
                                ("time", &time_secs),
                            ];

                            match &result.outcome {
                                JunitOutcome::Passed => {
                                    write_empty_element(w, "testcase", &tc_attrs)?;
                                }
                                JunitOutcome::Failed { message } => {
                                    write_element(w, "testcase", &tc_attrs, |w| {
                                        write_empty_element(
                                            w,
                                            "failure",
                                            &[("message", message.as_str())],
                                        )
                                    })?;
                                }
                                JunitOutcome::Error { message } => {
                                    write_element(w, "testcase", &tc_attrs, |w| {
                                        write_empty_element(
                                            w,
                                            "error",
                                            &[("message", message.as_str())],
                                        )
                                    })?;
                                }
                                JunitOutcome::Skipped { message } => {
                                    let skip_attrs: Vec<(&str, &str)> = if message.is_empty() {
                                        vec![]
                                    } else {
                                        vec![("message", message.as_str())]
                                    };
                                    write_element(w, "testcase", &tc_attrs, |w| {
                                        write_empty_element(w, "skipped", &skip_attrs)
                                    })?;
                                }
                            }
                        }
                        Ok(())
                    },
                )
            },
        )
        .map_err(std::io::Error::other)?;

        let mut file = std::fs::File::create(&*self.path)?;
        file.write_all(&buf)?;
        Ok(())
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::reporter::test_helpers::{make_item, make_outcome};
    use crate::reporter::Reporter;
    use crate::types::DurationMs;
    use insta::assert_snapshot;

    fn run_and_read(items: &[(&str, &str)]) -> String {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = camino::Utf8PathBuf::try_from(tmp.path().to_path_buf()).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        for (name, status) in items {
            let item = make_item(name);
            let outcome = make_outcome(status);
            rep.test_started(&item);
            rep.test_completed(&item, &outcome, DurationMs::ZERO);
        }
        rep.finish(&[], false, &crate::reporter::RunStats::new());
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn junit_mixed_outcomes() {
        let xml = run_and_read(&[
            ("test_pass", "passed"),
            ("test_fail", "failed"),
            ("test_skip", "skipped"),
            ("test_err", "error"),
        ]);
        assert_snapshot!(xml);
    }

    #[test]
    fn junit_all_passed() {
        let xml = run_and_read(&[("test_a", "passed"), ("test_b", "passed")]);
        assert_snapshot!(xml);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::test_helpers::{make_error, make_failed, make_item, make_item_in};
    use crate::types::TestOutcome;
    use tempfile::TempDir;

    fn run_reporter(outcomes: Vec<(std::sync::Arc<TestItem>, TestOutcome)>) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("results.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        for (item, outcome) in &outcomes {
            rep.test_completed(item, outcome, DurationMs::new(42.0));
        }
        rep.finish(&[], false, &crate::reporter::RunStats::new());
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_passed_produces_empty_testcase() {
        let xml = run_reporter(vec![(
            make_item("test_add"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
        )]);
        assert!(xml.contains("<testcase"), "must contain testcase element");
        assert!(
            !xml.contains("<failure"),
            "passed test must not have failure element"
        );
        assert!(
            !xml.contains("<skipped"),
            "passed test must not have skipped element"
        );
    }

    #[test]
    fn test_failed_produces_failure_element() {
        let xml = run_reporter(vec![(
            make_item("test_sub"),
            make_failed("expected 42", "tests/t.py", 5, "assert x == 42"),
        )]);
        assert!(
            xml.contains("<failure"),
            "failed test must have failure element"
        );
        assert!(
            xml.contains("expected 42"),
            "failure message must appear in XML"
        );
    }

    #[test]
    fn test_error_produces_error_element() {
        let xml = run_reporter(vec![(
            make_item("test_err"),
            make_error("ValueError: bad", "tests/t.py", 1, "x"),
        )]);
        assert!(xml.contains("<error"), "error test must have error element");
        assert!(
            xml.contains("ValueError: bad"),
            "error message must appear in XML"
        );
    }

    #[test]
    fn test_skipped_produces_skipped_element() {
        let xml = run_reporter(vec![(
            make_item("test_skip"),
            TestOutcome::Skipped {
                reason: "not ready".to_string(),
            },
        )]);
        assert!(
            xml.contains("<skipped"),
            "skipped test must have skipped element"
        );
        assert!(xml.contains("not ready"), "skip reason must appear in XML");
    }

    #[test]
    fn test_classname_converts_path_separators() {
        let xml = run_reporter(vec![(
            make_item_in("test_fn", "tests/unit/test_math.py"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
        )]);
        assert!(
            xml.contains("tests.unit.test_math"),
            "classname must use dot separators: {xml}"
        );
    }

    #[test]
    fn test_xml_has_declaration_and_root() {
        let xml = run_reporter(vec![(
            make_item("test_a"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
        )]);
        assert!(xml.contains("<?xml"), "must have XML declaration");
        assert!(
            xml.contains("<testsuites"),
            "must have testsuites root element"
        );
        assert!(xml.contains("<testsuite"), "must have testsuite element");
    }

    #[test]
    fn test_timeout_produces_error_element() {
        let xml = run_reporter(vec![(
            make_item("test_slow"),
            TestOutcome::Timeout {
                message: "exceeded 5s".to_string(),
            },
        )]);
        assert!(xml.contains("<error"), "timeout must produce error element");
        assert!(xml.contains("exceeded 5s"), "timeout message must appear");
    }

    #[test]
    fn test_xfailed_produces_skipped_element() {
        let xml = run_reporter(vec![(
            make_item("test_xf"),
            TestOutcome::XFailed {
                reason: "known bug".to_string(),
            },
        )]);
        assert!(
            xml.contains("<skipped"),
            "xfailed must produce skipped element"
        );
    }

    #[test]
    fn test_strict_xpass_produces_failure() {
        let xml = run_reporter(vec![(
            make_item("test_xp"),
            TestOutcome::XPassed { strict: true },
        )]);
        assert!(
            xml.contains("<failure"),
            "strict xpass must produce failure element"
        );
    }

    #[test]
    fn test_write_failure_returns_code_4() {
        let path = camino::Utf8PathBuf::from("/nonexistent/dir/out.xml");
        let mut rep = JunitReporter::new(path);
        rep.test_completed(
            &make_item("test_a"),
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        let vote = rep.finish(&[], false, &crate::reporter::RunStats::new());
        assert_eq!(
            vote.code(),
            4,
            "must return exit code 4 when XML file cannot be written"
        );
    }

    #[test]
    fn test_time_in_seconds() {
        let xml = run_reporter(vec![(
            make_item("test_a"),
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
        )]);
        // 42ms → 0.042 seconds
        assert!(
            xml.contains("time=\"0.042\""),
            "duration must be in seconds: {xml}"
        );
    }
}
