"""Integration tests for ``oxitest run --collection-profile``."""

from __future__ import annotations

import subprocess
import sys

from oxitest import TempDir


def _run_with_profile(tmp: TempDir) -> tuple[str, str, int]:
    """Run oxitest with --collection-profile."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--collection-profile",
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


def _run_without_profile(tmp: TempDir) -> tuple[str, str, int]:
    """Run oxitest without --collection-profile."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


def _write_simple_project(tmp: TempDir) -> None:
    """Write a simple test project into tmp."""
    from conftest import helpers

    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )


def test_collection_profile_output(tmp: TempDir):
    from conftest import helpers

    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\n",
        },
    )
    _out, err, rc = _run_with_profile(tmp)
    assert rc == 0, f"expected exit 0, got {rc}; stderr: {err}"
    assert "Collection profile" in err, f"missing profile header in stderr: {err!r}"
    assert "prescan:" in err, f"missing prescan line in stderr: {err!r}"
    assert "collection:" in err, f"missing collection line in stderr: {err!r}"


def test_collection_profile_shows_slowest_files(tmp: TempDir):
    _write_simple_project(tmp)
    _out, err, rc = _run_with_profile(tmp)
    assert rc == 0, f"expected exit 0, got {rc}; stderr: {err}"
    assert "test_a.py" in err, f"slowest files should include test_a.py: {err!r}"


def test_collection_profile_not_shown_by_default(tmp: TempDir):
    _write_simple_project(tmp)
    _out, err, rc = _run_without_profile(tmp)
    assert rc == 0, f"expected exit 0, got {rc}; stderr: {err}"
    assert "Collection profile" not in err, (
        f"profile should not appear by default: {err!r}"
    )
