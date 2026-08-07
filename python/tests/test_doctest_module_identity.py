"""Module identity for the doctest load route (#1962 §2).

The test route AST-rewrites asserts and injects globals; the doctest route
executes source as written. They must never serve each other's module.
"""

from __future__ import annotations

from oxitest._bridge._loader import ModuleCache


def test_module_cache_keeps_the_two_load_routes_apart() -> None:
    """A path cached for one route must not be served to the other."""
    cache = ModuleCache()
    test_module = object()
    doctest_module = object()
    other_module = object()

    cache.set("/p/m.py", test_module, kind="test")
    cache.set("/p/m.py", doctest_module, kind="doctest")
    cache.set("/p/other.py", other_module, kind="test")

    assert cache.get("/p/m.py", kind="test") is test_module, (
        "the test route must get the AST-rewritten module — serving it the "
        "doctest route's copy silently stops bare asserts being rewritten"
    )
    assert cache.get("/p/m.py", kind="doctest") is doctest_module, (
        "the doctest route must get the unrewritten module — doctest examples "
        "should execute the source as written"
    )
    assert cache.get("/p/other.py", kind="test") is other_module, (
        "a cache keyed by kind alone, ignoring path, would pass the two "
        "assertions above while conflating every module that shares a kind"
    )


def test_module_cache_evict_clears_both_kinds() -> None:
    """end_module calls evict(path) once; both kinds must go."""
    cache = ModuleCache()
    other_module = object()
    cache.set("/p/m.py", object(), kind="test")
    cache.set("/p/m.py", object(), kind="doctest")
    cache.set("/p/other.py", other_module, kind="test")

    cache.evict("/p/m.py")

    assert cache.get("/p/m.py", kind="test") is None, (
        "a surviving test-route entry outlives its module group, which is the "
        "isolation ModuleCache exists to provide"
    )
    assert cache.get("/p/m.py", kind="doctest") is None, (
        "a surviving doctest entry would outlive its group too — evict() is "
        "the only call end_module makes, so it must clear everything"
    )
    assert cache.get("/p/other.py", kind="test") is other_module, (
        "evict is scoped to one path — dropping another module's entry re-runs "
        "its body mid-group, losing the module state the cache exists to keep"
    )
