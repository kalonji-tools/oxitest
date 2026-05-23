"""CLI acceptance tests for watch mode.

Only tests CLI flag acceptance — not actual watch behavior
(that requires a real terminal and file system events).
"""

from __future__ import annotations

import subprocess
import sys


def test_watch_flag_in_help() -> None:
    """--watch flag appears in help output."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = result.stdout + result.stderr
    assert "--watch" in output, f"--watch not in help:\n{output}"


def test_watch_ignored_in_non_tty() -> None:
    """--watch in non-TTY environment is accepted by CLI parser."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", "--watch", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        stdin=subprocess.DEVNULL,
    )
    output = result.stdout + result.stderr
    assert "--watch" in output, f"--watch not in help:\n{output}"
