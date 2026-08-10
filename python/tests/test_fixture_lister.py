"""Tests for fixture_lister tree rendering."""

from __future__ import annotations

from typing import Any

from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge.fixture_lister import tree_fixtures_from_registry
from tests import helpers

# All tests use use_color=False to avoid ANSI codes in assertions.


def test_tree_empty_registry_shows_builtins() -> None:
    """An empty user registry still renders built-in fixtures like TempDir."""
    reg = FixtureRegistry()
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "TempDir" in result, f"built-in TempDir missing: {result!r}"
    assert "fixtures" in result, f"summary line missing: {result!r}"


def test_tree_single_no_deps() -> None:
    """A leaf fixture with no dependencies renders without tree branch characters."""
    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("db", declaration_path="conftest.py"))
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "db" in result, f"fixture name missing: {result!r}"
    # No tree branches for a leaf fixture
    assert "├" not in result.split("db")[1].split("\n")[0], (
        f"leaf fixture should not have branches: {result!r}"
    )


def test_tree_linear_chain() -> None:
    """A fixture chain renders as nested tree children in dependency order."""

    def _config() -> None:
        pass

    def _connection(config: Any) -> None:
        pass

    def _db(connection: Any) -> None:
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.make_fixture_def("config", factory=_config, declaration_path="c.py")
    )
    reg.register(
        helpers.make_fixture_def(
            "connection",
            factory=_connection,
            declaration_path="c.py",
            depends_on=(("config", object),),
        )
    )
    reg.register(
        helpers.make_fixture_def(
            "db",
            factory=_db,
            declaration_path="c.py",
            depends_on=(("connection", object),),
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    # db depends on connection, connection depends on config
    lines = result.split("\n")
    db_idx = next(i for i, line in enumerate(lines) if line.strip() == "db")
    assert "└── connection" in lines[db_idx + 1], f"connection not child of db: {lines}"
    assert "└── config" in lines[db_idx + 2], f"config not child of connection: {lines}"


def test_tree_diamond() -> None:
    """A diamond dependency (shared base) shows both branches under the top fixture."""

    def _base() -> None:
        pass

    def _left(base: Any) -> None:
        pass

    def _right(base: Any) -> None:
        pass

    def _top(left: Any, right: Any) -> None:
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.make_fixture_def("base", factory=_base, declaration_path="c.py")
    )
    reg.register(
        helpers.make_fixture_def(
            "left",
            factory=_left,
            declaration_path="c.py",
            depends_on=(("base", object),),
        )
    )
    reg.register(
        helpers.make_fixture_def(
            "right",
            factory=_right,
            declaration_path="c.py",
            depends_on=(("base", object),),
        )
    )
    reg.register(
        helpers.make_fixture_def(
            "top",
            factory=_top,
            declaration_path="c.py",
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


def test_tree_cycle_detection() -> None:
    """Circular fixture dependencies are detected and labeled in the tree output."""

    def _a(b: Any) -> None:
        pass

    def _b(a: Any) -> None:
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.make_fixture_def(
            "a", factory=_a, declaration_path="c.py", depends_on=(("b", object),)
        )
    )
    reg.register(
        helpers.make_fixture_def(
            "b", factory=_b, declaration_path="c.py", depends_on=(("a", object),)
        )
    )
    result = tree_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "Circular" in result or "circular" in result, (
        f"cycle not detected: {result!r}"
    )


def test_tree_keyword_filter() -> None:
    """The pattern arg limits root-level fixtures to matches but shows their subtree."""

    def _config() -> None:
        pass

    def _db(config: Any) -> None:
        pass

    reg = FixtureRegistry()
    reg.register(
        helpers.make_fixture_def("config", factory=_config, declaration_path="c.py")
    )
    reg.register(
        helpers.make_fixture_def(
            "db",
            factory=_db,
            declaration_path="c.py",
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


def test_tree_verbosity_1_shows_tags() -> None:
    """Verbosity level 1 adds the async tag alongside each fixture name.

    The [shared] tag went with the tier it named (#1720).
    """
    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("db", declaration_path="c.py", is_async=True))
    result = tree_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "async" in result, f"async tag missing: {result!r}"


def test_tree_verbosity_2_shows_origin() -> None:
    """Verbosity 2 includes the conftest.py path where each fixture was defined."""
    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("db", declaration_path="tests/conftest.py"))
    result = tree_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "tests/conftest.py" in result, f"origin missing: {result!r}"
