"""Shared fixtures: scope tier, introspection, and connected-component groups."""

from __future__ import annotations

from collections.abc import Generator

from oxitest import Fixture, Fixtures, raises
from oxitest._bridge.proxy import FrozenProxy, SharedFixtureMutationError
from tests import helpers

# ── shared= fixture tier ───────────────────────────────────────────────────────


def test_fixture_decorator_accepts_shared_kwarg() -> None:
    """@fixture(shared=True) stores shared=True on the resulting FixtureDef."""
    reg_obj = Fixtures()

    @reg_obj.fixture(shared=True)
    def my_val() -> int:
        return 42

    defn = reg_obj.defs[0]
    assert defn.shared is True, (
        f"@fixture(shared=True) should set defn.shared=True, got {defn.shared!r}"
    )
    assert defn.name == "my_val", f"fixture name should be 'my_val', got {defn.name!r}"


def test_fixture_decorator_default_shared_is_false() -> None:
    """@fixture without shared= defaults to shared=False on the FixtureDef."""
    reg_obj = Fixtures()

    @reg_obj.fixture
    def my_val() -> int:
        return 42

    defn = reg_obj.defs[0]
    assert defn.shared is False, (
        f"default @fixture (no shared=) should have defn.shared=False, got "
        f"{defn.shared!r}"
    )


def test_shared_fixture_is_called_once_across_tests() -> None:
    """A shared fixture factory is called once; subsequent resolutions use the cache."""
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    session = helpers.make_session(
        helpers.make_fixture_def("db", factory, shared=True, conftest_path="/c.py")
    )

    def fn(db: Fixture[int]) -> None:
        pass

    k1, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert len(calls) == 1, f"factory called {len(calls)} times, expected 1"
    # Both resolutions return the same proxy instance (cache hit)
    assert k1["db"] is k2["db"], "same FrozenProxy instance expected on cache hit"


def test_shared_fixture_value_is_wrapped_in_frozen_proxy() -> None:
    """Shared fixture values are wrapped in a FrozenProxy to prevent mutation."""

    def factory() -> dict[str, int]:
        return {"x": 1}

    session = helpers.make_session(
        helpers.make_fixture_def("cfg", factory, shared=True, conftest_path="/c.py")
    )

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert isinstance(k["cfg"], FrozenProxy), (
        f"shared fixture should be wrapped in a FrozenProxy, got "
        f"{type(k['cfg']).__name__}"
    )


def test_shared_fixture_proxy_raises_on_item_mutation() -> None:
    """__setitem__ on a shared fixture value raises SharedFixtureMutationError."""

    def factory() -> dict[str, int]:
        return {"x": 1}

    session = helpers.make_session(
        helpers.make_fixture_def("cfg", factory, shared=True, conftest_path="/c.py")
    )

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    with raises(SharedFixtureMutationError):
        k["cfg"]["x"] = 2


def test_shared_fixture_teardown_runs_on_end_session() -> None:
    """Shared yield fixture teardown is deferred to end_session, not end_module."""
    torn_down: list[bool] = []

    def factory() -> Generator[str]:
        yield "v"
        torn_down.append(True)

    session = helpers.make_session(
        helpers.make_fixture_def("res", factory, shared=True, conftest_path="/c.py")
    )

    def fn(res: Fixture[str]) -> None:
        pass

    session.resolve_for_test(fn, helpers.make_meta("t.py"))
    session.end_module("t.py")
    assert not torn_down, "teardown must not run at end_module for shared fixtures"
    session.end_session()
    assert torn_down == [True], "teardown must run at end_session"


# ── Shared fixtures introspection ──────────────────────────────────────────────


def test_shared_fixture_names_uses_most_local_definition() -> None:
    """shared_fixture_names() omits fixtures whose most-local definition is unshared."""
    # Root conftest defines db as shared; leaf conftest overrides it as non-shared.
    # shared_fixture_names() should NOT include "db" because the effective definition
    # (defs[-1]) has shared=False.
    session = helpers.make_session(
        helpers.make_fixture_def("db", shared=True, conftest_path="/root/conftest.py"),
        helpers.make_fixture_def("db", conftest_path="/root/sub/conftest.py"),
    )
    assert session.shared_fixture_names() == (), (
        "shared_fixture_names() should use only the most-local definition; "
        "a root shared=True overridden by leaf shared=False should not appear"
    )


def test_shared_fixture_names_returns_empty_when_no_shared() -> None:
    """shared_fixture_names() returns an empty list when no fixture has shared=True."""
    session = helpers.make_session(
        helpers.make_fixture_def("client", conftest_path="/c.py")
    )
    assert session.shared_fixture_names() == (), (
        "shared_fixture_names() should return [] when no fixture has shared=True"
    )


def test_shared_fixture_names_returns_only_shared_names() -> None:
    """shared_fixture_names() returns a sorted list of only the shared fixture names."""
    session = helpers.make_session(
        helpers.make_fixture_def("db", shared=True, conftest_path="/c.py"),
        helpers.make_fixture_def("cache", shared=True, conftest_path="/c.py"),
        helpers.make_fixture_def("client", conftest_path="/c.py"),
    )
    assert session.shared_fixture_names() == ("cache", "db"), (
        "shared_fixture_names() should return only names where shared=True, got "
        f"{session.shared_fixture_names()!r}"
    )


# ── Shared fixture groups (connected components) ──────────────────────────────


def test_shared_fixture_groups_empty_registry() -> None:
    """shared_fixture_groups() returns an empty list when no fixtures are registered."""
    session = helpers.make_session()
    assert session.shared_fixture_groups() == (), (
        "empty registry should return no fixture groups"
    )


def test_shared_fixture_groups_no_shared_fixtures() -> None:
    """shared_fixture_groups() returns an empty list when no fixture has shared=True."""
    session = helpers.make_session(
        helpers.make_fixture_def("store", conftest_path="/conftest.py")
    )
    assert session.shared_fixture_groups() == (), (
        "registry with no shared fixtures should return no groups"
    )


def test_shared_fixture_groups_single_shared() -> None:
    """A single shared fixture forms its own group of one."""
    session = helpers.make_session(
        helpers.make_fixture_def("db", shared=True, conftest_path="/conftest.py")
    )
    groups = session.shared_fixture_groups()
    assert groups == (("db",),), (
        f"single shared fixture should produce one group, got {groups}"
    )


def test_shared_fixture_groups_transitive_dependency() -> None:
    """A non-shared fixture depending on a shared fixture joins that fixture's group."""

    class _DbType:
        pass

    session = helpers.make_session(
        helpers.make_fixture_def(
            "db", shared=True, conftest_path="/conftest.py", fixture_type=_DbType
        ),
        helpers.make_fixture_def(
            "repo",
            conftest_path="/conftest.py",
            depends_on=(("db", _DbType),),
        ),
    )
    groups = session.shared_fixture_groups()
    assert groups == (("db", "repo"),), (
        f"repo depends on shared db — should form one group, got {groups}"
    )


def test_shared_fixture_groups_two_independent_shared() -> None:
    """Two independent shared fixtures each form their own group."""
    session = helpers.make_session(
        helpers.make_fixture_def("db", shared=True, conftest_path="/conftest.py"),
        helpers.make_fixture_def("cache", shared=True, conftest_path="/conftest.py"),
    )
    groups = session.shared_fixture_groups()
    assert len(groups) == 2, (
        f"two independent shared fixtures should produce two groups, got {groups}"
    )
    flat = [name for g in groups for name in g]
    assert sorted(flat) == ["cache", "db"], (
        f"groups should contain db and cache, got {flat}"
    )


def test_shared_fixture_groups_transitive_merge() -> None:
    """A fixture depending on two shared fixtures merges all three into one group."""

    class _DbType:
        pass

    class _CacheType:
        pass

    session = helpers.make_session(
        helpers.make_fixture_def(
            "db", shared=True, conftest_path="/c.py", fixture_type=_DbType
        ),
        helpers.make_fixture_def(
            "cache", shared=True, conftest_path="/c.py", fixture_type=_CacheType
        ),
        helpers.make_fixture_def(
            "service",
            conftest_path="/c.py",
            depends_on=(("db", _DbType), ("cache", _CacheType)),
        ),
    )
    groups = session.shared_fixture_groups()
    # service links db+cache into one connected component
    assert len(groups) == 1, (
        f"service links db+cache — should merge into one group, got {groups}"
    )
    assert sorted(groups[0]) == ["cache", "db", "service"], (
        f"merged group should contain all three fixtures, got {groups[0]}"
    )
