use crate::types::TestOutcome;

/// Analyze a `Failed` or `Error` outcome and return a raw suggestion string
/// if the failure message matches a known error pattern. Returns `None` for
/// all other outcomes or when no pattern matches.
///
/// The caller is responsible for formatting (color, indentation, box chrome).
pub fn suggest_fix(outcome: &TestOutcome) -> Option<String> {
    let message = match outcome {
        TestOutcome::Failed(..) | TestOutcome::Error(..) => outcome.message()?,
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

    None
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;

    #[test]
    fn async_mismatch_suggestion() {
        let outcome = TestOutcome::error("TypeError: object X can't be used in 'await' expression")
            .file("test.py")
            .lineno(5)
            .source("await fx")
            .build();
        let hint = suggest_fix(&outcome);
        insta::assert_snapshot!(hint.unwrap_or_default());
    }

    #[test]
    fn no_suggestion_for_normal_error() {
        let outcome = TestOutcome::error("ValueError: bad input")
            .file("test.py")
            .lineno(3)
            .source("raise ValueError")
            .build();
        let hint = suggest_fix(&outcome);
        insta::assert_snapshot!(hint.unwrap_or_default());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggest_async_mismatch() {
        let outcome = TestOutcome::error("TypeError: object X can't be used in 'await' expression")
            .file("test.py")
            .lineno(5)
            .source("await fx")
            .build();
        let hint = suggest_fix(&outcome);
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("async"));
    }

    #[test]
    fn suggest_shared_mutation() {
        let outcome = TestOutcome::error("SharedFixtureMutationError: cannot mutate")
            .file("test.py")
            .lineno(5)
            .source("fx.val = 1")
            .build();
        let hint = suggest_fix(&outcome);
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("shared=False"));
    }

    #[test]
    fn no_suggestion_for_fixture_not_found() {
        // FixtureNotFoundError's Python-side message (_default_fixture_not_found_message
        // in python/oxitest/_bridge/_errors.py) already names the namespace, both
        // declaration routes (@oxi.fixture in __fixtures__.py or a plugin), and the
        // Fixture[<type>] annotation requirement. A Rust-side hint here would either
        // repeat that or, worse, contradict it with the legacy `@fixtures.fixture` name.
        let outcome = TestOutcome::error("fixture 'db' not found")
            .file("test.py")
            .lineno(5)
            .source("def test(db):")
            .build();
        assert!(suggest_fix(&outcome).is_none());
    }

    #[test]
    fn no_suggestion_for_generic_failure() {
        let outcome = TestOutcome::failed("assert 1 == 2")
            .file("test.py")
            .lineno(5)
            .source("assert 1 == 2")
            .comparison("1", "==", "2")
            .build();
        assert!(suggest_fix(&outcome).is_none());
    }

    #[test]
    fn no_suggestion_for_passed() {
        let outcome = TestOutcome::Passed { tips: None };
        assert!(suggest_fix(&outcome).is_none());
    }
}
