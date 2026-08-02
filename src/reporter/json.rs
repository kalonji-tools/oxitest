use camino::Utf8PathBuf;

use serde::Serialize;

use crate::types::{CollectError, DurationMs, TestItem, TestOutcome};

use super::Reporter;

// ─── CTRF data structures ────────────────────────────────────────────────────

#[derive(Serialize)]
struct CtrfOutput {
    results: CtrfResults,
}

#[derive(Serialize)]
struct CtrfResults {
    tool: CtrfTool,
    summary: CtrfSummary,
    tests: Vec<CtrfTest>,
}

#[derive(Serialize)]
struct CtrfTool {
    name: &'static str,
}

#[derive(Serialize)]
struct CtrfSummary {
    tests: usize,
    passed: usize,
    failed: usize,
    skipped: usize,
    other: usize,
}

#[derive(Serialize)]
struct CtrfTest {
    name: String,
    status: &'static str,
    duration: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

/// The `tests[].name` to report a collection error under.
///
/// A file path, never a node ID, so a synthesised entry can never be mistaken
/// for — or collide with — a real test. Errors that carry no path fall back to
/// a literal `<collection>`.
fn collect_error_name(error: &CollectError) -> String {
    match error {
        CollectError::ImportError { path, .. } => path.to_string(),
        CollectError::PyError(_) | CollectError::Affected(_) => "<collection>".to_string(),
    }
}

// ─── JsonReporter ─────────────────────────────────────────────────────────────

pub struct JsonReporter {
    path: Utf8PathBuf,
    tests: Vec<CtrfTest>,
}

impl JsonReporter {
    pub fn new(path: Utf8PathBuf) -> Self {
        Self {
            path,
            tests: Vec::new(),
        }
    }
}

impl JsonReporter {
    /// Record a failure that belongs to the run rather than to a test.
    ///
    /// Collection errors and `--strict=abort` violations abort the run before
    /// any test executes. They are serialised as `failed` CTRF entries rather
    /// than left out of an empty `tests: []`, because `summary.failed == 0`
    /// makes every CTRF consumer render an aborted run as green — a quieter
    /// version of the missing-artifact bug this replaces (#1682).
    ///
    /// `name` is a file path or a node ID; `duration` is `0.0` because nothing
    /// ran.
    pub(crate) fn record_run_failure(&mut self, name: String, message: String) {
        self.tests.push(CtrfTest {
            name,
            status: "failed",
            duration: 0.0,
            message: Some(message),
        });
    }

    fn write_json(&self, output: &CtrfOutput) -> std::io::Result<()> {
        let json = serde_json::to_string_pretty(output).map_err(std::io::Error::other)?;
        std::fs::write(&self.path, json)
    }
}

impl Reporter for JsonReporter {
    fn test_completed(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
        _parallel_ctx: Option<&crate::parallel_context::ParallelContext>,
    ) {
        self.tests.push(CtrfTest {
            name: item.node_id.to_string(),
            status: outcome.ctrf_status(),
            duration: duration_ms.as_f64(),
            message: outcome.message().map(|s| s.to_string()),
        });
    }

    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        _interrupted: bool,
        _session: &super::ReporterSession,
    ) -> super::ExitVote {
        for error in collect_errors {
            self.record_run_failure(collect_error_name(error), error.to_string());
        }

        let passed = self.tests.iter().filter(|t| t.status == "passed").count();
        let failed = self.tests.iter().filter(|t| t.status == "failed").count();
        let skipped = self.tests.iter().filter(|t| t.status == "skipped").count();
        let total = self.tests.len();

        self.tests.sort_by(|a, b| a.name.cmp(&b.name));

        let output = CtrfOutput {
            results: CtrfResults {
                tool: CtrfTool { name: "oxitest" },
                summary: CtrfSummary {
                    tests: total,
                    passed,
                    failed,
                    skipped,
                    other: 0,
                },
                tests: std::mem::take(&mut self.tests),
            },
        };

        if let Err(e) = self.write_json(&output) {
            eprintln!("error: failed to write JSON report to {}: {e}", self.path);
            return super::ExitVote::Code(crate::types::ExitCode::UsageError);
        }
        super::ExitVote::Abstain
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::reporter::Reporter;
    use crate::types::{DurationMs, FailureDiagnostic, TestItem, TestOutcome};
    use insta::assert_snapshot;

    fn run_and_read(items: &[(&str, &str)]) -> String {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = camino::Utf8PathBuf::try_from(tmp.path().to_path_buf()).unwrap();
        let mut rep = JsonReporter::new(path.clone());
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
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn json_mixed_outcomes() {
        let json = run_and_read(&[
            ("test_pass", "passed"),
            ("test_fail", "failed"),
            ("test_skip", "skipped"),
            ("test_err", "error"),
        ]);
        assert_snapshot!(json);
    }

    #[test]
    fn json_all_passed() {
        let json = run_and_read(&[("test_a", "passed"), ("test_b", "passed")]);
        assert_snapshot!(json);
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
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        for (item, outcome) in &outcomes {
            rep.test_completed(item, outcome, DurationMs::new(1.0), None);
        }
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_json_includes_message_for_failed_outcome() {
        let json = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_fails").arc(),
            TestOutcome::failed("expected 42")
                .file("tests/t.py")
                .lineno(5)
                .source("assert x == 42")
                .build(),
        )]);
        assert!(
            json.contains("\"message\""),
            "message key must appear in JSON"
        );
        assert!(
            json.contains("expected 42"),
            "message value must appear in JSON"
        );
    }

    #[test]
    fn test_json_includes_message_for_error_outcome() {
        let json = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_error").arc(),
            TestOutcome::error("PyImportError: No module named 'foo'")
                .file("tests/t.py")
                .source("x")
                .build(),
        )]);
        assert!(
            json.contains("PyImportError"),
            "error message must appear in JSON"
        );
    }

    #[test]
    fn test_json_omits_message_for_passed_outcome() {
        let json = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_pass").arc(),
            TestOutcome::Passed { tips: None },
        )]);
        assert!(
            !json.contains("\"message\""),
            "message key must be absent for passed test"
        );
    }

    #[test]
    fn test_json_includes_message_for_timeout_outcome() {
        let json = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_slow").arc(),
            TestOutcome::Timeout {
                message: "test timed out after 5s".to_string(),
            },
        )]);
        assert!(
            json.contains("timed out"),
            "timeout message must appear in JSON"
        );
    }

    #[test]
    fn test_json_omits_message_for_failed_with_empty_message() {
        let json = run_reporter(vec![(
            TestItem::builder("tests/test_foo.py", "test_empty_msg").arc(),
            TestOutcome::failed("").file("tests/t.py").build(),
        )]);
        assert!(
            !json.contains("\"message\""),
            "empty message must not appear as a key in JSON"
        );
    }

    /// Run `finish` with no tests and the given collect errors, returning the
    /// parsed CTRF document. Panics if no file was written.
    fn finish_with_collect_errors(errors: &[CollectError]) -> serde_json::Value {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        rep.finish(errors, false, &crate::reporter::ReporterSession::new(0));
        assert!(
            path.exists(),
            "--json promises the file exists after the run; an aborted run that writes nothing is indistinguishable from a job that never started"
        );
        serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
    }

    #[test]
    fn test_collect_errors_are_serialized_as_failed_entries() {
        let doc = finish_with_collect_errors(&[CollectError::ImportError {
            path: camino::Utf8PathBuf::from("tests/test_bad.py"),
            message: "ModuleNotFoundError: No module named 'nope'".to_string(),
        }]);

        let summary = &doc["results"]["summary"];
        assert_eq!(
            summary["failed"], 1,
            "a collection error must count as failed — summary.failed == 0 makes every CTRF consumer render the aborted run green"
        );
        assert_eq!(
            summary["tests"], 1,
            "the failed entry must be counted in the total, or failed/tests is inconsistent"
        );
        assert_eq!(
            doc["results"]["tests"][0]["name"], "tests/test_bad.py",
            "the entry must name the file that failed to import so a dashboard can point at it"
        );
        assert!(
            doc["results"]["tests"][0]["message"]
                .as_str()
                .unwrap()
                .contains("ModuleNotFoundError"),
            "the underlying Python error must survive into the artifact — the CTRF file is all CI keeps"
        );
    }

    #[test]
    fn test_pathless_collect_error_uses_placeholder_name() {
        let doc =
            finish_with_collect_errors(&[CollectError::PyError("conftest exploded".to_string())]);

        assert_eq!(
            doc["results"]["tests"][0]["name"], "<collection>",
            "an error with no file must not borrow a node ID shape, or it could collide with a real test"
        );
        assert_eq!(
            doc["results"]["summary"]["failed"], 1,
            "a pathless collection error is still a failed run"
        );
    }

    #[test]
    fn test_run_failures_and_tests_are_summed_together() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        rep.test_completed(
            &TestItem::builder("tests/test_foo.py", "test_a").arc(),
            &TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
            None,
        );
        rep.record_run_failure("tests/test_bar.py".to_string(), "boom".to_string());
        rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));

        let doc: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(
            doc["results"]["summary"]["passed"], 1,
            "a recorded run failure must not swallow the tests that did execute"
        );
        assert_eq!(
            doc["results"]["summary"]["failed"], 1,
            "record_run_failure must reach the summary, not just the tests array"
        );
    }

    #[test]
    fn test_finish_returns_code_4_on_write_failure() {
        let path = camino::Utf8PathBuf::from("/nonexistent/dir/out.json");
        let mut rep = JsonReporter::new(path);
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
            "must return exit code 4 when JSON file cannot be written"
        );
    }

    #[test]
    fn test_finish_returns_abstain_on_successful_write() {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        rep.test_completed(
            &TestItem::builder("tests/test_foo.py", "test_a").arc(),
            &TestOutcome::Passed { tips: None },
            DurationMs::new(1.0),
            None,
        );
        let vote = rep.finish(&[], false, &crate::reporter::ReporterSession::new(0));
        assert!(
            matches!(vote, crate::reporter::ExitVote::Abstain),
            "must return Abstain on successful write"
        );
        assert!(path.exists(), "JSON file must be created");
    }
}
