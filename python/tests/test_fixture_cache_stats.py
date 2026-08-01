"""Unit tests: fixture cache hit/miss tracking."""

from __future__ import annotations

from oxitest._bridge._fixture_session import _Scope
from tests import helpers


def test_scope_miss_on_first_access() -> None:
    """First access to a name is a cache miss."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    assert scope.misses["db"] == 1, f"expected 1 miss, got {scope.misses}"
    assert scope.hits.get("db", 0) == 0, f"expected 0 hits, got {scope.hits}"


def test_scope_hit_on_second_access() -> None:
    """Second access to same name is a cache hit."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("db", lambda: "should not call")
    assert scope.hits["db"] == 1, f"expected 1 hit, got {scope.hits}"
    assert scope.misses["db"] == 1, f"expected 1 miss, got {scope.misses}"


def test_scope_multiple_fixtures_tracked_independently() -> None:
    """Each fixture name gets its own hit/miss counters."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("cache", lambda: "redis")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("cache", lambda: "x")
    assert scope.hits["db"] == 2, f"db hits: {scope.hits['db']}"
    assert scope.misses["db"] == 1, f"db misses: {scope.misses['db']}"
    assert scope.hits["cache"] == 1, f"cache hits: {scope.hits['cache']}"
    assert scope.misses["cache"] == 1, f"cache misses: {scope.misses['cache']}"


def test_get_cache_stats_shape() -> None:
    """Verify the shape of cache stats from _Scope data."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("cache", lambda: "redis")

    total_hits = sum(scope.hits.values())
    total_misses = sum(scope.misses.values())
    assert total_hits == 2, f"expected 2 total hits, got {total_hits}"
    assert total_misses == 2, f"expected 2 total misses, got {total_misses}"


def test_function_tier_counters_fold_at_test_dispose() -> None:
    """Per-test cache stats survive the test's dispose instead of vanishing.

    Module and package scopes fold their counters into session aggregates
    before being discarded; the per-test function scope (#1775) must do the
    same, or its hit/miss data is recorded and then thrown away every test.
    """
    # Arrange — one function-lifetime fixture; a session with an active test.
    session = helpers.make_session_with("db", object)
    meta = helpers.make_meta("t.py")

    def _test_fn() -> None:
        pass

    # Act — resolve once (miss), access again within the same test window
    # (hit — the arrange-phase route the executor uses), then drain teardowns
    # exactly as the executor does: in reverse, dispose last.
    _, teardowns = session.resolve_for_test(_test_fn, meta)
    session.get_fixture_by_name("db", meta.module_path, teardowns)
    session.get_fixture_by_name("db", meta.module_path, teardowns)
    for teardown in reversed(teardowns):
        teardown()

    # Assert
    assert session._function_hits["db"] == 1, (  # noqa: SLF001 — no public API reads the folded per-test counters yet; the fold itself is what's under test
        "the second same-test access hit the per-test cache; losing that count "
        "at dispose means function-tier caching is invisible to any future "
        "stats consumer, unlike the module/package tiers which fold"
    )
    assert session._function_misses["db"] == 1, (  # noqa: SLF001 — same: the aggregate is internal until a reporting decision surfaces it
        "the first access built the fixture; the miss must fold with the hit "
        "or the aggregate misstates the tier's build count"
    )
