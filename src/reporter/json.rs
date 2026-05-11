use std::fs;
use std::io::Write;

use camino::Utf8PathBuf;

use serde::Serialize;

use crate::types::{CollectError, TestItem, TestOutcome};

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

fn outcome_status(outcome: &TestOutcome) -> &'static str {
    match outcome {
        TestOutcome::Passed { .. } | TestOutcome::Warned { .. } => "passed",
        TestOutcome::Failed { .. } => "failed",
        TestOutcome::Error { .. } => "failed",
        TestOutcome::Skipped { .. } => "skipped",
        TestOutcome::XFailed { .. } => "skipped",
        TestOutcome::XPassed { strict: true } => "failed",
        TestOutcome::XPassed { strict: false } => "passed",
        TestOutcome::Timeout { .. } => "failed",
    }
}

fn non_empty(s: &str) -> Option<String> {
    if s.is_empty() {
        None
    } else {
        Some(s.to_owned())
    }
}

fn outcome_message(outcome: &TestOutcome) -> Option<String> {
    match outcome {
        TestOutcome::Failed { message, .. } => non_empty(message),
        TestOutcome::Error { message, .. } => non_empty(message),
        TestOutcome::Timeout { message } => non_empty(message),
        _ => None,
    }
}

impl Reporter for JsonReporter {
    fn test_started(&mut self, _item: &TestItem) {}

    fn test_completed(&mut self, item: &TestItem, outcome: &TestOutcome, duration_ms: f64) {
        self.tests.push(CtrfTest {
            name: item.node_id.to_string(),
            status: outcome_status(outcome),
            duration: duration_ms,
            message: outcome_message(outcome),
        });
    }

    fn finish(&mut self, _collect_errors: &[CollectError], _interrupted: bool) -> i32 {
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

        match serde_json::to_string_pretty(&output) {
            Ok(json) => match fs::File::create(&self.path) {
                Ok(mut f) => {
                    if let Err(e) = f.write_all(json.as_bytes()) {
                        tracing::error!(
                            path = %self.path,
                            error = %e,
                            "failed to write JSON output"
                        );
                    }
                }
                Err(e) => tracing::error!(
                    path = %self.path,
                    error = %e,
                    "failed to create JSON output file"
                ),
            },
            Err(e) => tracing::error!(error = %e, "failed to serialise JSON output"),
        }

        // JsonReporter does not determine the exit code — CompositeReporter uses max()
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporter::test_helpers::{make_error, make_failed, make_item};
    use crate::types::TestOutcome;
    use tempfile::TempDir;

    fn run_reporter(outcomes: Vec<(TestItem, TestOutcome)>) -> String {
        let dir = TempDir::new().unwrap();
        let path = camino::Utf8PathBuf::from_path_buf(dir.path().join("out.json")).unwrap();
        let mut rep = JsonReporter::new(path.clone());
        for (item, outcome) in outcomes {
            rep.test_completed(&item, &outcome, 1.0);
        }
        rep.finish(&[], false);
        std::fs::read_to_string(&path).unwrap()
    }

    #[test]
    fn test_json_includes_message_for_failed_outcome() {
        let json = run_reporter(vec![(
            make_item("test_fails"),
            make_failed("expected 42", "tests/t.py", 5, "assert x == 42"),
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
            make_item("test_error"),
            make_error("PyImportError: No module named 'foo'", "tests/t.py", 1, "x"),
        )]);
        assert!(
            json.contains("PyImportError"),
            "error message must appear in JSON"
        );
    }

    #[test]
    fn test_json_omits_message_for_passed_outcome() {
        let json = run_reporter(vec![(
            make_item("test_pass"),
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
            make_item("test_slow"),
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
            make_item("test_empty_msg"),
            TestOutcome::Failed {
                message: String::new(),
                file: "tests/t.py".to_string(),
                lineno: 1,
                source_line: String::new(),
                left: String::new(),
                right: String::new(),
                op: String::new(),
            },
        )]);
        assert!(
            !json.contains("\"message\""),
            "empty message must not appear as a key in JSON"
        );
    }
}
