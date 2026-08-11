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

    // Matched on the error class alone. The prose clause that also matched
    // "shared fixture" collected _async_orchestrator.py's lifetime-mismatch
    // error, which this hint does not answer (#2036). The class name always
    // reaches here: the rendered message is "<ErrorClass>: <message>".
    if message.contains("SharedFixtureMutationError") {
        return Some(
            "A fixture value that outlives one test is frozen to prevent cross-test \
             mutation. Declare the fixture with `lifetime=\"function\"` for a mutable \
             per-test copy."
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
        assert!(hint.unwrap().contains("lifetime=\"function\""));
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
    fn no_suggestion_for_async_lifetime_mismatch() {
        // _async_orchestrator.py rejects a fixture that depends on an async
        // fixture of a narrower lifetime. This hint answers a frozen-value
        // mutation, not a lifetime mismatch (#2036).
        //
        // Both spellings are asserted on purpose. The first is the message that
        // shipped before #2036: it carries the phrase "shared fixture", which
        // the retired prose clause matched, so it is the only input that can
        // detect the clause coming back. The second is what the same function
        // emits today, and is the live path. Dropping either one leaves a real
        // route uncovered.
        for message in [
            "Error in fixture 'outer': shared fixture 'outer' cannot depend on \
             non-shared async fixture 'inner' — lifetime mismatch",
            "Error in fixture 'outer': fixture 'outer' cannot depend on \
             async fixture 'inner' — lifetime mismatch",
        ] {
            let outcome = TestOutcome::error(message)
                .file("test.py")
                .lineno(5)
                .source("def test(outer):")
                .build();
            assert!(suggest_fix(&outcome).is_none());
        }
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
