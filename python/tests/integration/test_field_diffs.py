"""Integration tests for dataclass field diffs in assertion output."""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_dataclass_field_diffs_shown(tmp: TempDir) -> None:
    """Assertion failure on dataclasses should display per-field diffs in output."""
    integ.write_project(
        tmp,
        tests={
            "test_dc.py": """
                from dataclasses import dataclass

                @dataclass
                class User:
                    name: str
                    email: str
                    age: int

                def test_user_mismatch():
                    a = User(name="alice", email="alice@example.com", age=30)
                    b = User(name="alice", email="alice@test.com", age=31)
                    assert a == b
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)
    integ.assert_contains(out, "field diffs", "email", "age")


def test_non_dataclass_no_field_diffs(tmp: TempDir) -> None:
    """Non-dataclass assertion failures should not include a 'field diffs' section."""
    integ.write_project(
        tmp,
        tests={
            "test_plain.py": """
                def test_plain_mismatch():
                    assert [1, 2, 3] == [1, 2, 4]
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)
    integ.assert_excludes(out, "field diffs")
