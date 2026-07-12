"""Snapshot tests for getting-started.md output blocks."""

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).parent))
from _helpers import *
from oxitest import TempDir

PASSING_TESTS = """\
def test_addition():
    assert 1 + 1 == 2

def test_multiplication():
    assert 3 * 4 == 12

def test_string_repeat():
    assert "ha" * 3 == "hahaha"
"""

FAILING_TESTS = """\
def test_addition():
    assert 1 + 1 == 2

def test_multiplication():
    assert 3 * 4 == 13   # wrong expected value

def test_string_repeat():
    assert "ha" * 3 == "hahaha"
"""


def test_passing_run_snapshot(tmp: TempDir) -> None:
    write_project(tmp, tests={"test_math.py": PASSING_TESTS})
    out, _err, _rc = run_oxitest(tmp, "--serial")
    assert_snapshot("getting_started_passing", out, tmp_path=tmp)


def test_failing_run_snapshot(tmp: TempDir) -> None:
    write_project(tmp, tests={"test_math.py": FAILING_TESTS})
    out, _err, _rc = run_oxitest(tmp, "--serial")
    assert_snapshot("getting_started_failing", out, tmp_path=tmp)


def test_single_test_snapshot(tmp: TempDir) -> None:
    write_project(tmp, tests={"test_math.py": PASSING_TESTS})
    out, _err, _rc = run_oxitest(
        None, "test_math.py::test_addition", "--serial", cwd=str(tmp.path)
    )
    assert_snapshot("getting_started_single", out, tmp_path=tmp)
