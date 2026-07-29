"""Tests for @oxi.fixture decorator and _FixtureMarker."""

from __future__ import annotations

from oxitest import raises
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


def test_module_lifetime_is_accepted() -> None:
    """lifetime="module" produces a working decorator with a MODULE marker."""

    @fixture(lifetime="module")
    def pool() -> str:
        return "pool"

    marker = getattr(pool, MARKER_ATTR)
    assert marker.lifetime is Lifetime.MODULE, (
        "marker must record MODULE so the registrar can map it to "
        "FixtureScope.MODULE — a FUNCTION marker here silently downgrades the "
        "fixture to per-test"
    )
    assert pool() == "pool", (
        "the decorator is a pure marker — declaring a tier must not wrap or "
        "replace the function (ADR-0009 Rule 1)"
    )


def test_session_lifetime_rejected_with_slice_pointer() -> None:
    """`session` raises UsageError naming the slice that owns it.

    The pointer matters: without it a user hitting this has no way to tell a
    typo from a tier that simply has not landed yet.

    This was a two-case parametrize until slice 3 (#1710) landed ``package``.
    ``session`` stays rejected until #1711 decides whether it means per-run or
    per-worker — shipping it early would be guessing at the semantics that
    issue exists to settle.
    """
    with raises(UsageError, match="1711"):
        fixture(lifetime="session")


def test_unknown_lifetime_raises_value_error() -> None:
    """Unknown lifetime value raises ValueError from StrEnum lookup."""
    with raises(ValueError):
        fixture(lifetime="bogus")
