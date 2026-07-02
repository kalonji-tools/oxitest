from __future__ import annotations

from oxitest._bridge._allow_comment import parse_allow_rules


def test_single_rule() -> None:
    line = "fx = Fixtures()  # oxitest: allow[registrar-in-test-module]"
    assert parse_allow_rules(line) == {"registrar-in-test-module"}, (
        "should extract single rule"
    )


def test_multiple_rules() -> None:
    line = "fx = Fixtures()  # oxitest: allow[registrar-in-test-module, bare-assert]"
    assert parse_allow_rules(line) == {"registrar-in-test-module", "bare-assert"}, (
        "should extract comma-separated rules"
    )


def test_whitespace_inside_brackets() -> None:
    line = "fx = Fixtures()  # oxitest: allow[ registrar-in-test-module , bare-assert ]"
    assert parse_allow_rules(line) == {"registrar-in-test-module", "bare-assert"}, (
        "should strip whitespace inside brackets"
    )


def test_no_match_returns_empty() -> None:
    line = "fx = Fixtures()  # just a comment"
    assert parse_allow_rules(line) == set(), "no allow comment should return empty set"


def test_mixed_comments() -> None:
    line = "fx = Fixtures()  # my note  # oxitest: allow[registrar-in-test-module]"
    assert parse_allow_rules(line) == {"registrar-in-test-module"}, (
        "should find allow comment after other comments"
    )


def test_empty_brackets() -> None:
    line = "fx = Fixtures()  # oxitest: allow[]"
    assert parse_allow_rules(line) == set(), "empty brackets should return empty set"


def test_plain_code_no_comment() -> None:
    line = "fx = Fixtures()"
    assert parse_allow_rules(line) == set(), "no comment should return empty set"
