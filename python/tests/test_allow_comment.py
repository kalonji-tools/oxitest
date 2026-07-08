"""Tests for parse_allow_rules — inline allow-comment parsing."""

from __future__ import annotations

from oxitest._bridge._allow_comment import parse_allow_rules


def test_single_rule() -> None:
    """A single rule in allow brackets should be extracted as a one-item set."""
    line = "fx = Fixtures()  # oxitest: allow[registrar-in-test-module]"
    assert parse_allow_rules(line) == {"registrar-in-test-module"}, (
        "should extract single rule"
    )


def test_multiple_rules() -> None:
    """Comma-separated rules inside allow brackets should all be extracted."""
    line = "fx = Fixtures()  # oxitest: allow[registrar-in-test-module, bare-assert]"
    assert parse_allow_rules(line) == {"registrar-in-test-module", "bare-assert"}, (
        "should extract comma-separated rules"
    )


def test_whitespace_inside_brackets() -> None:
    """Whitespace around rule names inside brackets is stripped."""
    line = "fx = Fixtures()  # oxitest: allow[ registrar-in-test-module , bare-assert ]"
    assert parse_allow_rules(line) == {"registrar-in-test-module", "bare-assert"}, (
        "should strip whitespace inside brackets"
    )


def test_no_match_returns_empty() -> None:
    """A comment without the oxitest: allow pattern should return an empty set."""
    line = "fx = Fixtures()  # just a comment"
    assert parse_allow_rules(line) == set(), "no allow comment should return empty set"


def test_mixed_comments() -> None:
    """An allow comment should be found even when preceded by other comment text."""
    line = "fx = Fixtures()  # my note  # oxitest: allow[registrar-in-test-module]"
    assert parse_allow_rules(line) == {"registrar-in-test-module"}, (
        "should find allow comment after other comments"
    )


def test_empty_brackets() -> None:
    """Empty brackets in an allow comment should return an empty set."""
    line = "fx = Fixtures()  # oxitest: allow[]"
    assert parse_allow_rules(line) == set(), "empty brackets should return empty set"


def test_plain_code_no_comment() -> None:
    """Code with no comment at all should return an empty set."""
    line = "fx = Fixtures()"
    assert parse_allow_rules(line) == set(), "no comment should return empty set"
