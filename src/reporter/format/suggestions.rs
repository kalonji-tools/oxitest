#[cfg(test)]
use crate::types::LineNo;
use crate::types::TestOutcome;

/// Analyze a `Failed` or `Error` outcome and return a raw suggestion string
/// if the failure message matches a known error pattern. Returns `None` for
/// all other outcomes or when no pattern matches.
///
/// The caller is responsible for formatting (color, indentation, box chrome).
pub(crate) fn suggest_fix(outcome: &TestOutcome) -> Option<String> {
    let message = match outcome {
        TestOutcome::Failed { .. } | TestOutcome::Error { .. } => outcome.message()?,
        _ => return None,
    };

    if message.contains("can't be used in 'await'") || message.contains("cannot be used in 'await'")
    {
        return Some(
            "The test is async but a fixture returned a non-awaitable value. \
             Mark the fixture as `async def` or make the test synchronous."
                .to_string(),
        );
    }

    if message.contains("SharedFixtureMutationError") || message.contains("shared fixture") {
        return Some(
            "Shared fixtures are frozen to prevent cross-test mutation. \
             Use `shared=False` for a mutable per-test copy."
                .to_string(),
        );
    }

    if message.contains("fixture") && message.contains("not found") {
        return Some(
            "Check that the fixture name matches a `@fixtures.fixture` definition \
             and that the parameter is annotated with `Fixture[T]`."
                .to_string(),
        );
    }

    None
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use camino::Utf8PathBuf;

    #[test]
    fn async_mismatch_suggestion() {
        let outcome = TestOutcome::Error {
            message: "TypeError: object X can't be used in 'await' expression".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(5),
            source_line: "await fx".to_string(),
            frames: vec![],
        };
        let hint = suggest_fix(&outcome);
        insta::assert_snapshot!(hint.unwrap_or_default());
    }

    #[test]
    fn no_suggestion_for_normal_error() {
        let outcome = TestOutcome::Error {
            message: "ValueError: bad input".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(3),
            source_line: "raise ValueError".to_string(),
            frames: vec![],
        };
        let hint = suggest_fix(&outcome);
        insta::assert_snapshot!(hint.unwrap_or_default());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;

    #[test]
    fn suggest_async_mismatch() {
        let outcome = TestOutcome::Error {
            message: "TypeError: object X can't be used in 'await' expression".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(5),
            source_line: "await fx".to_string(),
            frames: vec![],
        };
        let hint = suggest_fix(&outcome);
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("async"));
    }

    #[test]
    fn suggest_shared_mutation() {
        let outcome = TestOutcome::Error {
            message: "SharedFixtureMutationError: cannot mutate".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(5),
            source_line: "fx.val = 1".to_string(),
            frames: vec![],
        };
        let hint = suggest_fix(&outcome);
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("shared=False"));
    }

    #[test]
    fn suggest_fixture_not_found() {
        let outcome = TestOutcome::Error {
            message: "fixture 'db' not found".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(5),
            source_line: "def test(db):".to_string(),
            frames: vec![],
        };
        let hint = suggest_fix(&outcome);
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("Fixture[T]"));
    }

    #[test]
    fn no_suggestion_for_generic_failure() {
        let outcome = TestOutcome::Failed {
            message: "assert 1 == 2".to_string(),
            file: Utf8PathBuf::from("test.py"),
            lineno: LineNo::new(5),
            source_line: "assert 1 == 2".to_string(),
            left: "1".to_string(),
            right: "2".to_string(),
            op: "==".to_string(),
            frames: vec![],
            field_diffs: vec![],
        };
        assert!(suggest_fix(&outcome).is_none());
    }

    #[test]
    fn no_suggestion_for_passed() {
        let outcome = TestOutcome::Passed {
            no_message_lines: vec![],
        };
        assert!(suggest_fix(&outcome).is_none());
    }
}
