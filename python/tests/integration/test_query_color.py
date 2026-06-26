"""Integration tests for query color and syntax highlighting (#687)."""

from __future__ import annotations

from conftest import helpers
from oxitest import TempDir

ANSI_ESCAPE = "\x1b["


def _write_project(tmp: TempDir) -> None:
    """Write a minimal project for query testing."""
    (tmp / "pyproject.toml").write_text("[tool.oxitest]\ntestpaths = ['.']\n")
    (tmp / "test_example.py").write_text(
        "def test_add():\n    # basic test\n    assert 1 + 1 == 2\n"
    )


def test_inspect_color_always_has_ansi(tmp: TempDir):
    """--color always produces ANSI escape codes in inspect output."""
    _write_project(tmp)
    out, err, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "query",
        "tests",
        "--inspect",
        "test_add",
        "--color",
        "always",
        cwd=".",
    )
    assert rc == 0, f"stderr: {err}"
    assert ANSI_ESCAPE in out, f"expected ANSI escapes in output: {out!r}"


def test_inspect_color_never_no_ansi(tmp: TempDir):
    """--color never produces no ANSI escape codes."""
    _write_project(tmp)
    out, err, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "query",
        "tests",
        "--inspect",
        "test_add",
        "--color",
        "never",
        cwd=".",
    )
    assert rc == 0, f"stderr: {err}"
    assert ANSI_ESCAPE not in out, f"unexpected ANSI in output: {out!r}"


def test_inspect_shows_source_with_highlighting(tmp: TempDir):
    """Inspect card includes source code when --color always."""
    _write_project(tmp)
    out, err, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "query",
        "tests",
        "--inspect",
        "test_add",
        "--color",
        "always",
        cwd=".",
    )
    assert rc == 0, f"stderr: {err}"
    assert "test_add" in out, f"expected 'test_add' in output: {out!r}"
    assert "def" in out, f"expected 'def' keyword in output: {out!r}"
