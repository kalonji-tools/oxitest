"""Integration tests for shell completion generation."""

from __future__ import annotations

import subprocess
import sys

from oxitest import TempDir


def _run_completions(shell: str) -> tuple[str, str, int]:
    """Run ``oxitest completions <shell>`` and return (stdout, stderr, rc)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", "completions", shell],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.stderr, result.returncode


def test_completions_bash_outputs_script(_tmp: TempDir) -> None:
    """'completions bash' should output a valid bash completion script and exit 0."""
    out, _err, rc = _run_completions("bash")
    assert rc == 0, f"expected exit 0, got {rc}\nstdout:\n{out}"
    assert "complete" in out.lower() or "_oxitest" in out, (
        f"expected bash completion script in output:\n{out}"
    )


def test_completions_zsh_outputs_script(_tmp: TempDir) -> None:
    """'completions zsh' should output a valid zsh completion script and exit 0."""
    out, _err, rc = _run_completions("zsh")
    assert rc == 0, f"expected exit 0, got {rc}\nstdout:\n{out}"
    assert "#compdef" in out or "_oxitest" in out, (
        f"expected zsh completion script in output:\n{out}"
    )


def test_completions_fish_outputs_script(_tmp: TempDir) -> None:
    """'completions fish' should output a valid fish completion script and exit 0."""
    out, _err, rc = _run_completions("fish")
    assert rc == 0, f"expected exit 0, got {rc}\nstdout:\n{out}"
    assert "complete" in out.lower(), (
        f"expected fish completion script in output:\n{out}"
    )
