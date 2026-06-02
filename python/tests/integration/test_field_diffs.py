"""Integration tests for dataclass field diffs in assertion output."""

from __future__ import annotations

from conftest import helpers
from oxitest import TempDir


def test_dataclass_field_diffs_shown(tmp: TempDir):
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, "test should fail"
    assert "field diffs" in out, f"expected field diffs in output: {out!r}"
    assert "email" in out, f"expected 'email' field: {out!r}"
    assert "age" in out, f"expected 'age' field: {out!r}"


def test_non_dataclass_no_field_diffs(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_plain.py": """
                def test_plain_mismatch():
                    assert [1, 2, 3] == [1, 2, 4]
            """,
        },
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, f"test should fail, got exit {rc}\n{out}"
    assert "field diffs" not in out, (
        f"field diffs should not appear for non-dataclass: {out!r}"
    )
