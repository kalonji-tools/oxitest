"""Integration tests for ``oxitest list --count``."""

from __future__ import annotations

import subprocess
import sys

from conftest import helpers
from oxitest import TempDir


def _run_list_count(tmp: TempDir) -> tuple[str, str, int]:
    """Run ``oxitest list --count`` against *tmp* and return (stdout, stderr, rc)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            "list",
            str(tmp),
            "--count",
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


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
    out, _err, rc = _run_list_count(tmp)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "2 tests in 1 file" in out, f"unexpected output: {out!r}"


def test_list_count_multiple_files(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\ndef test_three(): pass\n",
        },
    )
    out, _err, rc = _run_list_count(tmp)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "3 tests in 2 files" in out, f"unexpected output: {out!r}"


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
    out, _err, rc = _run_list_count(tmp)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "2 tests in 1 file" in out, f"unexpected output: {out!r}"


def test_list_count_no_tests(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_empty.py": "def helper(): pass\n",
        },
    )
    out, _err, rc = _run_list_count(tmp)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "0 tests in 0 files" in out, f"unexpected output: {out!r}"


def test_list_count_singular(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_one.py": "def test_solo(): pass\n",
        },
    )
    out, _err, rc = _run_list_count(tmp)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "1 test in 1 file" in out, f"unexpected output: {out!r}"
