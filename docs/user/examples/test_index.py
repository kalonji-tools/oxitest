"""Tested examples for the index page quick-start block."""

from oxitest import Fixture

# --8<-- [start:quickstart-test]


def test_greeting(greeting: Fixture[str]) -> None:
    assert greeting == "hello", "fixture should inject the return value"


# --8<-- [end:quickstart-test]
