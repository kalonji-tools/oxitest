"""Integration test for ``oxitest --version``."""

from __future__ import annotations

import subprocess
import sys

import oxitest
from oxitest import TempDir


def _run_version_flag(*args: str) -> tuple[str, str, int]:
    """Run oxitest with the given args and return (stdout, stderr, rc)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.stdout, result.stderr, result.returncode


@oxitest.arrange(TempDir)
def test_version_flag_prints_version_and_exits_zero() -> None:
    """``oxitest --version`` prints the version string and exits 0."""
    out, _stderr, rc = _run_version_flag("--version")
    assert rc == 0, f"expected exit 0, got {rc}\nstdout:\n{out}"
    assert "oxitest" in out, f"expected 'oxitest' in output:\n{out}"
    # Version string should contain at least one dot (e.g. "0.12.0")
    version_line = out.strip().split("\n")[0]
    parts = version_line.split()
    assert any("." in p for p in parts), f"no version number found in: {version_line}"


@oxitest.arrange(TempDir)
def test_short_version_flag() -> None:
    """``oxitest -V`` is equivalent to ``--version``."""
    out_long, _, rc_long = _run_version_flag("--version")
    out_short, _, rc_short = _run_version_flag("-V")
    assert rc_long == rc_short == 0, (
        f"expected both exit 0, got long={rc_long} short={rc_short}"
    )
    assert out_long == out_short, (
        f"--version and -V output differ:\n{out_long!r}\n{out_short!r}"
    )
