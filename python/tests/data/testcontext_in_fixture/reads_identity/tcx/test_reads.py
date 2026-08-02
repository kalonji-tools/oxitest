"""A test whose fixture reads ``ctx.name``. Expected to error at setup."""

from __future__ import annotations

from oxitest import Fixture


def test_schema_is_per_test(db_schema: Fixture[str]) -> None:
    # Assert — unreachable; the fixture must have raised during setup
    assert db_schema != "", (
        "reaching this line means the fixture derived a name from a "
        "TestContext that describes no test, which is the silent collision "
        "#1874 reports"
    )
