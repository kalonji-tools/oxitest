"""Tests for fixture_lister tree rendering."""

from __future__ import annotations

from oxitest import helpers
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge.fixture_lister import tree_fixtures_from_registry

# All tests use use_color=False to avoid ANSI codes in assertions.


def test_tree_empty_registry_shows_builtins():
    reg = FixtureRegistry()
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "TempDir" in result, f"built-in TempDir missing: {result!r}"
    assert "fixtures" in result, f"summary line missing: {result!r}"


def test_tree_single_no_deps():
    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("db", conftest_path="conftest.py"))
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "db" in result, f"fixture name missing: {result!r}"
    # No tree branches for a leaf fixture
    assert "├" not in result.split("db")[1].split("\n")[0], (
        f"leaf fixture should not have branches: {result!r}"
    )


def test_tree_linear_chain():
    def _config():
        pass

    def _connection(config):
        pass

    def _db(connection):
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def("config", factory=_config, conftest_path="c.py")
    )
    reg.register(
        helpers.common.make_fixture_def(
            "connection",
            factory=_connection,
            conftest_path="c.py",
            depends_on=(("config", object),),
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "db",
            factory=_db,
            conftest_path="c.py",
            depends_on=(("connection", object),),
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    # db depends on connection, connection depends on config
    lines = result.split("\n")
    db_idx = next(i for i, line in enumerate(lines) if line.strip() == "db")
    assert "└── connection" in lines[db_idx + 1], f"connection not child of db: {lines}"
    assert "└── config" in lines[db_idx + 2], f"config not child of connection: {lines}"


def test_tree_diamond():
    def _base():
        pass

    def _left(base):
        pass

    def _right(base):
        pass

    def _top(left, right):
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def("base", factory=_base, conftest_path="c.py")
    )
    reg.register(
        helpers.common.make_fixture_def(
            "left",
            factory=_left,
            conftest_path="c.py",
            depends_on=(("base", object),),
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "right",
            factory=_right,
            conftest_path="c.py",
            depends_on=(("base", object),),
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "top",
            factory=_top,
            conftest_path="c.py",
            depends_on=(("left", object), ("right", object)),
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    # "top" should show both left and right as children
    lines = result.split("\n")
    top_idx = next(i for i, line in enumerate(lines) if line.strip() == "top")
    subtree = "\n".join(lines[top_idx : top_idx + 5])
    assert "left" in subtree, f"left not in top subtree: {subtree!r}"
    assert "right" in subtree, f"right not in top subtree: {subtree!r}"
    assert "base" in subtree, f"base not in top subtree: {subtree!r}"


def test_tree_cycle_detection():
    def _a(b):
        pass

    def _b(a):
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "a", factory=_a, conftest_path="c.py", depends_on=(("b", object),)
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "b", factory=_b, conftest_path="c.py", depends_on=(("a", object),)
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "Circular" in result or "circular" in result, (
        f"cycle not detected: {result!r}"
    )


def test_tree_keyword_filter():
    def _config():
        pass

    def _db(config):
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def("config", factory=_config, conftest_path="c.py")
    )
    reg.register(
        helpers.common.make_fixture_def(
            "db",
            factory=_db,
            conftest_path="c.py",
            depends_on=(("config", object),),
        )
    )
    result = tree_fixtures_from_registry(
        reg, verbosity=0, pattern="db", use_color=False
    )
    # Only "db" is a root, but its subtree (config) is shown
    lines = [ln for ln in result.split("\n") if ln.strip()]
    root_lines = [
        ln for ln in lines if not ln.startswith(" ") and not ln.startswith("─")
    ]
    assert any("db" in ln for ln in root_lines), f"db should be a root: {lines}"
    assert not any(ln.strip() == "config" for ln in root_lines), (
        "config should not be a root"
    )
    assert "config" in result, "config should appear as dep of db"


def test_tree_verbosity_1_shows_tags():
    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "db", conftest_path="c.py", shared=True, is_async=True
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "[shared" in result, f"shared tag missing: {result!r}"
    assert "async" in result, f"async tag missing: {result!r}"


def test_tree_verbosity_2_shows_origin():
    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def("db", conftest_path="tests/conftest.py")
    )
    result = tree_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "tests/conftest.py" in result, f"origin missing: {result!r}"
