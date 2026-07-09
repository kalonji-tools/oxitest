"""Integration tests: color output verification."""

import subprocess
import sys

from oxitest import TempDir


def test_color_always_emits_ansi_codes(tmp: TempDir) -> None:
    """--color=always produces ANSI escape sequences in stdout."""
    (tmp / "test_colors.py").write_text(
        "def test_pass():\n    assert 1 == 1\n\ndef test_fail():\n    assert 1 == 2\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--color",
            "always",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    out = result.stdout
    # ANSI escape sequences start with ESC[ (\x1b[)
    assert "\x1b[" in out, (
        f"--color=always should emit ANSI escape codes, got:\n{out!r}"
    )
    # Green for pass (SGR code 32)
    assert "\x1b[32m" in out or "\x1b[32" in out, (
        f"expected green (32m) for passed test in:\n{out!r}"
    )
    # Red for fail (SGR code 31)
    assert "\x1b[31m" in out or "\x1b[31" in out, (
        f"expected red (31m) for failed test in:\n{out!r}"
    )
