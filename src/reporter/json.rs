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
    fn write_json(&self, output: &CtrfOutput) -> std::io::Result<()> {
        let json = serde_json::to_string_pretty(output).map_err(std::io::Error::other)?;
        std::fs::write(&self.path, json)
    }
}

impl Reporter for JsonReporter {
    fn test_started(&mut self, _item: &TestItem) {}

    fn test_completed(&mut self, item: &TestItem, outcome: &TestOutcome, duration_ms: DurationMs) {
        self.tests.push(CtrfTest {
            name: item.node_id.to_string(),
            status: outcome.ctrf_status(),
            duration: duration_ms.as_f64(),
            message: outcome.message().map(|s| s.to_string()),
        });
    }

    fn finish(
        &mut self,
        _collect_errors: &[CollectError],
        _interrupted: bool,
        _stats: &super::RunStats,
    ) -> super::ExitVote {
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
    use crate::reporter::test_helpers::make_outcome;
    use crate::reporter::Reporter;
    use crate::types::DurationMs;
    use crate::types::TestItem;
    use insta::assert_snapshot;

    fn run_and_read(items: &[(&str, &str)]) -> String {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = camino::Utf8PathBuf::try_from(tmp.path().to_path_buf()).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        for (name, status) in items {
            let item = TestItem::builder("tests/test_foo.py", name).arc();
            let outcome = make_outcome(status);
            rep.test_started(&item);
            rep.test_completed(&item, &outcome, DurationMs::ZERO);
        }
        rep.finish(&[], false, &crate::reporter::RunStats::new());
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
    use crate::types::{LineNo, TestOutcome};
    use tempfile::TempDir;

    fn run_reporter(outcomes: Vec<(std::sync::Arc<TestItem>, TestOutcome)>) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        for (item, outcome) in &outcomes {
            rep.test_completed(item, outcome, DurationMs::new(1.0));
        }
        rep.finish(&[], false, &crate::reporter::RunStats::new());
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
            TestOutcome::Passed {
                no_message_lines: vec![],
            },
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
            TestOutcome::Failed {
                message: String::new(),
                file: Utf8PathBuf::from("tests/t.py"),
                lineno: LineNo::new(1),
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
                frames: vec![],
                field_diffs: vec![],
            },
        )]);
        assert!(
            !json.contains("\"message\""),
            "empty message must not appear as a key in JSON"
        );
    }

    #[test]
    fn test_finish_returns_code_4_on_write_failure() {
        let path = camino::Utf8PathBuf::from("/nonexistent/dir/out.json");
        let mut rep = JsonReporter::new(path);
        rep.test_completed(
            &TestItem::builder("tests/test_foo.py", "test_a").arc(),
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        let vote = rep.finish(&[], false, &crate::reporter::RunStats::new());
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
            &TestOutcome::Passed {
                no_message_lines: vec![],
            },
            DurationMs::new(1.0),
        );
        let vote = rep.finish(&[], false, &crate::reporter::RunStats::new());
        assert!(
            matches!(vote, crate::reporter::ExitVote::Abstain),
            "must return Abstain on successful write"
        );
        assert!(path.exists(), "JSON file must be created");
    }
}
