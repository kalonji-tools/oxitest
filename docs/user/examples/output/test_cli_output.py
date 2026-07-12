"""Snapshot tests for cli.md and misc output blocks."""

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).parent))
from _helpers import *
from oxitest import TempDir

BASIC_TESTS = """def test_addition():
    assert 1 + 1 == 2

def test_multiplication():
    assert 3 * 4 == 12
"""


def test_exit_code_zero_on_pass(tmp: TempDir) -> None:
    write_project(tmp, tests={"test_math.py": BASIC_TESTS})
    out, _err, rc = run_oxitest(tmp, "--serial")
    assert rc == 0, f"passing suite should exit 0, got {rc}"
    assert_snapshot("cli_exit_zero", out, tmp_path=tmp)


def test_exit_code_one_on_failure(tmp: TempDir) -> None:
    write_project(
        tmp,
        tests={
            "test_math.py": """def test_pass():
    assert 1 + 1 == 2

def test_fail():
    assert 1 + 1 == 3
"""
        },
    )
    out, _err, rc = run_oxitest(tmp, "--serial")
    assert rc != 0, f"failing suite should exit non-zero, got {rc}"
    assert_snapshot("cli_exit_one", out, tmp_path=tmp)
