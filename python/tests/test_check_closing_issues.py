"""Tests for the closure gate in ``check_closing_issues.py``.

A pull request body states which issues it closes; GitHub's parser decides.
The two disagree silently when a body disclaims an issue, because the parser
does not read negation — a sentence of the form ``does not fix #N`` closes
``#N``. Classification is pure: it takes a title string and a decoded closure
list and returns the verdict, so every case is testable without touching the
network (#2005).

The fixtures are real. PR #2002 closed a live issue it disclaimed in prose, and
PR #2016 — the pull request that fixes this defect — reproduced it at creation
by quoting the bad form.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import oxitest as oxi
from oxitest import StdCapture

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_closing_issues.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_closing_issues.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``
    rather than a normal import. Registering in ``sys.modules`` before
    ``exec_module`` is load-bearing: the script defines a ``@dataclass`` and
    ``dataclasses._process_class`` resolves the defining module through
    ``sys.modules.get(cls.__module__).__dict__``. Executing without registering
    first makes that ``None`` and the decorator dies with ``AttributeError``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_closing_issues_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script_module()


# ── Title parsing ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TitleCase:
    """One pull-request title and the issue numbers it must yield."""

    title: str
    expected: frozenset[int]


@oxi.parametrize(
    one_issue=TitleCase("chore: name the environment (#2005)", frozenset({2005})),
    two_issues=TitleCase(
        "docs: two clause changes (#1972, #1940)", frozenset({1972, 1940})
    ),
    dependabot_names_none=TitleCase(
        "build(deps): bump the cargo-deps group with 4 updates", frozenset()
    ),
    two_issues_other_form=TitleCase(
        "fix: exitcode ord and ctrf name (#1863, #1864)", frozenset({1863, 1864})
    ),
)
def test_title_issue_numbers_are_parsed(case: TitleCase) -> None:
    """A title yields every issue it names, not only the first."""
    # Arrange / Act
    parsed = _script.parse_issue_refs(case.title)

    # Assert
    assert parsed == case.expected, (
        "the gate compares the title's issue set against GitHub's closures; "
        "dropping any number silently widens what a merge is allowed to close"
    )


# ── Branch parsing (cross-check only, never enforced) ────────────────────────


@dataclass(frozen=True, slots=True)
class BranchCase:
    """One branch name and the issue numbers it must yield."""

    branch: str
    expected: frozenset[int]


@oxi.parametrize(
    one_issue=BranchCase("chore/2005-environment-axis", frozenset({2005})),
    two_issues=BranchCase("fix/1863-1864-exitcode-ord", frozenset({1863, 1864})),
    no_issue=BranchCase("docs/workflow-evals-remedies", frozenset()),
    digits_in_slug_are_not_issues=BranchCase(
        "fix/2004-utf8-entry-point-streams", frozenset({2004})
    ),
)
def test_branch_issue_numbers_are_parsed(case: BranchCase) -> None:
    """A multi-issue branch yields both numbers, and a slug digit yields none.

    The known failure mode is ``head -1``, which keeps only the first — the
    defect filed as #2011 against a sibling tool. This cross-check must not
    reproduce it, and must not invent an issue out of ``utf8`` either.
    """
    # Arrange / Act
    parsed = _script.parse_branch_refs(case.branch)

    # Assert
    assert parsed == case.expected, (
        "a branch cross-check that drops the second issue reports an agreement "
        "it has not established, and one that invents a number reports a "
        "disagreement that does not exist"
    )


# ── The verdict, on measured pull requests ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerdictCase:
    """A measured pull request's two issue sets and the status they must give."""

    intended: frozenset[int]
    actual: frozenset[int]
    expected: int


@oxi.parametrize(
    pr_2016_at_creation=VerdictCase(frozenset({2005}), frozenset({2001, 2005}), 1),
    pr_2002_merged=VerdictCase(frozenset({1998}), frozenset({1998, 2001}), 1),
    pr_2016_corrected=VerdictCase(frozenset({2005}), frozenset({2005}), 0),
    pr_1980_reordered=VerdictCase(frozenset({1972, 1940}), frozenset({1940, 1972}), 0),
    pr_1983_dependabot=VerdictCase(frozenset(), frozenset(), 0),
    title_names_an_unclosed_issue=VerdictCase(frozenset({1, 2}), frozenset({1}), 1),
)
def test_verdict_matches_measured_pull_requests(case: VerdictCase) -> None:
    """Set equality, both directions, on six real pull requests."""
    # Arrange / Act
    status = _script.verdict(case.intended, case.actual)

    # Assert
    assert status == case.expected, (
        "a wrong verdict here either lets a merge close an issue nobody named, "
        "or refuses a pull request that is correct"
    )


# ── Rendering, which a reader sees only when the gate refuses ────────────────


def test_agreement_names_every_closure() -> None:
    """The agreeing report lists the issues, so a reader can check them."""
    # Arrange
    report = _script.Report(
        intended=frozenset({1940, 1972}),
        actual=frozenset({1972, 1940}),
        branch=frozenset({1940, 1972}),
    )

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert "#1940, #1972" in rendered, (
        "an agreeing report that hides which issues agreed gives a reader "
        "nothing to check the title against"
    )


def test_agreement_with_no_issues_says_none() -> None:
    """A Dependabot pull request closes nothing and must still read clearly."""
    # Arrange
    report = _script.Report(frozenset(), frozenset(), frozenset())

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert "none" in rendered, (
        "an empty issue list rendered as an empty string reads as a truncated "
        "message rather than a deliberate result"
    )


def test_refusal_names_the_unnamed_closure_and_the_cause() -> None:
    """The #2002 case: the report must name the issue and the negated keyword."""
    # Arrange
    report = _script.Report(
        intended=frozenset({1998}),
        actual=frozenset({1998, 2001}),
        branch=frozenset({1998}),
    )

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert "closes but does not name: #2001" in rendered, (
        "a refusal that does not name the offending issue leaves the author "
        "searching a whole body for a keyword they wrote on purpose"
    )
    assert "does not address #N" in rendered, (
        "the negated keyword is the cause in every recorded instance, so the "
        "refusal states the remedy rather than only the symptom"
    )


def test_refusal_names_an_issue_the_body_never_closed() -> None:
    """The reverse direction: the title claims more than the body delivers."""
    # Arrange
    report = _script.Report(
        intended=frozenset({43, 44}),
        actual=frozenset({43}),
        branch=frozenset({43, 44}),
    )

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert "names but does not close: #44" in rendered, (
        "this is the documented one-keyword-per-issue defect; a refusal that "
        "reports only the closure direction cannot describe it"
    )


def test_a_disagreeing_branch_name_is_reported_and_not_enforced() -> None:
    """The cross-check appears as a note, and never changes the verdict."""
    # Arrange
    report = _script.Report(
        intended=frozenset({2005}),
        actual=frozenset({2005}),
        branch=frozenset({2005, 1999}),
    )

    # Act
    rendered = _script.format_report(report)
    status = _script.verdict(report.intended, report.actual)

    # Assert
    assert status == _script.EXIT_OK, (
        "the branch name is a free-text slug, so enforcing it would refuse "
        "correct pull requests over a digit inside a word"
    )
    assert "not enforced" in rendered, (
        "a note that does not say it is advisory reads as a refusal reason"
    )


# ── The failure the gate must not confuse with a finding ─────────────────────


@dataclass(frozen=True, slots=True)
class PayloadCase:
    """A gh payload that the gate cannot read, and why it cannot."""

    payload: str


@oxi.parametrize(
    missing_title=PayloadCase('{"closingIssuesReferences": [], "headRefName": "x"}'),
    missing_closures=PayloadCase('{"title": "chore: x (#1)", "headRefName": "x"}'),
    missing_branch=PayloadCase(
        '{"title": "chore: x (#1)", "closingIssuesReferences": []}'
    ),
    closure_entry_has_no_number=PayloadCase(
        '{"title": "chore: x (#1)", "closingIssuesReferences": [{}],'
        ' "headRefName": "x"}'
    ),
    not_an_object=PayloadCase("[]"),
)
def test_an_unreadable_payload_cannot_answer_rather_than_refuse(
    case: PayloadCase,
    cap: StdCapture,
) -> None:
    """A payload the gate cannot read must exit CANNOT ANSWER, never MISMATCH.

    Python exits 1 on an uncaught exception, and 1 is EXIT_MISMATCH. So an
    unguarded ``KeyError`` would report an infrastructure failure as a finding
    about the pull request — the same defect this gate exists to prevent, one
    layer down.

    ``cap`` is not decoration. ``main`` writes to the real stdout, and this
    suite shares one worker process, so an uncaptured write here lands inside
    whichever later test captures that descriptor. That is measured: it broke
    ``test_fdcapture_captures_fd_write`` in the full run while every
    single-file run stayed green.
    """
    # Arrange / Act
    status = _script.main([], fetch=lambda _args: case.payload)
    cap.readouterr()

    # Assert
    assert status == _script.EXIT_CANNOT_ANSWER, (
        "exit 1 means the title and the closures disagree; a payload the gate "
        "could not read must not borrow that meaning"
    )


def test_a_readable_payload_reaches_a_verdict(cap: StdCapture) -> None:
    """The fetch seam must not change the answer for a payload that is fine."""
    # Arrange / Act
    status = _script.main(
        [],
        fetch=lambda _args: (
            '{"title": "chore: x (#1)", "closingIssuesReferences": [{"number": 1}],'
            ' "headRefName": "chore/1-x"}'
        ),
    )
    captured = cap.readouterr()

    # Assert
    assert "closures agree with the title (#1)" in captured.out, (
        "the agreeing path must render its report; a silent success gives the "
        "reader nothing to check the title against"
    )
    assert status == _script.EXIT_OK, (
        "a guard that refuses every payload would pass the unreadable cases "
        "above while making the gate useless"
    )
