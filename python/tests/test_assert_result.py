"""Tests for assert_result's narrowing and failure messages (#1791)."""

from __future__ import annotations

from oxitest import raises
from oxitest._bridge.result import FailedResult, PassedResult
from tests import helpers


def test_assert_result_returns_the_narrowed_variant() -> None:
    """The return value is typed and valued as the requested variant, not the union."""
    source = PassedResult()
    narrowed = helpers.assert_result(source, PassedResult, why="baseline")

    assert narrowed is source, (
        "assert_result must hand back the same object it narrowed -- callers chain"
        " variant-specific field access onto this return value"
    )
    assert narrowed.no_message_lines == (), (
        "the narrowed return type must expose variant-specific fields -- this access"
        " is what fails to type-check if the annotation reverts to the union"
    )


def test_assert_result_omits_the_separator_when_why_is_absent() -> None:
    """Without a why, the message must not trail a dangling separator."""
    with raises(AssertionError, match=r"got PassedResult\Z"):
        helpers.assert_result(PassedResult(), FailedResult)


def test_assert_result_reports_the_actual_variant_on_mismatch() -> None:
    """A wrong variant names both sides, so the developer sees what arrived."""
    with raises(AssertionError, match="expected FailedResult, got PassedResult"):
        helpers.assert_result(PassedResult(), FailedResult, why="irrelevant")


def test_assert_result_surfaces_the_why_on_mismatch() -> None:
    """The why text reaches the failure output -- it is the only explanatory channel."""
    with raises(AssertionError, match="executor must not mangle results"):
        helpers.assert_result(
            PassedResult(), FailedResult, why="executor must not mangle results"
        )


def test_assert_result_surfaces_the_why_on_none() -> None:
    """A None result explains itself too -- 'got None' alone is not actionable."""
    with raises(AssertionError, match=r"got None\nthe executor must return a result"):
        helpers.assert_result(
            None, PassedResult, why="the executor must return a result"
        )


def test_assert_result_reports_the_actual_results_message() -> None:
    """The wrong variant's own message is quoted -- it is usually the diagnosis."""
    with raises(AssertionError, match=r"got FailedResult\('the inner test blew up'\)"):
        helpers.assert_result(
            FailedResult(message="the inner test blew up"),
            PassedResult,
            why="the executor must not mangle results",
        )


def test_assert_result_checks_every_field_not_just_the_first() -> None:
    """A mismatch in the second field is caught -- the loop must not stop at one.

    ``why`` is a named parameter, so it never lands in ``**fields``; the two
    field kwargs below are what make this distinct from the single-field case.
    """
    with raises(AssertionError, match=r"FailedResult\.lineno: expected 99, got 7"):
        helpers.assert_result(
            FailedResult(message="boom", lineno=7),
            FailedResult,
            why="every declared field is part of the contract",
            message="boom",
            lineno=99,
        )


def test_assert_result_reports_a_field_mismatch_with_the_why() -> None:
    """Field assertions carry the why too, so **fields failures stay explicable."""
    with raises(
        AssertionError, match=r"no_message_lines[\s\S]*bare asserts must be tracked"
    ):
        helpers.assert_result(
            PassedResult(no_message_lines=(1,)),
            PassedResult,
            why="bare asserts must be tracked",
            no_message_lines=(),
        )
