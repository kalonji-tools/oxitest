"""Unit tests: fixture cache hit/miss tracking."""

from __future__ import annotations

from oxitest._bridge._fixture_session import _Scope


def test_scope_miss_on_first_access():
    """First access to a name is a cache miss."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    assert scope._misses["db"] == 1, f"expected 1 miss, got {scope._misses}"
    assert scope._hits.get("db", 0) == 0, f"expected 0 hits, got {scope._hits}"


def test_scope_hit_on_second_access():
    """Second access to same name is a cache hit."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("db", lambda: "should not call")
    assert scope._hits["db"] == 1, f"expected 1 hit, got {scope._hits}"
    assert scope._misses["db"] == 1, f"expected 1 miss, got {scope._misses}"


def test_scope_multiple_fixtures_tracked_independently():
    """Each fixture name gets its own hit/miss counters."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("cache", lambda: "redis")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("cache", lambda: "x")
    assert scope._hits["db"] == 2, f"db hits: {scope._hits['db']}"
    assert scope._misses["db"] == 1, f"db misses: {scope._misses['db']}"
    assert scope._hits["cache"] == 1, f"cache hits: {scope._hits['cache']}"
    assert scope._misses["cache"] == 1, f"cache misses: {scope._misses['cache']}"


def test_get_cache_stats_shape():
    """Verify the shape of cache stats from _Scope data."""
    scope = _Scope()
    scope.get_or_create("db", lambda: "conn")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("db", lambda: "x")
    scope.get_or_create("cache", lambda: "redis")

    total_hits = sum(scope._hits.values())
    total_misses = sum(scope._misses.values())
    assert total_hits == 2, f"expected 2 total hits, got {total_hits}"
    assert total_misses == 2, f"expected 2 total misses, got {total_misses}"
