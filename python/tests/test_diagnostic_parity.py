# python/tests/test_diagnostic_parity.py
"""Integration test: parallel failures must have same diagnostic fields as
serial failures.

Strategy: invoke the oxitest CLI as a subprocess in both --serial and
--workers 2 (forced-parallel) modes, then compare the diagnostic block in the
text output. This exercises the full Rust pipeline end-to-end, not just the
worker subprocess.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_parallel_failure_diagnostics_match_serial(tmp: TempDir) -> None:
    """Parallel and serial modes must produce identical diagnostic blocks."""
    test_file = tmp / "test_parity.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_failing_assert():
            left = [1, 2, 3]
            right = [1, 2, 4]
            assert left == right
    """)
    )

    # Serial: runs tests in-process via the PyO3 bridge (run_phase)
    serial_out, serial_rc = run_oxitest(tmp, "--serial")

    # Parallel: forces the subprocess-worker path (run_phase_parallel) even for 1 test
    parallel_out, parallel_rc = run_oxitest(tmp, "--workers", "2")

    assert serial_rc != 0, f"Expected serial run to fail, got rc={serial_rc}"
    assert parallel_rc != 0, f"Expected parallel run to fail, got rc={parallel_rc}"

    serial_diag = _extract_failures_section(serial_out)
    parallel_diag = _extract_failures_section(parallel_out)

    assert serial_diag, f"No FAILURES section found in serial output:\n{serial_out}"
    assert parallel_diag, (
        f"No FAILURES section found in parallel output:\n{parallel_out}"
    )

    for field, label in [
        ("test_parity.py:4", "file:lineno"),
        ("left:", "left label"),
        ("right:", "right label"),
        ("[1, 2, 3]", "left value"),
        ("[1, 2, 4]", "right value"),
        ("assert left == right", "source line"),
    ]:
        assert field in serial_diag, (
            f"{label!r} ({field!r}) missing from serial diagnostics:\n{serial_diag}"
        )
        assert field in parallel_diag, (
            f"{label!r} ({field!r}) missing from parallel diagnostics:\n{parallel_diag}"
        )

    assert serial_diag == parallel_diag, (
        f"Diagnostic output differs between serial and parallel modes.\n"
        f"--- serial ---\n{serial_diag}\n"
        f"--- parallel ---\n{parallel_diag}"
    )


def _extract_failures_section(output: str) -> str:
    """Return the lines between FAILURES header and the final summary separator."""
    lines = output.splitlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        if start is None and "FAILURES" in line and line.startswith("FAILURES"):
            start = i + 1
        elif start is not None and line.startswith("═" * 5):
            end = i
            break
    if start is None:
        return ""
    if end is None:
        end = len(lines)
    return "\n".join(lines[start:end]).strip()
