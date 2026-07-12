"""Snapshot tests for errors.md output blocks."""

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).parent))
from _helpers import *
from oxitest import TempDir


def test_bare_assert_strict_abort(tmp: TempDir) -> None:
    write_project(
        tmp,
        tests={
            "test_bare.py": """def test_example():
    result = True
    assert result
"""
        },
    )
    out, err, _rc = run_oxitest(tmp, "--serial", "--strict=abort")
    assert_snapshot("errors_bare_assert", out + err, tmp_path=tmp)


def test_missing_mark_reason_strict_abort(tmp: TempDir) -> None:
    write_project(
        tmp,
        tests={
            "test_marks.py": """import oxitest

@oxitest.mark.skip
def test_no_reason():
    pass
"""
        },
        pyproject="""[tool.oxitest]
markers = ["slow: slow tests"]
""",
    )
    out, err, _rc = run_oxitest(tmp, "--serial", "--strict=abort")
    assert_snapshot("errors_missing_mark_reason", out + err, tmp_path=tmp)


def test_fixture_not_found(tmp: TempDir) -> None:
    write_project(
        tmp,
        tests={
            "test_missing.py": """from oxitest import Fixture

def test_needs_fixture(db: Fixture[object]) -> None:
    pass
"""
        },
    )
    out, err, _rc = run_oxitest(tmp, "--serial")
    assert_snapshot("errors_fixture_not_found", out + err, tmp_path=tmp)


def test_syntax_error_in_test_file(tmp: TempDir) -> None:
    write_project(
        tmp,
        tests={
            "test_broken.py": """def test_ok():
    assert True

def test_bad(
"""
        },
    )
    out, err, _rc = run_oxitest(tmp, "--serial")
    assert_snapshot("errors_syntax_error", out + err, tmp_path=tmp)
