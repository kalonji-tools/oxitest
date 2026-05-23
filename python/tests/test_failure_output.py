# python/tests/test_failure_output.py
"""CLI integration tests for failure output improvements.

Strategy: invoke oxitest CLI as a subprocess against temp test files,
assert on output patterns. Tests color diffs, frame truncation, and
fix suggestions.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from oxitest import TempDir


def _run_oxitest(tmp: TempDir, *extra_args: str) -> tuple[str, int]:
    """Run oxitest CLI against tmp and return (stdout, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never", *extra_args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.returncode


# ── Color-coded diffs ────────────────────────────────────────────────


def test_diff_shows_left_and_right(tmp: TempDir) -> None:
    """Assertion failures with left/right values show diff labels."""
    test_file = tmp / "test_diff.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_values():
            assert 3 == 4, ""
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "- left:" in out, f"missing left diff label:\n{out}"
    assert "+ right:" in out, f"missing right diff label:\n{out}"
    assert "3" in out, f"missing left value:\n{out}"
    assert "4" in out, f"missing right value:\n{out}"


def test_diff_shows_inline_for_string_values(tmp: TempDir) -> None:
    """String assertion failures show inline diff with left/right labels."""
    test_file = tmp / "test_string_diff.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_strings():
            left = "hello"
            right = "world"
            assert left == right, ""
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "- left:" in out, f"missing left diff label:\n{out}"
    assert "+ right:" in out, f"missing right diff label:\n{out}"
    assert "hello" in out, f"missing left value:\n{out}"
    assert "world" in out, f"missing right value:\n{out}"


def test_diff_not_shown_for_bool_assert(tmp: TempDir) -> None:
    """Bool assertions show value label, not left/right diff."""
    test_file = tmp / "test_bool.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_falsy():
            assert False, ""
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "- left:" not in out, f"left diff should not appear for bool:\n{out}"


# ── Frame truncation ────────────────────────────────────────────────


def test_short_tb_hides_internal_frames(tmp: TempDir) -> None:
    """Default --tb short hides oxitest internal frames."""
    test_file = tmp / "test_frames.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_error():
            raise ValueError("boom")
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "executor.py" not in out, f"internal frame should be hidden:\n{out}"
    assert "_middleware.py" not in out, f"internal frame should be hidden:\n{out}"


def test_long_tb_shows_test_file(tmp: TempDir) -> None:
    """--tb long shows test file in output."""
    test_file = tmp / "test_frames_long.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_error():
            raise ValueError("boom")
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial", "--tb", "long")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "test_frames_long.py" in out, f"test file should appear:\n{out}"


# ── Fix suggestions ─────────────────────────────────────────────────


def test_no_hint_for_plain_assertion(tmp: TempDir) -> None:
    """Plain assertion failures should not produce hint lines."""
    test_file = tmp / "test_no_hint.py"
    test_file.write_text(
        textwrap.dedent("""\
        def test_plain():
            assert 1 == 2, ""
    """)
    )
    out, rc = _run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    assert "hint:" not in out, f"no hint expected for plain assertion:\n{out}"
