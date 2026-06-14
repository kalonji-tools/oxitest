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

use super::JunitCategory;

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
    category: JunitCategory,
    message: String,
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

impl Reporter for JunitReporter {
    fn test_started(&mut self, _item: &TestItem) {}

    fn test_completed(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
        _parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
    ) {
        let message = match outcome {
            TestOutcome::XPassed { strict: true } => {
                "expected failure but test passed (strict xfail)".to_string()
            }
            _ => outcome.message().unwrap_or_default().to_owned(),
        };
        self.results.push(JunitResult {
            classname: to_classname(item.module_path.as_str()),
            name: to_testname(item),
            time_secs: duration_ms.as_f64() / 1000.0,
            category: outcome.junit_category(),
            message,
        });
    }

    fn finish(
        &mut self,
        _collect_errors: &[CollectError],
        _interrupted: bool,
        _session: &super::ReporterSession,
    ) -> super::ExitVote {
        if let Err(e) = self.write_xml() {
            eprintln!("error: failed to write JUnit XML to {}: {e}", self.path);
            return super::ExitVote::Code(crate::types::ExitCode::UsageError);
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
            .filter(|r| r.category == JunitCategory::Failed)
            .count();
        let errors = self
            .results
            .iter()
            .filter(|r| r.category == JunitCategory::Error)
            .count();
        let skipped = self
            .results
            .iter()
            .filter(|r| r.category == JunitCategory::Skipped)
            .count();
        let total_time = self.start.elapsed().as_secs_f64();

        // <testsuites>
        let mut testsuites = BytesStart::new("testsuites");
        testsuites.push_attribute(("tests", total.to_string().as_str()));
        testsuites.push_attribute(("failures", failures.to_string().as_str()));
        testsuites.push_attribute(("errors", errors.to_string().as_str()));
        testsuites.push_attribute(("time", format!("{total_time:.3}").as_str()));
        writer
            .write_event(Event::Start(testsuites))
            .map_err(std::io::Error::other)?;

        //   <testsuite name="oxitest">
        let mut testsuite = BytesStart::new("testsuite");
        testsuite.push_attribute(("name", "oxitest"));
        testsuite.push_attribute(("tests", total.to_string().as_str()));
        testsuite.push_attribute(("failures", failures.to_string().as_str()));
        testsuite.push_attribute(("errors", errors.to_string().as_str()));
        testsuite.push_attribute(("skipped", skipped.to_string().as_str()));
        testsuite.push_attribute(("time", format!("{total_time:.3}").as_str()));
        writer
            .write_event(Event::Start(testsuite))
            .map_err(std::io::Error::other)?;

        for result in &self.results {
            //     <testcase classname="..." name="..." time="...">
            let mut testcase = BytesStart::new("testcase");
            testcase.push_attribute(("classname", result.classname.as_str()));
            testcase.push_attribute(("name", result.name.as_str()));
            testcase.push_attribute(("time", format!("{:.3}", result.time_secs).as_str()));

            match result.category {
                JunitCategory::Passed => {
                    writer
                        .write_event(Event::Empty(testcase))
                        .map_err(std::io::Error::other)?;
                }
                JunitCategory::Failed => {
                    writer
                        .write_event(Event::Start(testcase))
                        .map_err(std::io::Error::other)?;
                    let mut failure = BytesStart::new("failure");
                    failure.push_attribute(("message", result.message.as_str()));
                    writer
                        .write_event(Event::Empty(failure))
                        .map_err(std::io::Error::other)?;
                    writer
                        .write_event(Event::End(BytesEnd::new("testcase")))
                        .map_err(std::io::Error::other)?;
                }
                JunitCategory::Error => {
                    writer
                        .write_event(Event::Start(testcase))
                        .map_err(std::io::Error::other)?;
                    let mut error = BytesStart::new("error");
                    error.push_attribute(("message", result.message.as_str()));
                    writer
                        .write_event(Event::Empty(error))
                        .map_err(std::io::Error::other)?;
                    writer
                        .write_event(Event::End(BytesEnd::new("testcase")))
                        .map_err(std::io::Error::other)?;
                }
                JunitCategory::Skipped => {
                    writer
                        .write_event(Event::Start(testcase))
                        .map_err(std::io::Error::other)?;
                    let mut skip = BytesStart::new("skipped");
                    if !result.message.is_empty() {
                        skip.push_attribute(("message", result.message.as_str()));
                    }
                    writer
                        .write_event(Event::Empty(skip))
                        .map_err(std::io::Error::other)?;
                    writer
                        .write_event(Event::End(BytesEnd::new("testcase")))
                        .map_err(std::io::Error::other)?;
                }
            }
        }

        //   </testsuite>
        writer
            .write_event(Event::End(BytesEnd::new("testsuite")))
            .map_err(std::io::Error::other)?;
        // </testsuites>
        writer
            .write_event(Event::End(BytesEnd::new("testsuites")))
            .map_err(std::io::Error::other)?;

        let mut file = std::fs::File::create(&*self.path)?;
        file.write_all(&buf)?;
        Ok(())
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::reporter::Reporter;
    use crate::types::{DurationMs, FailureDiagnostic, TestItem, TestOutcome};
    use insta::assert_snapshot;

    /// Normalize all `time="..."` attribute values to `time="0.000"` for snapshot stability.
    fn normalize_times(xml: &str) -> String {
        let mut result = String::with_capacity(xml.len());
        let mut rest = xml;
        while let Some(pos) = rest.find("time=\"") {
            result.push_str(&rest[..pos]);
            result.push_str("time=\"0.000\"");
            let after_key = &rest[pos + 6..]; // skip past `time="`
            if let Some(end) = after_key.find('"') {
                rest = &after_key[end + 1..];
            } else {
                break;
            }
        }
        result.push_str(rest);
        result
    }

    fn run_and_read(items: &[(&str, &str)]) -> String {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = camino::Utf8PathBuf::try_from(tmp.path().to_path_buf()).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        for (name, status) in items {
            let item = TestItem::builder("tests/test_foo.py", name).arc();
            let outcome = match *status {
                "passed" => TestOutcome::Passed { tips: None },
                "failed" => {
                    TestOutcome::Failed(Box::new(FailureDiagnostic::sentinel(String::new())))
                }
                "skipped" => TestOutcome::Skipped {
                    reason: String::new(),
                },
                "error" => TestOutcome::Error(Box::new(FailureDiagnostic::sentinel(String::new()))),
                "xfailed" => TestOutcome::XFailed {
                    reason: String::new(),
                },
                "xpassed" => TestOutcome::XPassed { strict: false },
                "warned" => TestOutcome::Warned {
                    reason: String::new(),
                    tips: None,
                },
                "timeout" => TestOutcome::Timeout {
                    message: String::new(),
                },
                other => panic!("unexpected status in test: {other}"),
            };
            rep.test_started(&item);
            rep.test_completed(&item, &outcome, DurationMs::ZERO, None);
        }
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        let xml = std::fs::read_to_string(&path).unwrap();
        normalize_times(&xml)
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
    use crate::types::TestItem;
    use crate::types::TestOutcome;
    use tempfile::TempDir;

    fn run_reporter(outcomes: Vec<(std::sync::Arc<TestItem>, TestOutcome)>) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("results.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        for (item, outcome) in &outcomes {
            rep.test_completed(item, outcome, DurationMs::new(42.0), None);
        }
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_passed_produces_empty_testcase() {
        let xml = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_add").arc(),
            TestOutcome::Passed { tips: None },
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
            TestItem::builder("tests/test_foo.py", "test_sub").arc(),
            TestOutcome::failed("expected 42")
                .file("tests/t.py")
                .lineno(5)
                .source("assert x == 42")
                .build(),
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
            TestItem::builder("tests/test_foo.py", "test_err").arc(),
            TestOutcome::error("ValueError: bad")
                .file("tests/t.py")
                .source("x")
                .build(),
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
            TestItem::builder("tests/test_foo.py", "test_skip").arc(),
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
            TestItem::builder("tests/unit/test_math.py", "test_fn").arc(),
            TestOutcome::Passed { tips: None },
        )]);
        assert!(
            xml.contains("tests.unit.test_math"),
            "classname must use dot separators: {xml}"
        );
    }

    #[test]
    fn test_xml_has_declaration_and_root() {
        let xml = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_a").arc(),
            TestOutcome::Passed { tips: None },
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
            TestItem::builder("tests/test_foo.py", "test_slow").arc(),
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
            TestItem::builder("tests/test_foo.py", "test_xf").arc(),
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
            TestItem::builder("tests/test_foo.py", "test_xp").arc(),
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
            &TestItem::builder("tests/test_foo.py", "test_a").arc(),
            &TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
            None,
        );
        let vote = rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        assert_eq!(
            vote.code(),
            crate::types::ExitCode::UsageError,
            "must return exit code 4 when XML file cannot be written"
        );
    }

    #[test]
    fn test_time_in_seconds() {
        let xml = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_a").arc(),
            TestOutcome::Passed { tips: None },
        )]);
        // 42ms → 0.042 seconds
        assert!(
            xml.contains("time=\"0.042\""),
            "duration must be in seconds: {xml}"
        );
    }
}
