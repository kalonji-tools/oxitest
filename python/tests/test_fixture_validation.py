"""Tests for collection-time fixture name validation and unused fixture detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge._helper_namespace import HelperNamespace

    helpers: HelperNamespace

from conftest import helpers  # type: ignore[assignment]

# ── validate_fixture_names ───────────────────────────────────────────────────


def test_valid_names_return_empty():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names(
        [
            {
                "node_id": "test.py::test_a",
                "fixture_names": ["store"],
                "fixref_names": [],
            },
        ]
    )

    assert errors == [], f"expected no errors, got {errors}"


def test_invalid_name_returns_error():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names(
        [
            {
                "node_id": "test.py::test_a",
                "fixture_names": ["no_such_fx"],
                "fixref_names": [],
            },
        ]
    )

    assert errors == [("test.py::test_a", "no_such_fx")], f"unexpected errors: {errors}"


def test_fixref_names_excluded():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names(
        [
            {
                "node_id": "test.py::test_a",
                "fixture_names": ["backend", "store"],
                "fixref_names": ["backend"],
            },
        ]
    )

    assert errors == [], f"fixref names should be excluded, got {errors}"


def test_mixed_valid_and_invalid():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names(
        [
            {
                "node_id": "test.py::test_a",
                "fixture_names": ["store"],
                "fixref_names": [],
            },
            {
                "node_id": "test.py::test_b",
                "fixture_names": ["no_such_fx"],
                "fixref_names": [],
            },
            {
                "node_id": "test.py::test_c",
                "fixture_names": ["missing"],
                "fixref_names": [],
            },
        ]
    )

    expected = [("test.py::test_b", "no_such_fx"), ("test.py::test_c", "missing")]
    assert errors == expected, f"expected {expected}, got {errors}"


def test_empty_items_return_empty():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names([])

    assert errors == [], f"expected no errors for empty items, got {errors}"


def test_no_fixture_names_return_empty():
    session = helpers.common.make_session_with("store", lambda: 42)

    errors = session.validate_fixture_names(
        [
            {"node_id": "test.py::test_a", "fixture_names": [], "fixref_names": []},
        ]
    )

    assert errors == [], f"expected no errors for empty fixture_names, got {errors}"


# ── find_unused_fixtures ─────────────────────────────────────────────────────


def _factory_no_dep() -> None:
    """A fixture factory with no dependencies."""


def test_unused_fixture_detected() -> None:
    """A fixture that no test references should appear as unused."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "unused_db", conftest_path="/project/conftest.py", factory=_factory_no_dep
        ),
    )
    items: list[dict] = [{"fixture_names": []}]

    result = session.find_unused_fixtures(items)

    assert len(result) == 1, f"expected 1 unused fixture, got {len(result)}"
    assert result[0] == (
        "/project/conftest.py",
        "unused_db",
    ), f"unexpected result: {result[0]}"


def test_autouse_excluded() -> None:
    """Autouse fixtures should never be reported as unused."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "auto_setup",
            autouse=True,
            conftest_path="/project/conftest.py",
            factory=_factory_no_dep,
        ),
    )
    items: list[dict] = [{"fixture_names": []}]

    result = session.find_unused_fixtures(items)

    assert result == [], f"autouse fixture should not appear as unused: {result}"


def test_transitive_deps_excluded() -> None:
    """A fixture used only as a dependency of a used fixture is not unused."""

    def _parent(dep: object = None) -> None:
        """Parent fixture depending on 'dep'."""

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "dep", conftest_path="/project/conftest.py", factory=_factory_no_dep
        ),
        helpers.common.make_fixture_def(
            "parent",
            conftest_path="/project/conftest.py",
            factory=_parent,
            depends_on=(("dep", object),),
        ),
    )
    items: list[dict] = [{"fixture_names": ["parent"]}]

    result = session.find_unused_fixtures(items)

    assert result == [], f"transitive dep should not appear as unused: {result}"


def test_all_fixtures_used_empty_result() -> None:
    """When every fixture is referenced by at least one test, result is empty."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", conftest_path="/project/conftest.py", factory=_factory_no_dep
        ),
        helpers.common.make_fixture_def(
            "cache", conftest_path="/project/conftest.py", factory=_factory_no_dep
        ),
    )
    items: list[dict] = [{"fixture_names": ["db", "cache"]}]

    result = session.find_unused_fixtures(items)

    assert result == [], f"all fixtures used, expected empty result: {result}"
