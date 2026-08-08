"""Integration tests for ``oxitest inspect``."""

from __future__ import annotations

import subprocess
import sys

from oxitest import TempDir


def _run_inspect(*args: str, cwd: str | None = None) -> tuple[str, str, int]:
    """Run oxitest inspect and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", "inspect", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=cwd,
        check=False,
    )
    return result.stdout, result.stderr, result.returncode


def test_inspect_fails_gracefully_without_tty(tmp: TempDir) -> None:
    """``oxitest inspect`` exits non-zero when stdout is not a TTY.

    ``enable_raw_mode()`` fails in a piped subprocess, so reaching that
    failure proves CLI parsing, file collection, and graph construction all
    succeeded without crashing.
    """
    _out, err, rc = _run_inspect(cwd=str(tmp))
    assert rc != 0, (
        "inspect must exit non-zero when stdout is not a TTY — "
        "a zero exit here would mean the TUI ran successfully in a pipe, "
        "which crossterm does not support"
    )
    # The exit code alone cannot tell the explicit guard from the old
    # behaviour: before #1989 this also exited non-zero, because
    # enable_raw_mode() happened to fail on POSIX. It did not on Windows, where
    # the TUI blocked on input instead. Asserting the message is what makes the
    # guard verifiable on the platform CI runs most.
    assert "interactive terminal" in err, (
        "the refusal must say why and what to do instead; without this the "
        "test passes on the raw errno it used to emit ('No such device or "
        f"address'), which told the user nothing. got stderr:\n{err!r}"
    )


def test_inspect_expression_filter_accepted(tmp: TempDir) -> None:
    """``oxitest inspect -E 'name(hello)'`` accepts a valid DSL expression.

    A syntactically valid filter expression must not produce an 'invalid'
    error message — it should be accepted and reach terminal setup, where
    it then fails due to the non-TTY environment (as expected).
    """
    (tmp / "test_hello.py").write_text(
        "def test_hello():\n    assert 1 + 1 == 2, 'basic arithmetic must hold'\n",
        encoding="utf-8",
    )
    _out, err, _rc = _run_inspect("-E", "name(hello)", cwd=str(tmp))
    assert "invalid" not in err.lower(), (
        "a syntactically valid DSL expression must not produce an 'invalid' "
        f"error message; got stderr:\n{err!r}"
    )


def test_inspect_invalid_expression_reports_error(tmp: TempDir) -> None:
    """``oxitest inspect -E 'name('`` rejects a broken DSL expression.

    A truncated expression is a parse error that must be caught before
    terminal setup, producing a non-zero exit so the caller can diagnose
    the typo rather than silently launching a TUI with no filters.
    """
    _out, _err, rc = _run_inspect("-E", "name(", cwd=str(tmp))
    assert rc != 0, (
        "inspect must exit non-zero when given a syntactically invalid "
        "DSL expression — a zero exit would mean the broken filter was silently ignored"
    )


def test_inspect_name_argument_accepted(tmp: TempDir) -> None:
    """``oxitest inspect hello`` accepts a positional name argument.

    The name argument is an optional jump target; passing one must not
    produce an 'unrecognized' argument error from clap — it is a defined
    field on InspectArgs.
    """
    _out, err, _rc = _run_inspect("hello", cwd=str(tmp))
    assert "unrecognized" not in err.lower(), (
        "the positional name argument must be recognised by the CLI — "
        "an 'unrecognized' error means the InspectArgs definition is broken; "
        f"got stderr:\n{err!r}"
    )
