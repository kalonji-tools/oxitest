"""Subprocess helpers for output snippet validation tests."""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from oxitest import TempDir

_SNAP_DIR = Path(__file__).parent / "snapshots"


def run_oxitest(tmp_path, *extra_args, cwd=None):
    """Run oxitest as a subprocess and return (stdout, stderr, returncode)."""
    cmd = [sys.executable, "-m", "oxitest"]
    if tmp_path is not None:
        path = tmp_path.path if isinstance(tmp_path, TempDir) else tmp_path
        cmd.append(str(path))
    cmd.extend(["--color", "never", *extra_args])
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, cwd=cwd, check=False
    )
    return result.stdout, result.stderr, result.returncode


def write_project(tmp, *, tests=None, conftest=None, pyproject=None):
    """Scaffold a minimal oxitest project in a TempDir."""
    if pyproject:
        (tmp.path / "pyproject.toml").write_text(textwrap.dedent(pyproject))
    if conftest:
        (tmp.path / "conftest.py").write_text(textwrap.dedent(conftest))
    if tests:
        for name, code in tests.items():
            (tmp.path / name).write_text(textwrap.dedent(code))


def _normalize(text, tmp_path=None):
    """Normalize output for snapshot comparison.

    Replaces dynamic values (paths, timing, worker IDs) with stable placeholders.
    """
    if tmp_path:
        path_str = str(tmp_path.path if isinstance(tmp_path, TempDir) else tmp_path)
        text = text.replace(path_str, "<TMPDIR>")
    # Strip timing values like "0.42ms", "1.23s"
    text = re.sub(r"\d+\.\d+\s*ms", "<TIMING>", text)
    text = re.sub(r"\d+\.\d+\s*s\b", "<TIMING>", text)
    # Strip worker IDs
    text = re.sub(r"worker #\d+", "worker #N", text)
    # Strip shared fixture cache stats
    text = re.sub(
        r"shared fixture cache: \d+/\d+ hits \(\d+%\)",
        "shared fixture cache: N/N hits (N%)",
        text,
    )
    # Normalize tip line counts (vary based on assertion messages)
    text = re.sub(r"  tip   \d+ assertion", "  tip   N assertion", text)
    # Strip KEPT lines (--keep-tmp output)
    text = re.sub(r"KEPT .*\n", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def assert_snapshot(name, actual, *, tmp_path=None):
    """Compare normalized output against a stored snapshot.

    If the snapshot doesn't exist, create it (first run).
    Set OXITEST_UPDATE_SNAPSHOTS=1 to regenerate.
    """
    _SNAP_DIR.mkdir(exist_ok=True)
    snap_file = _SNAP_DIR / f"{name}.txt"
    normalized = _normalize(actual, tmp_path=tmp_path)

    if not snap_file.exists() or os.environ.get("OXITEST_UPDATE_SNAPSHOTS"):
        snap_file.write_text(normalized)
        return  # first run or update — snapshot written

    expected = snap_file.read_text()
    assert normalized == expected, (
        f"output does not match snapshot {snap_file.name}\n"
        f"--- expected ---\n{expected}\n"
        f"--- actual ---\n{normalized}\n"
        f"To update: OXITEST_UPDATE_SNAPSHOTS=1 just test-docs"
    )


# Legacy helpers for non-snapshot tests
def assert_passed(out, rc, *, count=None):
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    if count is not None:
        assert f"{count} passed" in out, f"expected '{count} passed' in:\n{out}"


def assert_failed(out, rc, *, count=None):
    assert rc != 0, f"expected non-zero exit, got {rc}\n{out}"
    if count is not None:
        assert f"{count} failed" in out, f"expected '{count} failed' in:\n{out}"


def assert_contains(out, *terms):
    for term in terms:
        assert term in out, f"expected {term!r} in:\n{out}"


def assert_collection_error(out, rc):
    assert rc == 3, f"expected exit 3, got {rc}\n{out}"
