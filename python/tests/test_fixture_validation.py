"""Tests for collection-time fixture name validation."""

from conftest import helpers


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
