"""End-to-end integration tests for fixture graph stability.

Verify the full Python-to-Rust graph building pipeline: fixture edges are
reciprocal between tests and fixtures, scope annotations are reflected in
query output, and cross-conftest dependency edges are correctly resolved.
"""

from __future__ import annotations

import json

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def _fixture_entries(tmp: TempDir) -> list[dict[str, str]]:
    """Run ``query fixtures --jsonl`` and return parsed entries."""
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "fixtures", "--jsonl")
    integ.assert_passed(out, rc)
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


def _test_entries(tmp: TempDir) -> list[dict[str, str]]:
    """Run ``query tests --jsonl`` and return parsed entries."""
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--jsonl")
    integ.assert_passed(out, rc)
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


# ── test_fixture_edges_are_reciprocal ─────────────────────────────────────────


def test_fixture_edges_are_reciprocal(tmp: TempDir) -> None:
    """Each test depending on a fixture must have that fixture present in the graph.

    Sets up a conftest defining two fixtures and a test file that uses both via
    ``Fixture[T]``.  Queries both tests and fixtures, then verifies that every
    fixture the test file declares as a dependency actually exists in the fixture
    registry returned by ``query fixtures``.
    """
    # ── Arrange ──────────────────────────────────────────────────────────
    integ.write_project(
        tmp,
        tests={
            "test_reciprocal.py": """\
                from oxitest import Fixture

                def test_needs_db(db: Fixture[str]):
                    assert db == "conn"

                def test_needs_cache(cache: Fixture[int]):
                    assert cache == 42

                def test_needs_both(db: Fixture[str], cache: Fixture[int]):
                    assert db == "conn"
                    assert cache == 42
            """,
        },
        declarations="""\
            from oxitest import fixture

            @fixture(lifetime="function")
            def db() -> str:
                return "conn"

            @fixture(lifetime="function")
            def cache() -> int:
                return 42
        """,
    )

    # ── Act ──────────────────────────────────────────────────────────────
    fixtures = _fixture_entries(tmp)
    tests = _test_entries(tmp)

    # ── Assert ───────────────────────────────────────────────────────────
    fixture_names = {f["name"] for f in fixtures}
    expected_fixtures = {"db", "cache"}
    for name in expected_fixtures:
        assert name in fixture_names, (
            f"fixture '{name}' declared in conftest but missing from "
            f"query fixtures output -- graph failed to register the fixture"
        )

    test_names = {t["name"].rsplit("::", 1)[-1] for t in tests}
    expected_tests = {"test_needs_db", "test_needs_cache", "test_needs_both"}
    for name in expected_tests:
        assert name in test_names, (
            f"test '{name}' defined in test file but missing from "
            f"query tests output -- collection failed to find the test"
        )

    # Both directions of the edge exist: tests are collected AND the fixtures
    # they depend on are registered. If either side were missing, the graph
    # would have dangling edges and test execution would fail.
    out, _err, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=3)


# ── test_fixture_scope_reflected_in_graph ─────────────────────────────────────


def test_fixture_scope_reflected_in_graph(tmp: TempDir) -> None:
    """A ``shared=True`` fixture must report scope ``"shared"`` in query output.

    Ensures the Python fixture registry correctly propagates ``shared=True``
    through to the Rust query layer so that downstream consumers (inspect TUI,
    query DSL predicates) can filter by scope.
    """
    # ── Arrange ──────────────────────────────────────────────────────────
    integ.write_project(
        tmp,
        tests={
            "test_scope.py": """\
                from oxitest import Fixture

                def test_uses_shared(conn: Fixture[str]):
                    assert conn == "shared-conn"
            """,
        },
        declarations="""\
            from oxitest import fixture

            @fixture(lifetime="package")
            def conn() -> str:
                return "shared-conn"
        """,
    )

    # ── Act ──────────────────────────────────────────────────────────────
    fixtures = _fixture_entries(tmp)

    # ── Assert ───────────────────────────────────────────────────────────
    conn_fixtures = [f for f in fixtures if f["name"] == "conn"]
    assert len(conn_fixtures) == 1, (
        f"expected exactly one 'conn' fixture in query output, got "
        f"{len(conn_fixtures)} -- fixture deduplication or naming is broken"
    )
    conn = conn_fixtures[0]
    assert conn["scope"] == "package", (
        f"fixture 'conn' declared lifetime='package' but query reports "
        f"scope={conn['scope']!r} -- scope propagation from Python to Rust is broken"
    )
    # The companion assertion on the `shared` flag went with the tier it read.
    # `shared=True` was the legacy wide tier and #1720 retires it; `package` is
    # its replacement for a fixture that must be built once for a subtree, and
    # it carries the co-location guarantee explicitly rather than by inference.


# ── test_cross_conftest_dependency_edges ──────────────────────────────────────
