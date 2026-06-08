"""Integration tests: --doctest-modules flag."""

import subprocess
import sys
from pathlib import Path

import oxitest
from oxitest import Fixture, TempDir, Yields

fx = oxitest.Fixtures()


@fx.fixture
def doctest_project(tmp: TempDir) -> Yields[Path]:
    """Tmp dir with a Python source file containing doctests."""
    (tmp / "mylib.py").write_text(
        "def add(a, b):\n"
        '    """Add two numbers.\n'
        "\n"
        "    >>> add(1, 2)\n"
        "    3\n"
        '    """\n'
        "    return a + b\n"
        "\n"
        "\n"
        "def broken():\n"
        '    """A broken doctest.\n'
        "\n"
        "    >>> 1 + 1\n"
        "    3\n"
        '    """\n'
        "    pass\n"
    )
    yield tmp


def test_doctest_modules_collects_and_runs(doctest_project: Fixture[Path]):
    """--doctest-modules collects doctests from source files."""
    tmp = doctest_project
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--doctest-modules",
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    out = result.stdout

    # Should have 1 passed (add) and 1 failed (broken)
    assert "1 passed" in out, f"expected 1 passed in:\n{out}"
    assert "1 failed" in out, f"expected 1 failed in:\n{out}"


def test_doctest_modules_off_by_default(doctest_project: Fixture[Path]):
    """Without --doctest-modules, doctests are not collected."""
    tmp = doctest_project
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    out = result.stdout
    # No test files match test_*.py, so 0 items collected
    assert "collected 0 items" in out, f"expected 0 items in:\n{out}"


def test_doctest_coexists_with_regular_tests(doctest_project: Fixture[Path]):
    """Doctests and regular tests coexist in the same run."""
    tmp = doctest_project
    (tmp / "test_math.py").write_text("def test_one(): assert 1 == 1\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--doctest-modules",
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    out = result.stdout
    # 1 regular test + 1 doctest pass (add), 1 doctest fail (broken)
    assert "2 passed" in out, f"expected 2 passed in:\n{out}"
    assert "1 failed" in out, f"expected 1 failed in:\n{out}"


def test_doctest_node_id_format(doctest_project: Fixture[Path]):
    """Doctest node IDs use the <doctest> prefix."""
    tmp = doctest_project
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--doctest-modules",
            "--color",
            "never",
            "-v",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    out = result.stdout
    assert "<doctest>" in out, f"expected <doctest> prefix in node IDs:\n{out}"
