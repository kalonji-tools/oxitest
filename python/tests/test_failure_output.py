# python/tests/test_failure_output.py
"""CLI integration tests for failure output improvements.

Strategy: invoke oxitest CLI as a subprocess against temp test files,
assert on output patterns. Tests color diffs, frame truncation, and
fix suggestions.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

import oxitest as oxi
from conftest import helpers
from oxitest import TempDir


@dataclass(frozen=True)
class OutputCase:
    """Parameters for a single failure-output integration test case."""

    test_code: str
    expected: tuple[str, ...] = field(default_factory=tuple)
    not_expected: tuple[str, ...] = field(default_factory=tuple)
    extra_args: tuple[str, ...] = ("--serial",)


# ── Color-coded diffs + frame truncation + fix suggestions ───────────


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
        extra_args=("--serial", "--tb", "long"),
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
    out, rc = helpers.common.run_oxitest(tmp, *extra_args)
    assert rc != 0, f"expected non-zero exit code:\n{out}"
    for s in expected:
        assert s in out, f"expected {s!r} in output:\n{out}"
    for s in not_expected:
        assert s not in out, f"did not expect {s!r} in output:\n{out}"
