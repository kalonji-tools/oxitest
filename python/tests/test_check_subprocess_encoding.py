"""Tests for the subprocess-encoding check in ``check_subprocess_encoding.py``.

A text-mode ``subprocess`` call with no ``encoding=`` decodes the child's bytes
with ``locale.getencoding()``. On Linux and macOS that is UTF-8 and nothing is
ever noticed; on Windows it is cp1252, which cannot decode ``0x90`` — the third
byte of ``═`` (U+2550) in oxitest's own separator rule. Measured on
``windows-latest``: 292 of 1774 tests failed that way with **zero**
``AssertionError``s, because the harness died before it could assert
(#1951, #1986).

The predicate is the delicate part and is what these tests pin. Text mode is
``text=True`` or ``universal_newlines=True`` — **not** ``capture_output``, which
only sets ``stdout=PIPE`` and yields ``bytes``. #1986's first sweep conflated
them and reported 28 violations where there are 18; a gate that fires on the 10
correct calls gets suppressed, and then it is not a gate. So the
``capture_output``-only case has a test of its own below.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import TempDir


@dataclass(frozen=True)
class EncodingCase:
    """One keyword that takes a text-mode call off the locale default."""

    kwarg: str


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_subprocess_encoding.py"


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the checker against ``root`` and return the completed process.

    ``PYTHONIOENCODING`` makes both halves agree. The parent decodes UTF-8, but
    a piped ``sys.stdout`` in the child defaults to ``locale.getencoding()`` —
    cp1252 on Windows — and the checker's own failure message contains an
    em-dash, which cp1252 writes as the single byte ``0x97``. That is not a
    valid UTF-8 start byte, so the parent's reader thread dies mid-read,
    ``result.stdout`` becomes ``None``, and the assertion below fails as
    ``TypeError: argument of type 'NoneType' is not iterable`` — nowhere near
    the cause. Declaring the child's encoding is the fix; ``errors="replace"``
    would corrupt the text rather than transport it.
    """
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def _write_python(root: Path, body: str) -> None:
    """Write ``body`` to ``<root>/python/sample.py``, creating the tree."""
    package = root / "python"
    package.mkdir(parents=True, exist_ok=True)
    (package / "sample.py").write_text(textwrap.dedent(body), encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)


def test_text_mode_without_encoding_is_a_violation(tmp: TempDir) -> None:
    """The defect the gate exists for must fail it."""
    # Arrange
    root = Path(tmp)
    _write_python(
        root,
        """
        import subprocess

        subprocess.run(["echo"], capture_output=True, text=True)
        """,
    )

    # Act
    result = _run_checker(root)

    # Assert
    assert result.returncode == 1, (
        f"a text-mode call with no encoding= must fail the hook, or the gate "
        f"passes on the exact defect that produced 292 red tests on Windows; "
        f"got {result.returncode} with stdout={result.stdout!r}"
    )
    assert "sample.py:4" in result.stdout, (
        f"the failure must name the file and line so the developer can act "
        f"without re-deriving the rule; got stdout={result.stdout!r}"
    )


def test_capture_output_alone_is_not_a_violation(tmp: TempDir) -> None:
    """``capture_output`` without ``text`` yields bytes and never decodes.

    This is the assertion that stops the gate from firing on correct code. Ten
    calls in this repo are git plumbing that discards its output; #1986's first
    sweep counted them as violations, and a gate that fires on correct code
    gets suppressed rather than obeyed.
    """
    # Arrange
    root = Path(tmp)
    _write_python(
        root,
        """
        import subprocess

        subprocess.run(["git", "init"], check=True, capture_output=True)
        """,
    )

    # Act
    result = _run_checker(root)

    # Assert
    assert result.returncode == 0, (
        f"a bytes-mode call cannot raise UnicodeDecodeError on any platform, "
        f"so flagging it would make the gate fire on correct code; got "
        f"{result.returncode} with stdout={result.stdout!r}"
    )


@oxi.parametrize(
    encoding_kwarg=EncodingCase('encoding="utf-8"'),
    errors_kwarg=EncodingCase('errors="replace"'),
)
def test_naming_an_encoding_satisfies_the_gate(tmp: TempDir, kwarg: str) -> None:
    """Either ``encoding=`` or ``errors=`` takes the call off the locale default."""
    # Arrange
    root = Path(tmp)
    _write_python(
        root,
        f"""
        import subprocess

        subprocess.run(["echo"], text=True, {kwarg})
        """,
    )

    # Act
    result = _run_checker(root)

    # Assert
    assert result.returncode == 0, (
        f"passing {kwarg} is enough to leave locale.getencoding() behind, so "
        f"the gate must accept it; got {result.returncode} with "
        f"stdout={result.stdout!r}"
    )


def test_text_false_is_an_explicit_opt_out(tmp: TempDir) -> None:
    """``text=False`` is bytes mode stated out loud, not a missing encoding."""
    # Arrange
    root = Path(tmp)
    _write_python(
        root,
        """
        import subprocess

        subprocess.run(["echo"], capture_output=True, text=False)
        """,
    )

    # Act
    result = _run_checker(root)

    # Assert
    assert result.returncode == 0, (
        f"text=False decodes nothing, so demanding an encoding there would be "
        f"noise; got {result.returncode} with stdout={result.stdout!r}"
    )


def test_script_exits_0_on_this_repo() -> None:
    """The gate must be green on this repo as it stands.

    #1986 fixed all 18 sites. If this fails, either a new text-mode call landed
    without an encoding, or the checker's predicate drifted — both are worth
    failing the suite for.
    """
    # Act
    result = _run_checker(_REPO_ROOT)

    # Assert
    assert result.returncode == 0, (
        f"the checker must pass on this repo's own sources or it cannot be "
        f"wired into prek and just check; got {result.returncode} with "
        f"stdout={result.stdout!r}"
    )
