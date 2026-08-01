"""Integration tests for ``oxitest query tests --count``."""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_list_count_basic(tmp: TempDir) -> None:
    """'query tests --count' should report the correct number of discovered tests."""
    integ.write_project(
        tmp,
        tests={
            "test_math.py": """\
def test_add():
    assert 1 + 1 == 2

def test_sub():
    assert 2 - 1 == 1
""",
        },
    )
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "2 tests")


def test_list_count_multiple_files(tmp: TempDir) -> None:
    """'query tests --count' should aggregate tests across multiple test files."""
    integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\ndef test_three(): pass\n",
        },
    )
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "3 tests")


def test_list_count_with_class(tmp: TempDir) -> None:
    """'query tests --count' should count methods in class-based test groups."""
    integ.write_project(
        tmp,
        tests={
            "test_cls.py": """\
class TestGroup:
    def test_a(self): pass
    def test_b(self): pass
""",
        },
    )
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "2 tests")


def test_list_count_no_tests(tmp: TempDir) -> None:
    """'query tests --count' reports 0 when no test functions are present."""
    integ.write_project(
        tmp,
        tests={
            "test_empty.py": "def helper(): pass\n",
        },
    )
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "0 tests")


def test_list_count_singular(tmp: TempDir) -> None:
    """'query tests --count' output should include '1 tests' for a single test."""
    integ.write_project(
        tmp,
        tests={
            "test_one.py": "def test_solo(): pass\n",
        },
    )
    out, _err, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "1 tests")
