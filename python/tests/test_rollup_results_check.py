"""Tests for the two gates inside every ``* (required)`` rollup.

#2072 replaced each rollup's results check with a ``jq`` expression reading
``toJSON(needs)``, and deleted ``scripts/check_rollup_agreement.py`` and its
413 lines of tests with it. The replacement arrived with no test, in three
byte-identical copies, and a gate that guards nothing reports **green** — which
is also what it reports when it works.

This file tests the literal that ships. It reads the program out of the
workflow rather than restating it, so there is no second copy to drift from,
which is the defect #2072 removed and would otherwise be reintroduced here.

Two gates, two shapes, and the difference is forced rather than chosen:

* the **results check** is byte-identical across the three rollups (measured),
  so identity is asserted once and one program is exercised;
* the **change-filter guard** is not — its message names the workflow — so its
  behavioural table runs against each copy separately.

Discovery anchors on step *content*, never on step *name*: the three results
steps are already called ``Check results``, ``Check build result`` and
``Check result``, so a name-keyed lookup would silently cover one rollup of
three and read as green.

Known limit, inherited deliberately from the checker this replaces: it proves
the expression reaches the right verdict, not that the surrounding ``run:``
block is gated on it. Widening that means interpreting shell.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
import yaml
from oxitest import TempDir

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# Branch protection matches a rollup on its display `name`, not its job id —
# all three are called `required`. The suffix is the same key
# `check_rollup_agreement.py` used before it was deleted.
_REQUIRED_SUFFIX = " (required)"

# A single-quoted shell string cannot contain a single quote, so the first `'`
# after `jq -e` always closes the program. That is a fact about shell quoting,
# which is what makes this extraction deterministic rather than a guess.
_JQ_PROGRAM = re.compile(r"jq -e '(.*?)'", re.DOTALL)

# GitHub interpolates `${{ … }}` textually before bash is started, so
# substituting it is faithful to what runs rather than a model of it.
_CHANGES_RESULT = "${{ needs.changes.result }}"


@dataclass(frozen=True)
class Rollup:
    """One ``* (required)`` job, with both of the gates it carries."""

    workflow: str
    results_program: str
    change_filter: str


def _find_rollups(workflows: Path = _WORKFLOWS) -> tuple[Rollup, ...]:
    """Every ``* (required)`` job across every workflow, with both gates.

    A job carrying one gate and not the other raises. That case does not exist
    in the tree today, so nothing measured constrains it, and the two options
    are not symmetric: failing breaks a legitimately-shaped new rollup on
    arrival and is recoverable in one edit, while skipping lets a rollup that
    *loses* a gate pass quietly — which is the defect this file exists to
    close. The recoverable failure wins.

    ``workflows`` is a parameter for one reason: the refusal above is a choice
    this file made rather than measured, and a choice nobody can exercise is
    indistinguishable from one nobody implemented. The real directory cannot
    produce a one-gate rollup, so the test that pins it needs its own.
    """
    found: list[Rollup] = []
    for path in sorted(workflows.glob("*.y*ml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (document.get("jobs") or {}).values():
            display_name = str(job.get("name", ""))
            if not display_name.endswith(_REQUIRED_SUFFIX):
                continue
            program, guard = None, None
            for step in job.get("steps", []):
                run = step.get("run", "")
                match = _JQ_PROGRAM.search(run)
                if match:
                    program = match.group(1)
                if "case " in run and "changes.result" in run:
                    guard = run
            if program is None or guard is None:
                missing = "results check" if program is None else "change filter"
                msg = (
                    f"{path.name}: job {display_name!r} has no {missing}. Every "
                    f"rollup carries both gates; a job with one is either "
                    f"half-built or has silently lost one, and this test cannot "
                    f"tell those apart — so it refuses rather than covering less."
                )
                raise AssertionError(msg)
            found.append(Rollup(path.name, program, guard))
    return tuple(found)


def _run_jq(program: str, jobs: tuple[tuple[str, str], ...]) -> bool:
    """True when ``jq`` exits 0, meaning the rollup would pass."""
    executable = shutil.which("jq")
    if executable is None:
        msg = (
            "jq is not on PATH, so the shipped expression cannot be executed. "
            "This raises rather than skipping: a skipped test here is "
            "indistinguishable from a passing one, which is the exact failure "
            "this file was written to stop."
        )
        raise RuntimeError(msg)
    payload = {job: {"result": result} for job, result in jobs}
    completed = subprocess.run(
        [executable, "-e", program],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.returncode == 0


def _run_guard(guard: str, changes_result: str) -> int:
    """Exit status of the change-filter guard for one ``changes`` result.

    ``bash`` is reachable on every runner the suite uses: the workflows already
    declare ``shell: bash`` on the steps that run under ``windows-latest``.
    """
    executable = shutil.which("bash")
    if executable is None:
        msg = (
            "bash is not on PATH, so the shipped guard cannot be executed. "
            "This raises rather than skipping, for the same reason as jq."
        )
        raise RuntimeError(msg)
    script = guard.replace(_CHANGES_RESULT, changes_result)
    completed = subprocess.run(
        [executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.returncode


@dataclass(frozen=True)
class ResultsCase:
    """One ``needs`` payload and the verdict the results check must reach."""

    jobs: tuple[tuple[str, str], ...]
    passes: bool
    kills: str


@dataclass(frozen=True)
class FilterCase:
    """One ``needs.changes.result`` value and the status the guard must exit."""

    changes_result: str
    exits_zero: bool
    why: str


def test_every_required_job_is_found_and_carries_both_gates() -> None:
    """Discovery must find rollups at all, so the file cannot go vacuous.

    The set is discovered rather than listed so a fourth rollup is covered the
    day it is added. That trade has a failure mode in the other direction, and
    this assertion reaches only part of it: an **empty** set is caught here,
    a **smaller** one is not. A job renamed out of the ``(required)`` suffix
    takes the set from three to two, every assertion below then ranges over
    less, and this test still passes.

    That gap is left open deliberately rather than closed with a literal
    ``>= 3``. Nothing inside the repository independently knows how many
    rollups there ought to be — the only source that does is branch
    protection's required-contexts list, which a unit test must not reach for
    — so a literal would be a guess that goes stale the day a rollup is
    legitimately retired, and it would go stale by *failing*, which trains
    the next person to raise the number without looking.
    """
    # Arrange / Act
    rollups = _find_rollups()

    # Assert
    assert rollups, (
        "no `* (required)` job was discovered in any workflow. Every assertion "
        "in this file ranges over this set, so an empty one makes the whole "
        "file vacuous while still reporting green — the same shape as the "
        "vacuity guard these tests exist to protect."
    )


def test_the_results_program_is_identical_in_every_rollup() -> None:
    """One rule, or three rollups judging their jobs differently.

    This is the invariant #2075 named. A change to the semantics needs one edit
    per copy, and two-of-three is a rollup applying a different rule from its
    siblings with nothing comparing them. Identity is asserted rather than
    behaviour-per-copy because identity is strictly stronger: a copy can drift
    in a way that no case in the table distinguishes and still be a second rule.
    """
    # Arrange
    rollups = _find_rollups()

    # Act
    programs = {rollup.workflow: rollup.results_program for rollup in rollups}
    distinct = set(programs.values())

    # Assert
    assert len(distinct) == 1, (
        "the rollups no longer share one results expression, so at least one "
        "judges its jobs by a different rule and nothing else compares them. "
        f"Programs by workflow: {programs}"
    )


@oxi.parametrize(
    success_and_skipped=ResultsCase(
        jobs=(("changes", "success"), ("a", "success"), ("b", "skipped")),
        passes=True,
        kills="the `skipped` allowance",
    ),
    one_failure=ResultsCase(
        jobs=(("changes", "success"), ("a", "failure")),
        passes=False,
        kills="an always-true allowlist",
    ),
    cancelled=ResultsCase(
        jobs=(("changes", "success"), ("a", "cancelled")),
        passes=False,
        kills="an always-true allowlist",
    ),
    one_of_two_failed=ResultsCase(
        jobs=(("changes", "success"), ("a", "success"), ("b", "failure")),
        passes=False,
        kills="an always-true allowlist",
    ),
    only_changes_present=ResultsCase(
        jobs=(("changes", "success"),),
        passes=False,
        kills="the vacuity guard, and the `changes` exclusion",
    ),
    empty_object=ResultsCase(
        jobs=(),
        passes=False,
        kills="the vacuity guard",
    ),
    changes_failed_others_pass=ResultsCase(
        jobs=(("changes", "failure"), ("a", "success")),
        passes=True,
        kills="the `changes` exclusion",
    ),
    every_job_skipped=ResultsCase(
        jobs=(("changes", "success"), ("build", "skipped"), ("doc-tests", "skipped")),
        passes=True,
        kills="the `skipped` allowance",
    ),
)
def test_the_results_program_reaches_the_right_verdict(case: ResultsCase) -> None:
    """The shipped expression, executed, against every shape that reaches it.

    Three of these eight carry the whole mutation load — ``only_changes_present``
    kills two clauses on its own, and it is joined by ``success_and_skipped``
    and ``one_failure``. The remaining five document the semantics rather than
    catching a distinct defect: the three failure-flavoured rows all kill the
    same single clause. The ``kills`` field records which is which, so nobody
    has to re-derive the matrix to know what is safe to change.

    ``every_job_skipped`` is not a hypothetical. `Docs (required)` reached it on
    run 31608777438, printing ``build: skipped`` and ``doc-tests: skipped``, and
    passed. That run is also what proves ``length`` counts skipped jobs, so the
    vacuity guard cannot fire on a rollup whose jobs merely all skipped.
    """
    # Arrange
    rollups = _find_rollups()
    # One program is exercised rather than three because
    # `test_the_results_program_is_identical_in_every_rollup` asserts they are
    # the same string. Which one is therefore arbitrary — but only while that
    # assertion holds, so the two tests are coupled and this names the coupling.
    program = rollups[0].results_program

    # Act
    accepted = _run_jq(program, case.jobs)

    # Assert
    assert accepted == case.passes, (
        f"the rollup would {'pass' if accepted else 'fail'} where it must "
        f"{'pass' if case.passes else 'fail'}. This case is what stands between "
        f"the gate and {case.kills} being removed without anything noticing."
    )


@oxi.parametrize(
    success=FilterCase(
        changes_result="success",
        exits_zero=True,
        why="the test selection is known, so the rollup may judge its jobs",
    ),
    failure=FilterCase(
        changes_result="failure",
        exits_zero=False,
        why="the selection is unknown, so no verdict below it means anything",
    ),
    cancelled=FilterCase(
        changes_result="cancelled",
        exits_zero=False,
        why="the selection is unknown, so no verdict below it means anything",
    ),
    skipped=FilterCase(
        changes_result="skipped",
        exits_zero=False,
        why=(
            "`changes` carries no `if:` and no `needs:`, so it always runs — a "
            "`skipped` here means something went wrong. This is the defect "
            "#1961 fixed, and it is the one clause where this gate and the "
            "results check deliberately treat `skipped` in opposite ways"
        ),
    ),
)
def test_the_change_filter_guard_reaches_the_right_verdict(case: FilterCase) -> None:
    """The rollup's other gate, run per copy because the copies differ.

    The three guards are not byte-identical — each names its own workflow in
    the message — so the identity assertion used for the results check does not
    apply, and the table runs against each copy instead. That is the weaker
    shape, taken because the stronger one does not fit, not by preference.
    """
    # Arrange
    rollups = _find_rollups()

    # Act / Assert
    for rollup in rollups:
        status = _run_guard(rollup.change_filter, case.changes_result)
        exited_zero = status == 0
        assert exited_zero == case.exits_zero, (
            f"{rollup.workflow}: the guard exited {status} on a `changes` result "
            f"of {case.changes_result!r}, so the rollup would "
            f"{'proceed' if exited_zero else 'stop'} where it must "
            f"{'proceed' if case.exits_zero else 'stop'}. {case.why}."
        )


@dataclass(frozen=True)
class HalfRollup:
    """A ``* (required)`` job carrying one gate, and the gate it is missing."""

    step_run: str
    missing: str


@oxi.parametrize(
    results_check_only=HalfRollup(
        step_run="echo \"$NEEDS\" | jq -e 'del(.changes)' > /dev/null",
        missing="change filter",
    ),
    change_filter_only=HalfRollup(
        step_run='case "${{ needs.changes.result }}" in\n  success) ;;\nesac',
        missing="results check",
    ),
)
def test_a_rollup_carrying_one_gate_is_refused(case: HalfRollup, tmp: TempDir) -> None:
    """The choice the premise ledger made, exercised rather than asserted.

    No ``* (required)`` job in this repository carries one gate, so nothing in
    the real tree can reach the refusal in ``_find_rollups`` — which is exactly
    why it needs its own directory here. The alternative was to leave a branch
    that was *decided* in the plan, *written* in the code, and never once run:
    indistinguishable, from the outside, from a branch nobody implemented.

    The refusal is deliberate and it is the recoverable half of a pair. A
    half-built rollup breaks this test on arrival and is fixed in one edit; a
    rollup that silently *lost* a gate would otherwise pass, which is the whole
    defect this file exists to close.
    """
    # Arrange
    workflows = tmp / "workflows"
    workflows.mkdir(parents=True)
    body = textwrap.indent(case.step_run, " " * 14)
    (workflows / "half.yml").write_text(
        f"name: Half\non: push\njobs:\n  required:\n    name: Half (required)\n"
        f"    steps:\n      - run: |\n{body}\n",
        encoding="utf-8",
    )

    # Act / Assert
    with oxi.raises(AssertionError, match=f"has no {case.missing}"):
        _find_rollups(workflows)
