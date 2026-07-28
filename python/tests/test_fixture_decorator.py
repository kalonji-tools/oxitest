"""Tests for @oxi.fixture decorator and _FixtureMarker."""

from __future__ import annotations

from dataclasses import dataclass

from oxitest import parametrize, raises
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import (
    MARKER_ATTR,
    _FixtureMarker,
    fixture,
)
from oxitest._bridge._lifetime import Lifetime


def test_decorator_writes_marker_attribute() -> None:
    """Decorator writes _FixtureMarker to __oxitest_fixture__ attribute."""

    @fixture(lifetime="function")
    def conn() -> object:
        return object()

    marker = getattr(conn, MARKER_ATTR)
    assert isinstance(marker, _FixtureMarker), (
        "decorator must write a _FixtureMarker instance for the "
        "Python-import fallback discovery path to find it"
    )
    assert marker.lifetime is Lifetime.FUNCTION, (
        "marker must record the requested lifetime for the registrar"
    )


def test_decorator_preserves_callable() -> None:
    """Decorated function remains callable with original behavior."""

    @fixture(lifetime="function")
    def conn() -> str:
        return "hello"

    assert conn() == "hello", (
        "decorator is a pure marker — the wrapped function must still be "
        "callable with its original behavior (ADR-0009 Rule 1)"
    )


@dataclass(frozen=True)
class _RejectedTier:
    lifetime: str


@parametrize(
    module=_RejectedTier(lifetime="module"),
    package=_RejectedTier(lifetime="package"),
    session=_RejectedTier(lifetime="session"),
)
def test_slice1_rejects_non_function_lifetime(case: _RejectedTier) -> None:
    """Slice 1 rejects non-function lifetimes with UsageError."""
    with raises(UsageError, match="not yet supported"):
        fixture(lifetime=case.lifetime)


def test_unknown_lifetime_raises_value_error() -> None:
    """Unknown lifetime value raises ValueError from StrEnum lookup."""
    with raises(ValueError):
        fixture(lifetime="bogus")
