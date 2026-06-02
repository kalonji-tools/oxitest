"""Integration tests for ``oxitest list --count``."""

from __future__ import annotations

from conftest import helpers
from oxitest import TempDir


def test_list_count_basic(tmp: TempDir):
    helpers.integ.write_project(
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
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "2 tests in 1 file")


def test_list_count_multiple_files(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\ndef test_three(): pass\n",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "3 tests in 2 files")


def test_list_count_with_class(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_cls.py": """\
class TestGroup:
    def test_a(self): pass
    def test_b(self): pass
""",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "2 tests in 1 file")


def test_list_count_no_tests(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_empty.py": "def helper(): pass\n",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "0 tests in 0 files")


def test_list_count_singular(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_one.py": "def test_solo(): pass\n",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "1 test in 1 file")
