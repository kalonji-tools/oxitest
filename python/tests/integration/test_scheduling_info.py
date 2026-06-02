"""Integration tests for scheduling diagnostics with -v."""

from __future__ import annotations

import subprocess
import sys

from conftest import helpers
from oxitest import TempDir


def _run_verbose(tmp: TempDir, *extra_args: str) -> tuple[str, str, int]:
    """Run oxitest with -v and return (stdout, stderr, rc)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "-v",
            "--color",
            "never",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


def _run_normal(tmp: TempDir) -> tuple[str, str, int]:
    """Run oxitest without -v."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


def test_verbose_shows_scheduling_decision(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = _run_verbose(tmp)
    assert rc == 0, f"rc={rc}, stderr={err}"
    assert "scheduling:" in err, f"expected scheduling info in stderr: {err!r}"


def test_verbose_shows_serial_reason(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = _run_verbose(tmp, "--serial")
    assert rc == 0, f"rc={rc}, stderr={err}"
    assert "serial" in err, f"expected 'serial' in stderr: {err!r}"


def test_verbose_shows_strategy(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = _run_verbose(tmp)
    assert rc == 0, f"rc={rc}, stderr={err}"
    assert "strategy" in err, f"expected 'strategy' in stderr: {err!r}"


def test_no_scheduling_info_without_verbose(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = _run_normal(tmp)
    assert rc == 0, f"rc={rc}, stderr={err}"
    assert "scheduling:" not in err, (
        f"scheduling info should not appear without -v: {err!r}"
    )
