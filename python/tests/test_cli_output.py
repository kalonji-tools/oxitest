"""CLI integration tests for output formatting.

Covers failure diagnostics, progress display, and serial/parallel parity.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

import oxitest as oxi
from oxitest import TempDir, helpers

# ── Failure diagnostics ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutputCase:
    """Parameters for a single failure-output integration test case."""

    test_code: str
    expected: tuple[str, ...] = field(default_factory=tuple)
    not_expected: tuple[str, ...] = field(default_factory=tuple)
    extra_args: tuple[str, ...] = ("--serial",)


@oxi.parametrize(
    diff_left_right=OutputCase(
        test_code=textwrap.dedent("""\
            def test_values():
                assert 3 == 4, ""
        """),
        expected=("- left:", "+ right:", "3", "4"),
    ),
    diff_string_values=OutputCase(
        test_code=textwrap.dedent("""\
            def test_strings():
                left = "hello"
                right = "world"
                assert left == right, ""
        """),
        expected=("- left:", "+ right:", "hello", "world"),
    ),
    diff_not_shown_for_bool=OutputCase(
        test_code=textwrap.dedent("""\
            def test_falsy():
                assert False, ""
        """),
        not_expected=("- left:",),
    ),
    short_tb_hides_internal_frames=OutputCase(
        test_code=textwrap.dedent("""\
            def test_error():
                raise ValueError("boom")
        """),
        not_expected=("executor.py", "_middleware.py"),
    ),
    long_tb_shows_test_file=OutputCase(
        test_code=textwrap.dedent("""\
            def test_error():
                raise ValueError("boom")
        """),
        expected=("test_check.py",),
        extra_args=("--serial", "--show-internals"),
    ),
    no_hint_for_plain_assertion=OutputCase(
        test_code=textwrap.dedent("""\
            def test_plain():
                assert 1 == 2, ""
        """),
        not_expected=("hint:",),
    ),
)
def test_failure_output(
    tmp: TempDir,
    test_code: str,
    expected: tuple[str, ...],
    not_expected: tuple[str, ...],
    extra_args: tuple[str, ...],
) -> None:
    """Failure output matches expected patterns for each scenario."""
    (tmp / "test_check.py").write_text(test_code)
    out, _, rc = helpers.common.run_oxitest(tmp, *extra_args)
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    for s in expected:
        assert s in out, f"expected {s!r} in output:\n{out}"
    for s in not_expected:
        assert s not in out, f"did not expect {s!r} in output:\n{out}"


# ── Progress display ─────────────────────────────────────────────────────────


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
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial", "-v")
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
    assert rc != 0, f"expected non-zero exit code, got rc={rc}\n{out}"
    assert "test_diag.py" in out, f"file location should appear:\n{out}"
    assert "assert x == 99" in out, f"source line should appear:\n{out}"


# ── Serial / parallel parity ─────────────────────────────────────────────────


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
    serial_out, _, serial_rc = helpers.common.run_oxitest(tmp, "--serial")

    # Parallel: forces the subprocess-worker path (run_phase_parallel) even for 1 test
    parallel_out, _, parallel_rc = helpers.common.run_oxitest(tmp, "--workers", "2")

    assert serial_rc != 0, f"Expected serial run to fail, got rc={serial_rc}"
    assert parallel_rc != 0, f"Expected parallel run to fail, got rc={parallel_rc}"

    serial_diag = _extract_failures_section(serial_out)
    parallel_diag = _extract_failures_section(parallel_out)

    assert serial_diag, f"No FAILURES section found in serial output:\n{serial_out}"
    assert parallel_diag, (
        f"No FAILURES section found in parallel output:\n{parallel_out}"
    )

    for fragment, label in [
        ("test_parity.py:4", "file:lineno"),
        ("3,", "left element (collection diff)"),
        ("4,", "right element (collection diff)"),
        ("assert left == right", "source line"),
    ]:
        assert fragment in serial_diag, (
            f"{label!r} ({fragment!r}) missing from serial diagnostics:\n{serial_diag}"
        )
        assert fragment in parallel_diag, (
            f"{label!r} ({fragment!r}) missing from parallel diagnostics:"
            f"\n{parallel_diag}"
        )

    # Strip the parallel-only context line (worker #N | concurrent: ...)
    # before comparing diagnostic blocks.
    parallel_diag_stripped = "\n".join(
        line
        for line in parallel_diag.splitlines()
        if not line.strip().startswith("worker #")
    ).strip()

    assert serial_diag == parallel_diag_stripped, (
        f"Diagnostic output differs between serial and parallel modes.\n"
        f"--- serial ---\n{serial_diag}\n"
        f"--- parallel ---\n{parallel_diag_stripped}"
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
