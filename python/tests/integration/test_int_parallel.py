"""Integration tests: serial and parallel produce identical outcomes."""

import re
import subprocess
import sys

from oxitest import TempDir


def _run(tmp: TempDir, *extra: str) -> tuple[str, int]:
    """Run oxitest against a temp dir, return (stdout, exit_code)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never", *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.returncode


def _parse_counts(out: str) -> dict[str, int]:
    """Extract outcome counts from summary line."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"(\d+)\s+(passed|failed|skipped|error)", out):
        counts[m.group(2)] = int(m.group(1))
    return counts


def test_serial_and_default_same_counts(tmp: TempDir):
    """Serial and default (parallel) runs produce the same outcome counts."""
    (tmp / "test_a.py").write_text(
        "def test_one(): assert True\n"
        "def test_two(): assert True\n"
    )
    (tmp / "test_b.py").write_text(
        "def test_fail(): assert False\n"
        "def test_pass(): assert True\n"
    )
    serial_out, _ = _run(tmp, "--serial")
    default_out, _ = _run(tmp)
    serial_counts = _parse_counts(serial_out)
    default_counts = _parse_counts(default_out)
    assert serial_counts == default_counts, (
        f"serial counts {serial_counts} differ from default counts {default_counts}"
    )


def test_serial_and_default_both_pass(tmp: TempDir):
    """Serial and default runs both exit 0 when all tests pass."""
    (tmp / "test_all_pass.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n"
    )
    _, serial_rc = _run(tmp, "--serial")
    _, default_rc = _run(tmp)
    assert serial_rc == 0, f"serial run should exit 0, got {serial_rc}"
    assert default_rc == 0, f"default run should exit 0, got {default_rc}"
