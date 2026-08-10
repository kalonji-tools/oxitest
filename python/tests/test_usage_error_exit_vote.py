"""Acceptance tests for the run-level usage-error exit vote (#1761).

Exit code 4 means the request was invalid. Until this issue the Python bridge
had no way to say so: a suite that was wired wrong reported exit 1, which CI
cannot tell from a failed assertion.

The unit half below pins *which* errors vote. The run half pins the whole path
— Python funnel, both transports, reporter ladder — because the vote is a
property of a run rather than of any object, and because the two transports are
different code. A B1 verdict has already degraded in parallel only once
(#1713), invisible to every serial test.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._async_orchestrator import _check_async_dep
from oxitest._bridge._errors import (
    AsyncDependencyError,
    AsyncFixtureAccessError,
    BoundaryError,
    FixtureNotFoundError,
    FixtureSetupError,
    is_usage_error,
)
from oxitest._bridge.result import ErrorResult


@dataclass(frozen=True)
class WiringCase:
    """One error that must vote for ``ExitCode::UsageError``."""

    label: str
    exc: Exception


_BOUNDARY = WiringCase(
    label="fx. proxy crosses a B1 boundary",
    exc=BoundaryError("conn", "api", "/t/api", "/t/admin/test_a.py"),
)
_NOT_FOUND = WiringCase(
    label="Fixture[T] route cannot see the fixture",
    exc=FixtureNotFoundError("conn"),
)
_ASYNC_ACCESS = WiringCase(
    label="sync test reaches an async fixture",
    exc=AsyncFixtureAccessError("conn", "db", "function"),
)
_ASYNC_DEP = WiringCase(
    label="fixture dependency lifetime cannot hold",
    exc=AsyncDependencyError("outer", RuntimeError("lifetime mismatch")),
)


@oxi.parametrize(
    boundary=_BOUNDARY,
    not_found=_NOT_FOUND,
    async_access=_ASYNC_ACCESS,
    async_dep=_ASYNC_DEP,
)
def test_wiring_errors_are_usage_errors(case: WiringCase) -> None:
    """Every error in this set means the suite is wired wrong."""
    # Act / Assert
    assert is_usage_error(case.exc) is True, (
        f"{case.label}: if this error stops voting, its violation silently "
        f"reverts to exit 1 and CI reads a misconfigured suite as an ordinary "
        f"test failure — the exact defect #1761 removes"
    )


def test_a_fixture_body_exception_is_not_a_usage_error() -> None:
    """FixtureSetupError also wraps genuine exceptions from a user's fixture."""
    # Arrange
    exc = FixtureSetupError("db", ValueError("connection refused"))

    # Act / Assert
    assert is_usage_error(exc) is False, (
        "a user's fixture raising is an ordinary failure, not a wiring "
        "mistake; flagging FixtureSetupError wholesale would turn every "
        "broken fixture body into exit 4 and make the code meaningless"
    )


def test_the_async_dependency_guard_raises_a_usage_error() -> None:
    """All three refusals raise through one function, so one raise covers them."""

    # Arrange
    async def _bound_to_one_loop() -> str:
        return "a value that dies with its test's event loop"

    coro = _bound_to_one_loop()

    # Act / Assert
    with oxi.raises(AsyncDependencyError):
        _check_async_dep("short_lived", coro, "long_lived", "lifetime mismatch")


def test_an_assertion_failure_is_not_a_usage_error() -> None:
    """The two codes must stay distinguishable."""
    # Act / Assert
    assert is_usage_error(AssertionError("2 != 3")) is False, (
        "separating a misconfigured suite from a failing assertion is the "
        "whole purpose of the vote; if this returns True the codes collapse "
        "back into one and the issue has bought nothing"
    )


def test_the_wire_form_carries_the_usage_flag() -> None:
    """The worker path is LDJSON, so the flag must survive serialisation."""
    # Arrange
    flagged = ErrorResult(message="boundary", usage_error=True)
    plain = ErrorResult(message="boom")

    # Act
    flagged_wire = flagged.to_wire("t.py::test_a", 1.0)
    plain_wire = plain.to_wire("t.py::test_b", 1.0)

    # Assert
    assert flagged_wire["usage_error"] is True, (
        "a flag the coordinator never sees on the wire cannot vote, so a "
        "parallel run would silently keep exit 1 while the same suite run "
        "serially reported 4 — the split #1713 already suffered once"
    )
    assert "usage_error" not in plain_wire, (
        "an ordinary error must not carry the key; emitting false on every "
        "result would grow every line of a large run's output to state a "
        "value the coordinator already defaults to"
    )
