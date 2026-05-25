# python/tests/test_progress_output.py
"""CLI integration tests for progress visualization.

Strategy: invoke oxitest CLI as a subprocess, assert on output
patterns for failures-only default and verbose modes.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_default_hides_passing_tests(tmp: TempDir) -> None:
    """Non-verbose mode hides passing tests, shows failures."""
    test_file = tmp / "test_mixed.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_pass_one():
            assert True, ""

        def test_pass_two():
            assert True, ""

        def test_fail():
            assert 1 == 2, ""
    """)
    )
    out, rc = run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code, got rc={rc}\n{out}"
    assert "test_pass_one" not in out, f"passing test should be hidden:\n{out}"
    assert "test_pass_two" not in out, f"passing test should be hidden:\n{out}"
    assert "test_fail" in out, f"failing test should appear:\n{out}"
    assert "passed" in out, f"summary should appear:\n{out}"


def test_verbose_failure_node_id_shown(tmp: TempDir) -> None:
    """Verbose mode shows failing test node_id in FAILURES section.

    Note: when oxitest runs as a subprocess with captured stdout it is not a
    TTY, so the CI reporter (dot-style) is used regardless of -v.  The
    FAILURES section still emits the full node_id for every failure.
    """
    test_file = tmp / "test_verbose.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_pass_alpha():
            assert True, ""

        def test_pass_beta():
            assert True, ""

        def test_fail_gamma():
            assert 1 == 2, ""
    """)
    )
    out, rc = run_oxitest(tmp, "--serial", "-v")
    assert rc != 0, f"expected non-zero exit code, got rc={rc}\n{out}"
    assert "test_fail_gamma" in out, (
        f"failing test node_id should appear in FAILURES:\n{out}"
    )
    assert "passed" in out, f"summary should appear:\n{out}"


def test_default_shows_summary_for_all_passing(tmp: TempDir) -> None:
    """When all tests pass, non-verbose still shows summary."""
    test_file = tmp / "test_all_pass.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_one():
            assert True, ""

        def test_two():
            assert True, ""
    """)
    )
    out, rc = run_oxitest(tmp, "--serial")
    assert rc == 0, f"expected zero exit code, got rc={rc}\n{out}"
    assert "passed" in out, f"summary should appear:\n{out}"
    assert "test_one" not in out, f"test name should be hidden:\n{out}"
    assert "test_two" not in out, f"test name should be hidden:\n{out}"


def test_failure_diagnostic_shown_in_default(tmp: TempDir) -> None:
    """Failure diagnostics appear even in non-verbose mode."""
    test_file = tmp / "test_diag.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_diag():
            x = 42
            assert x == 99, ""
    """)
    )
    out, rc = run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code, got rc={rc}\n{out}"
    assert "test_diag.py" in out, f"file location should appear:\n{out}"
    assert "assert x == 99" in out, f"source line should appear:\n{out}"
