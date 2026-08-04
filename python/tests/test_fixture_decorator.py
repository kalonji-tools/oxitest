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


def test_process_lifetime_is_accepted() -> None:
    """lifetime="process" produces a working decorator with a PROCESS marker.

    This was a rejection test until slice 4 (#1711) settled the semantics, and
    the tier was spelled ``session`` until #1777 (ADR-0009 Amendment 6). The
    rename was the point of that change rather than cosmetic: the tier promised
    a per-process resource and delivered one per *task group*, so ``session``
    named a boundary the implementation never had. ``process`` is once per
    worker process, which makes its instance count whatever ``-n`` is. All four
    ADR-0009 tiers are declarable, so ``LIFETIME_SCOPES`` is total over
    ``Lifetime`` and no tier is gated any more.
    """

    @fixture(lifetime="process")
    def engine() -> str:
        return "engine"

    marker = getattr(engine, MARKER_ATTR)
    assert marker.lifetime is Lifetime.PROCESS, (
        "marker must record PROCESS so the registrar maps it to "
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


def test_autouse_is_recorded_on_the_marker() -> None:
    """@oxi.fixture(autouse=True) reaches the marker payload (#1716)."""

    @fixture(lifetime="module", autouse=True)
    def db_pool() -> str:
        return "pool"

    marker = getattr(db_pool, MARKER_ATTR)
    assert marker.autouse is True, (
        "the registrar reads autouse off this marker; if it never lands here "
        "the fixture registers as an ordinary one and silently never fires — "
        "there is no error, the setup simply does not happen"
    )


def test_autouse_defaults_to_false() -> None:
    """Existing declarations keep their meaning (#1716)."""

    @fixture(lifetime="function")
    def conn() -> str:
        return "c"

    marker = getattr(conn, MARKER_ATTR)
    assert marker.autouse is False, (
        "a default of True would turn every fixture in every existing suite "
        "into an autouse fixture on upgrade"
    )


def test_autouse_preserves_callable() -> None:
    """autouse=True does not make the decorator stop being a pure marker."""

    @fixture(lifetime="function", autouse=True)
    def conn() -> str:
        return "hello"

    assert conn() == "hello", (
        "the decorator is a pure marker at every keyword combination — "
        "wrapping the function here would break ADR-0009 Rule 1 for exactly "
        "the fixtures nobody calls directly, so nothing else would catch it"
    )
