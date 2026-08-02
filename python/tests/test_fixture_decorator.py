"""Tests for @oxi.fixture decorator and _FixtureMarker."""

from __future__ import annotations

from oxitest import raises
from oxitest._bridge._fixture_decorator import (
    MARKER_ATTR,
    _FixtureMarker,
    fixture,
)
from oxitest._bridge._fixture_registry import LIFETIME_SCOPES
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


def test_session_lifetime_is_accepted() -> None:
    """lifetime="session" produces a working decorator with a SESSION marker.

    This was a rejection test until slice 4 (#1711) settled the semantics —
    ``session`` is once per **task group**, not once per run and not once per
    worker process (ADR-0009 Amendment 4). All four
    ADR-0009 tiers are now declarable, so ``LIFETIME_SCOPES`` is total over
    ``Lifetime`` and no tier is gated any more.
    """

    @fixture(lifetime="session")
    def engine() -> str:
        return "engine"

    marker = getattr(engine, MARKER_ATTR)
    assert marker.lifetime is Lifetime.SESSION, (
        "marker must record SESSION so the registrar maps it to "
        "FixtureScope.PROCESS — any other marker routes the fixture to "
        "function scope and rebuilds it per test"
    )
    assert engine() == "engine", (
        "the decorator is a pure marker — declaring a tier must not wrap or "
        "replace the function (ADR-0009 Rule 1)"
    )


def test_every_lifetime_tier_has_a_scope_mapping() -> None:
    """No ``Lifetime`` member may be declarable without a scope.

    The decorator's gate is membership in ``LIFETIME_SCOPES``. A member added
    without a mapping would not raise there — it would ``KeyError`` deeper in
    the registrar, far from the declaration that caused it.
    """
    missing = [tier.value for tier in Lifetime if tier not in LIFETIME_SCOPES]
    assert not missing, (
        f"these Lifetime members have no FixtureScope mapping: {missing}. "
        f"Add one, or the decorator will accept a tier the registrar cannot map"
    )


def test_unknown_lifetime_raises_value_error() -> None:
    """Unknown lifetime value raises ValueError from StrEnum lookup."""
    with raises(ValueError):
        fixture(lifetime="bogus")
