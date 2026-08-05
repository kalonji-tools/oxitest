//! JUnit XML reporter — writes a JUnit-compatible XML file for CI integration.
//!
//! Compatible with GitHub Actions, Jenkins, GitLab CI, and Azure DevOps.
//! Activated via `--junit-xml <path>`. Can be used alongside `--json`.

use std::io::Write;
use std::time::Instant;

use camino::Utf8PathBuf;
use quick_xml::Writer;
use quick_xml::events::{BytesDecl, BytesEnd, BytesStart, BytesText, Event};

use crate::types::{CollectError, DurationMs, NodeId, TestItem, TestOutcome};

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

    /// Record a `--strict=abort` violation as a `<failure>` entry.
    ///
    /// A per-test violation reuses the identity of the test it belongs to — the
    /// same deliberate reuse `json-output.md` documents for CTRF, and what lets a
    /// consumer line the violation up with its test. A suite-level violation
    /// belongs to no test and gets the `<strict>` marker instead (#1858).
    pub(crate) fn record_strict_violation(&mut self, node_id: Option<&NodeId>, message: String) {
        let (classname, name) = node_id.map_or_else(
            || (String::new(), STRICT_MARKER.to_string()),
            split_identity,
        );
        self.results.push(JunitResult {
            classname,
            name,
            time_secs: 0.0,
            category: JunitCategory::Failed,
            message,
        });
    }

    /// Record a collection error as a counted `<error>` entry.
    ///
    /// Counted rather than reported out-of-band: `<system-err>` does not
    /// participate in the verdict, so an aborted run carrying only that renders
    /// green. `tests=` over-reports the suite size by one, which cannot inflate a
    /// pass rate; `tests="0" errors="0"` under-reports a broken run as clean.
    fn record_collect_error(&mut self, error: &CollectError) {
        let classname = match error {
            CollectError::ImportError { path, .. } => to_classname(path.as_str()),
            CollectError::PyError(_) | CollectError::Affected(_) => String::new(),
        };
        self.results.push(JunitResult {
            classname,
            name: COLLECTION_MARKER.to_string(),
            time_secs: 0.0,
            category: JunitCategory::Error,
            message: error.to_string(),
        });
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
        None => item.fn_name.to_string(),
    }
}

/// `<testcase name=…>` for an entry that is not a real test.
///
/// Angle brackets are deliberate: `<` cannot appear in a Python identifier, so a
/// synthesised entry can never collide with a real test name.
///
/// The CTRF artifact uses the same two spellings, but **not under the same
/// rules**, so do not treat them as a shared contract: CTRF has one flat `name`
/// field, so it names an import failure after the *file* and falls back to
/// `<collection>` only when the error carries no path. JUnit has `classname` as
/// well, so the file goes there and `<collection>` is used for every collection
/// error. `<strict>` does mean the same thing in both (#1858).
const COLLECTION_MARKER: &str = "<collection>";
const STRICT_MARKER: &str = "<strict>";

/// Split a node ID into JUnit's `(classname, name)` pair.
fn split_identity(node_id: &NodeId) -> (String, String) {
    match crate::types::node_id::split_node_id_once(node_id) {
        Some((module, rest)) => (to_classname(module), rest.to_string()),
        None => (String::new(), node_id.to_string()),
    }
}

/// The line terminators that end a `message="…"` summary.
///
/// `\r` is included as well as `\n` so that a lone-CR message — rare, but
/// produced by anything that renders progress with carriage returns — cannot
/// smuggle its later lines into the attribute.
const LINE_BREAKS: [char; 2] = ['\n', '\r'];

/// The first line of `s`, for the `message="…"` attribute.
fn first_line(s: &str) -> &str {
    s.split(LINE_BREAKS).next().unwrap_or(s)
}

/// Replace the control characters XML 1.0 forbids outright.
///
/// quick-xml escapes `<`, `>`, `&`, `'` and `"`, but passes C0 control
/// characters through raw — and [XML 1.0 §2.2] forbids them *anywhere* in a
/// document, escaped or not. A conforming parser then rejects the **whole
/// file**, not the offending element, which would leave the artifact #1858
/// exists to guarantee unreadable.
///
/// This is reachable: a collection error carries arbitrary Python exception
/// text, and any library that colourises its own exception message puts a raw
/// `\x1b` in `str(exc)`. Tab, newline and carriage return are the three C0
/// characters XML permits, so they survive.
///
/// [XML 1.0 §2.2]: https://www.w3.org/TR/xml/#charsets
fn xml_safe(s: &str) -> std::borrow::Cow<'_, str> {
    if s.bytes()
        .any(|b| matches!(b, 0x00..=0x08 | 0x0B | 0x0C | 0x0E..=0x1F))
    {
        std::borrow::Cow::Owned(
            s.chars()
                .map(|c| {
                    if matches!(c, '\t' | '\n' | '\r') || c >= ' ' {
                        c
                    } else {
                        char::REPLACEMENT_CHARACTER
                    }
                })
                .collect(),
        )
    } else {
        std::borrow::Cow::Borrowed(s)
    }
}

/// Write a non-passing outcome's child element.
///
/// `message` is the text to report, or `None` to omit the `message` attribute
/// entirely — the empty-skip-reason case.
///
/// The attribute always carries only the **first line**. When the text spans
/// more than one line the whole of it is repeated as the element's body,
/// because XML attribute-value normalisation ([XML 1.0 §3.3.3]) replaces a
/// literal newline in an attribute with a space and quick-xml does not escape
/// newlines — so the body is the only place a traceback survives a conforming
/// parser.
///
/// Single-line messages keep the self-closing shape, so single-line artifacts
/// are byte-identical to before. **Multi-line bodies are new on every route**,
/// not only the aborted one: a failing doctest always takes this branch (#1858).
///
/// [XML 1.0 §3.3.3]: https://www.w3.org/TR/xml/#AVNormalize
fn write_child(
    writer: &mut Writer<&mut Vec<u8>>,
    tag: &str,
    message: Option<&str>,
) -> std::io::Result<()> {
    let text = message.unwrap_or_default();
    let mut elem = BytesStart::new(tag);
    if let Some(value) = message {
        elem.push_attribute(("message", xml_safe(first_line(value)).as_ref()));
    }
    // Keyed on the same character set `first_line` splits on, so a lone-CR
    // message cannot lose its tail: whatever the attribute drops, the body keeps.
    if text.contains(LINE_BREAKS) {
        writer
            .write_event(Event::Start(elem))
            .map_err(std::io::Error::other)?;
        writer
            .write_event(Event::Text(BytesText::new(xml_safe(text).as_ref())))
            .map_err(std::io::Error::other)?;
        writer
            .write_event(Event::End(BytesEnd::new(tag)))
            .map_err(std::io::Error::other)?;
    } else {
        writer
            .write_event(Event::Empty(elem))
            .map_err(std::io::Error::other)?;
    }
    Ok(())
}

impl Reporter for JunitReporter {
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
            classname: to_classname(item.module_path()),
            name: to_testname(item),
            time_secs: duration_ms.as_f64() / 1000.0,
            category: outcome.junit_category(),
            message,
        });
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        _interrupted: bool,
        _session: &super::ReporterSession,
    ) -> super::ExitVote {
        for error in collect_errors {
            self.record_collect_error(error);
        }

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
            testcase.push_attribute(("classname", xml_safe(&result.classname).as_ref()));
            testcase.push_attribute(("name", xml_safe(&result.name).as_ref()));
            testcase.push_attribute(("time", format!("{:.3}", result.time_secs).as_str()));

            // Exhaustive on purpose — no wildcard. A new `JunitCategory` must
            // fail to compile here rather than silently serialise as `<skipped>`,
            // which is the one category that moves no counter in `<testsuites>`.
            let child: Option<(&str, Option<&str>)> = match result.category {
                JunitCategory::Passed => None,
                JunitCategory::Failed => Some(("failure", Some(result.message.as_str()))),
                JunitCategory::Error => Some(("error", Some(result.message.as_str()))),
                // `skipped` omits an empty message; `failure`/`error` keep
                // `message=""`, which both existing snapshots pin.
                JunitCategory::Skipped => Some((
                    "skipped",
                    (!result.message.is_empty()).then_some(result.message.as_str()),
                )),
            };

            match child {
                None => {
                    writer
                        .write_event(Event::Empty(testcase))
                        .map_err(std::io::Error::other)?;
                }
                Some((tag, message)) => {
                    writer
                        .write_event(Event::Start(testcase))
                        .map_err(std::io::Error::other)?;
                    write_child(&mut writer, tag, message)?;
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

    // ── Aborted runs (#1858) ──────────────────────────────────────────────────

    /// Run `finish` with no tests and the given collect errors, returning the XML.
    fn finish_with_collect_errors(errors: &[CollectError]) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.finish(errors, false, &crate::reporter::ReporterSession::new(0));
        assert!(
            path.exists(),
            "--junit-xml promises the file exists after the run; an aborted run that writes nothing is indistinguishable from a job that never started"
        );
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_collect_error_becomes_counted_error_testcase() {
        let xml = finish_with_collect_errors(&[CollectError::ImportError {
            path: camino::Utf8PathBuf::from("tests/test_bad.py"),
            message: "ModuleNotFoundError: No module named 'nope'".to_string(),
        }]);

        assert!(
            xml.contains(r#"errors="1""#),
            "a collection error must move the errors counter — errors=\"0\" makes every JUnit consumer render the aborted run green: {xml}"
        );
        assert!(
            xml.contains(r#"tests="1""#),
            "the synthesised entry must be counted in tests= too, or tests/errors is internally inconsistent: {xml}"
        );
        assert!(
            xml.contains(r#"classname="tests.test_bad""#),
            "classname must name the file that failed to import so a dashboard groups it where its tests would have been: {xml}"
        );
        assert!(
            xml.contains(r#"name="&lt;collection&gt;""#),
            "name must carry the <collection> marker so the entry cannot be mistaken for a real test: {xml}"
        );
        assert!(
            xml.contains("ModuleNotFoundError"),
            "the underlying Python error must survive into the artifact — the XML file is all CI keeps: {xml}"
        );
    }

    #[test]
    fn test_pathless_collect_error_has_empty_classname() {
        let xml =
            finish_with_collect_errors(&[CollectError::PyError("conftest exploded".to_string())]);

        assert!(
            xml.contains(r#"classname="""#),
            "an error naming no file has no module to group under — inventing one would point a dashboard at the wrong file: {xml}"
        );
        assert!(
            xml.contains(r#"errors="1""#),
            "a pathless collection error is still a broken run: {xml}"
        );
    }

    #[test]
    fn test_multiline_message_survives_in_element_body() {
        let xml = finish_with_collect_errors(&[CollectError::ImportError {
            path: camino::Utf8PathBuf::from("tests/test_bad.py"),
            message: "line one\nline two".to_string(),
        }]);

        assert!(
            xml.contains("line one\nline two</error>"),
            "a multi-line message must reach the element text body verbatim — XML attribute-value normalisation flattens newlines in message=, so the body is the only place a traceback survives a conforming parser: {xml}"
        );
        assert!(
            !xml.contains("line two\">"),
            "the attribute must be reduced to the detail's first line once the body carries the full text — duplicating a whole traceback doubles the artifact for the one case that matters, and the parser would flatten the attribute copy anyway: {xml}"
        );
    }

    #[test]
    fn test_affected_collect_error_has_empty_classname() {
        let xml = finish_with_collect_errors(&[CollectError::Affected(
            crate::affected::AffectedError::NotAGitRepo,
        )]);

        assert!(
            xml.contains(r#"classname="""#) && xml.contains(r#"errors="1""#),
            "Affected shares the pathless arm with PyError; without a case here half that arm is unasserted and could be given a bogus classname unnoticed: {xml}"
        );
    }

    /// Feed `message` through a `Failed` entry and return the rendered XML.
    fn xml_for_message(message: &str) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.record_strict_violation(None, message.to_string());
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_crlf_message_does_not_leave_a_stray_cr_in_the_attribute() {
        let xml = xml_for_message("first\r\nsecond");

        assert!(
            xml.contains(r#"message="first""#),
            "splitting on '\\n' alone would leave a trailing '\\r' in the attribute, which a conforming parser normalises to a trailing space: {xml}"
        );
        assert!(
            xml.contains("first\r\nsecond</failure>"),
            "the body must still carry the untouched original: {xml}"
        );
    }

    #[test]
    fn test_lone_cr_message_keeps_its_tail_in_the_body() {
        let xml = xml_for_message("first\rsecond");

        assert!(
            xml.contains(r#"message="first""#),
            "a lone CR ends the first line just as a newline does: {xml}"
        );
        assert!(
            xml.contains("first\rsecond</failure>"),
            "the body decision must key on the same characters the summary splits on — keying it on '\\n' alone would drop 'second' from the attribute AND skip the body, losing it entirely: {xml}"
        );
    }

    #[test]
    fn test_trailing_newline_message_still_gets_a_body() {
        let xml = xml_for_message("boom\n");

        assert!(
            xml.contains(r#"message="boom""#) && xml.contains("boom\n</failure>"),
            "a trailing newline makes the text multi-line, so it takes the body branch; the attribute keeps the one real line: {xml}"
        );
    }

    #[test]
    fn test_control_characters_do_not_break_well_formedness() {
        // A colourised Python exception message — rich, click and colorama all
        // put a raw ESC in `str(exc)`.
        let xml = xml_for_message("\x1b[31mboom\x1b[0m\nsecond line\x00");

        assert!(
            !xml.contains('\x1b') && !xml.contains('\u{0}'),
            "XML 1.0 forbids C0 control characters anywhere in a document, escaped or not, and a parser rejects the WHOLE file rather than the one element — a colourised exception message would make the artifact #1858 guarantees unreadable: {xml:?}"
        );
        assert!(
            xml.contains('\u{FFFD}'),
            "the illegal characters must be replaced rather than dropped, so the reader can see something was there: {xml:?}"
        );
    }

    #[test]
    fn test_hostile_identity_cannot_break_out_of_its_attribute() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.test_completed(
            &TestItem::builder("tests/test_foo.py", "test_x")
                .param_id(r#"a"><injected/><x y=""#.to_string())
                .arc(),
            &TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
            None,
        );
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));

        let xml = std::fs::read_to_string(&path).unwrap();
        assert!(
            !xml.contains("<injected/>"),
            "a param ID is attacker-adjacent data (it comes from test source); if it escaped its attribute it could forge testcase elements and rewrite a CI verdict: {xml}"
        );
    }

    #[test]
    fn test_errors_and_failures_are_counted_separately_in_one_abort() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.record_strict_violation(None, "bad marker".to_string());
        rep.finish(
            &[
                CollectError::PyError("conftest exploded".to_string()),
                CollectError::ImportError {
                    path: camino::Utf8PathBuf::from("tests/test_bad.py"),
                    message: "boom".to_string(),
                },
            ],
            false,
            &crate::reporter::ReporterSession::new(0),
        );

        let xml = std::fs::read_to_string(&path).unwrap();
        assert!(
            xml.contains(r#"tests="3""#)
                && xml.contains(r#"failures="1""#)
                && xml.contains(r#"errors="2""#)
                && xml.contains(r#"skipped="0""#),
            "the three counters are three independent filter passes over one vector, so a mixed abort is where an off-by-one between them surfaces; skipped must stay 0 rather than absorbing the synthesised entries: {xml}"
        );
    }

    #[test]
    fn test_strict_violation_reuses_its_test_identity() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.record_strict_violation(
            Some(&crate::types::NodeId::from_raw("tests/test_foo.py::test_x")),
            "bare assert at line 5".to_string(),
        );
        rep.record_strict_violation(None, "marker 'db' has no description".to_string());
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));

        let xml = std::fs::read_to_string(&path).unwrap();
        assert!(
            xml.contains(r#"classname="tests.test_foo" name="test_x""#),
            "a per-test violation must reuse the identity of the test it belongs to, which is what lets a consumer line the violation up with its test (json-output.md documents the same reuse for CTRF): {xml}"
        );
        assert!(
            xml.contains(r#"name="&lt;strict&gt;""#),
            "a suite-level violation belongs to no test, so it must not borrow one's identity: {xml}"
        );
        assert!(
            xml.contains(r#"failures="2""#),
            "strict violations are failures, not errors — the suite was checked and found non-conforming rather than failing to load: {xml}"
        );
    }

    #[test]
    fn test_run_failures_and_real_results_are_summed_together() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.xml")).unwrap();
        let mut rep = JunitReporter::new(path.clone());
        rep.test_completed(
            &TestItem::builder("tests/test_foo.py", "test_a").arc(),
            &TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
            None,
        );
        rep.record_strict_violation(None, "boom".to_string());
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));

        let xml = std::fs::read_to_string(&path).unwrap();
        assert!(
            xml.contains(r#"tests="2""#) && xml.contains(r#"failures="1""#),
            "a recorded run failure must not swallow the tests that did execute, nor be omitted from the summary: {xml}"
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
