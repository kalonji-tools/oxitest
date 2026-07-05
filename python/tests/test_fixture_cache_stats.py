"""Unit tests: fixture cache hit/miss tracking."""

from __future__ import annotations

from oxitest._bridge._fixture_session import _Scope


def test_scope_miss_on_first_access():
    """First access to a name is a cache miss."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    assert scope.misses["db"] == 1, f"expected 1 miss, got {scope.misses}"
    assert scope.hits.get("db", 0) == 0, f"expected 0 hits, got {scope.hits}"


def test_scope_hit_on_second_access():
    """Second access to same name is a cache hit."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("db", lambda: "should not call")
    assert scope.hits["db"] == 1, f"expected 1 hit, got {scope.hits}"
    assert scope.misses["db"] == 1, f"expected 1 miss, got {scope.misses}"


def test_scope_multiple_fixtures_tracked_independently():
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


def test_get_cache_stats_shape():
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
