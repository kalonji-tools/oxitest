"""Tests for the justfile-quoting check in ``check_justfile_quoting.py``.

`just` substitutes ``{{ x }}`` textually before the shell parses the line, so
``'{{ x }}'`` and ``"{{ x }}"`` do not quote the value — they place the value
between two quote characters, and a quote of the same kind inside the value
closes the pair early.

Both halves of the resulting defect are covered here as input cases, because
both are caused by the same construct and the gate's job is to refuse it:

* the half that **fails loudly** — a value carrying a quote and shell syntax,
  which killed the recipe with ``syntax error near unexpected token `('``;
* the half that **fails silently** — a value carrying only a quote, which the
  shell re-joined so the recorded text was not the text the caller supplied.

The checker is tested rather than the recipe because CI never invokes ``just``:
the platform jobs run ``uv run python -m oxitest`` and the quality job runs
``prek``. A test that shelled out to ``just`` would be inert on every CI job,
which is the shape of an assertion that cannot fire (#2015).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import oxitest as oxi
from oxitest import TempDir

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_justfile_quoting.py"
_JUSTFILE = _REPO_ROOT / "justfile"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_justfile_quoting.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``
    rather than a normal import.

    The ``sys.modules`` registration is load-bearing: the script defines a
    ``@dataclass``, and ``dataclasses._process_class`` resolves the defining
    module through ``sys.modules.get(cls.__module__).__dict__``. Executing the
    module without registering it first makes that ``None`` and the decorator
    dies with ``AttributeError: 'NoneType' object has no attribute '__dict__'``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_justfile_quoting_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── The two halves of the defect, as rejected constructs ─────────────────────


@dataclass(frozen=True)
class RejectCase:
    """One justfile line the checker must refuse, and what it interpolates."""

    line: str
    name: str
    why: str


@oxi.parametrize(
    loud_half=RejectCase(
        line="    python scripts/dispose_finding.py '{{ reason }}'",
        name="reason",
        why=(
            "the filed defect — a reason carrying a quote and a parenthesis"
            " killed the recipe with a shell syntax error"
        ),
    ),
    assignment_rhs=RejectCase(
        line="    test_cmd='{{ test_cmd }}'",
        name="test_cmd",
        why=(
            "an assignment right-hand side is the same construct — a quote"
            " in the value closes the pair and strands a mutant"
        ),
    ),
    file_path=RejectCase(
        line="    python scripts/post_review_findings.py '{{ spec }}'",
        name="spec",
        why="a file path is exposed too, if the path carries a quote",
    ),
    no_inner_spaces=RejectCase(
        line="    @printf '%s\\n' '{{msg}}'",
        name="msg",
        why="no inner spaces — the interpolation is still whole-token",
    ),
    double_quoted=RejectCase(
        line='    python scripts/dispose_finding.py "{{ reason }}"',
        name="reason",
        why=(
            "a double-quoted interpolation corrupts a value the same way — "
            "`just dq 'he said \"hi\" and (x)'` recorded `he said hi and (x)`"
        ),
    ),
)
def test_a_quoted_interpolation_is_refused(case: RejectCase) -> None:
    """Every whole-token quoted interpolation is a violation, either quote character."""
    module = _load_script_module()

    violations = module.find_violations(case.line)

    assert len(violations) == 1, (
        f"the checker missed a quoted interpolation: {case.why}."
        " An unflagged site keeps corrupting its argument silently, which is"
        " the half of #2015 that nobody notices"
    )
    assert violations[0].name.strip() == case.name, (
        "the violation must name the interpolated variable so the report can"
        " suggest the fix; a violation naming the wrong variable sends the"
        " reader to rewrite something that is already correct"
    )


def test_the_report_suggests_the_quote_form() -> None:
    """The suggestion is the exact text that would have been correct."""
    module = _load_script_module()

    violations = module.find_violations("    cmd '{{ reason }}'")

    assert violations[0].suggestion == "{{ quote(reason) }}", (
        "the suggestion is the whole value of the report — a reader who has to"
        " work out the replacement form is as likely to reach for shell"
        " escaping, which is the defect this gate exists to stop"
    )


# ── Constructs the checker must accept ───────────────────────────────────────


@dataclass(frozen=True)
class AcceptCase:
    """One justfile line the checker must leave alone, and why."""

    line: str
    why: str


@oxi.parametrize(
    fixed_form=AcceptCase(
        line="    python scripts/dispose_finding.py {{ quote(reason) }}",
        why="the fixed form is what the gate is steering towards",
    ),
    variadic=AcceptCase(
        line="    python scripts/check.py {{ args }}",
        why=(
            "a variadic argument must word-split into separate arguments,"
            " which is exactly what quoting would prevent"
        ),
    ),
    embedded_in_literal=AcceptCase(
        line="    @printf '\\033[{{color}}m→ %s\\033[0m\\n' {{ quote(msg) }}",
        why=(
            "an interpolation inside a longer literal cannot take quote(),"
            " which escapes a whole value rather than a fragment"
        ),
    ),
    unquoted=AcceptCase(
        line='    just _log {{ _red }} "MUTANT NOT APPLIED"',
        why="an unquoted interpolation is not the construct",
    ),
    no_interpolation=AcceptCase(
        line="    trap 'git checkout -- \"$mutant_path\"' EXIT",
        why="a quoted string with no interpolation at all",
    ),
    mismatched_quotes=AcceptCase(
        line="    cmd '{{ a }}\"",
        why=(
            "the closing quote is a different character, so this is not a"
            " quoted token — the backreference is what tells them apart"
        ),
    ),
    conditional_with_braces=AcceptCase(
        line='    cmd \'{{ if x == "a" { "p" } else { "q" } }}\'',
        why=(
            "a known and documented limit: admitting braces would let one"
            " match run across two interpolations, which is the worse failure"
        ),
    ),
)
def test_an_accepted_construct_is_not_flagged(case: AcceptCase) -> None:
    """A gate that fires on correct code gets suppressed, and then it is not a gate."""
    module = _load_script_module()

    violations = module.find_violations(case.line)

    assert violations == [], (
        f"the checker flagged a correct construct: {case.why}."
        " A false positive here forces the author to either break a working"
        " recipe or silence the hook"
    )


def test_each_interpolation_on_a_line_is_reported_separately() -> None:
    """One line can carry several violations, and every one needs its own fix."""
    module = _load_script_module()

    violations = module.find_violations("    cmd '{{ slug }}' '{{ id }}' '{{ verb }}'")

    assert [violation.name.strip() for violation in violations] == [
        "slug",
        "id",
        "verb",
    ], (
        "reporting one violation per line would let a partly-fixed line pass:"
        " review-dispose carried four on a single line, and fixing only the"
        " first leaves the other three corrupting their arguments"
    )


# ── The gate against this repo ───────────────────────────────────────────────


def test_this_repo_justfile_is_clean() -> None:
    """The justfile this branch ships must pass its own gate."""
    module = _load_script_module()

    violations = module.find_violations(_JUSTFILE.read_text(encoding="utf-8"))

    assert violations == [], (
        "the repo's own justfile carries a single-quoted interpolation:"
        f" {[(v.line, v.name) for v in violations]}."
        " Every one of them alters its argument silently"
    )


def test_the_report_survives_a_locale_encoded_pipe(tmp: TempDir) -> None:
    """The report must be readable when the child encodes with a non-UTF-8 locale.

    `prek` runs this script as a child with its stdout piped, and on Windows a
    piped ``sys.stdout`` encodes with the locale codec rather than UTF-8. One
    em dash in the report is cp1252 byte ``0x97``, which a UTF-8 reader cannot
    decode: the reader thread dies and ``result.stdout`` becomes ``None``, so
    the failure surfaces as ``TypeError: argument of type 'NoneType' is not
    iterable`` far from its cause (#1986, #2004).

    Forcing ``PYTHONIOENCODING`` reproduces that on every platform, so this
    test is not one that only Windows can fail.
    """
    dirty = tmp / "justfile"
    dirty.write_text("recipe arg:\n    cmd '{{ arg }}'\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--justfile", str(dirty)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )

    assert result.stdout is not None, (
        "the child wrote a byte the UTF-8 reader could not decode, so its"
        " output was lost entirely — a gate whose report cannot be read tells"
        " the author nothing about what to fix"
    )
    assert "quote(arg)" in result.stdout, (
        "the report reached the reader but lost its content; prek shows only"
        " this output, so the replacement form has to survive the pipe"
    )


def test_the_script_exits_non_zero_on_a_dirty_justfile(tmp: TempDir) -> None:
    """The gate must fail the process, not only return a list."""
    dirty = tmp / "justfile"
    dirty.write_text("recipe arg:\n    cmd '{{ arg }}'\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--justfile", str(dirty)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 1, (
        "a checker that finds violations and exits 0 is a no-op gate — prek"
        " reads the exit status, so it would report success on a justfile that"
        f" corrupts its arguments. stdout: {result.stdout}"
    )
    assert "quote(arg)" in result.stdout, (
        "the report must name the replacement form; prek shows only this"
        " output, so a report without the fix leaves the author guessing"
    )


def test_the_script_exits_zero_on_a_clean_justfile(tmp: TempDir) -> None:
    """A clean justfile must not fail the gate."""
    clean = tmp / "justfile"
    clean.write_text("recipe arg:\n    cmd {{ quote(arg) }}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--justfile", str(clean)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, (
        "the gate rejected the very form it tells authors to use, which would"
        f" make it impossible to satisfy. stdout: {result.stdout}"
    )
