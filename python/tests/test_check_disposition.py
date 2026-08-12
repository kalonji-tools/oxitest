"""Tests for the disposition gate in ``check_disposition.py``.

An issue can ship part of its scope and close as COMPLETED, leaving the rest
unowned. This gate refuses the merge when a closing issue carries no
disposition table (#2057).

Detection is presence-only, and the rule is one sentence: the marker counts
only where it renders as nothing. Inside a fence or a code span it is a
*display* of the convention, not a use of it — which is what stops this gate
from passing its own documentation. Without that rule, #2057 would satisfy the
gate off its own spec comment.

Classification is pure: it takes an issue's text and returns a verdict, so
every case is testable without touching the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import oxitest as oxi
from oxitest import StdCapture

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_disposition.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_disposition.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``
    rather than a normal import. Registering in ``sys.modules`` before
    ``exec_module`` is load-bearing: the script defines a ``@dataclass`` and
    ``dataclasses._process_class`` resolves the defining module through
    ``sys.modules.get(cls.__module__).__dict__``. Executing without registering
    first makes that ``None`` and the decorator dies with ``AttributeError``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_disposition_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script_module()
_MARKER = "<!-- disposition -->"


# ── The rule: the marker counts only where it renders as nothing ─────────────


@dataclass(frozen=True, slots=True)
class TextCase:
    """One issue's text, and whether it must count as carrying the artifact."""

    body: str
    comments: tuple[str, ...]
    expected: bool


_TABLE = "| AC | Owner |\n|---|---|\n| AC5 | shipped |"


@oxi.parametrize(
    bare_in_body=TextCase(
        body=f"{_MARKER}\n\n{_TABLE}",
        comments=(),
        expected=True,
    ),
    bare_in_one_comment_among_several=TextCase(
        body="an ordinary issue body",
        comments=("chatter", f"{_MARKER}\n\n{_TABLE}"),
        expected=True,
    ),
    fenced_with_backticks=TextCase(
        body=f"Write it like this:\n\n```\n{_MARKER}\n```\n",
        comments=(),
        expected=False,
    ),
    fenced_with_tildes=TextCase(
        body=f"~~~\n{_MARKER}\n~~~\n",
        comments=(),
        expected=False,
    ),
    inline_code_span=TextCase(
        body=f"The marker is `{_MARKER}` and it goes on the issue.",
        comments=(),
        expected=False,
    ),
    fenced_inside_a_comment=TextCase(
        body="an ordinary issue body",
        comments=(f"For example:\n\n```md\n{_MARKER}\n```\n",),
        expected=False,
    ),
    table_after_a_longer_closing_fence=TextCase(
        body=f"Example:\n\n```\nsome code\n````\n\n{_MARKER}\n\n{_TABLE}",
        comments=(),
        expected=True,
    ),
    absent_entirely=TextCase(
        body="an ordinary issue body",
        comments=("an ordinary comment",),
        expected=False,
    ),
)
def test_the_marker_counts_only_where_it_renders_as_nothing(case: TextCase) -> None:
    """A displayed marker is not a placed one.

    The four quoting cases are the gate's own safety. Every issue about this
    convention quotes the marker, so a rule that counted a quoted one would
    pass exactly the issues most likely to need the artifact.
    """
    # Arrange / Act
    found = _script.has_disposition(case.body, list(case.comments))

    # Assert
    assert found == case.expected, (
        "counting a quoted marker lets a document about the convention satisfy "
        "the gate; missing a bare one refuses an author who complied"
    )


def test_the_real_1721_body_does_not_count() -> None:
    """The one exemplar predates the convention and carries no marker.

    #1721 published the table this gate asks for, under the heading
    ``## Discharged before this issue was picked up``. It is the right shape
    and it is not detectable, which is why the marker is a new convention
    rather than a detector for an existing one.
    """
    # Arrange
    body = (
        "## Discharged before this issue was picked up\n\n"
        "| Was | Discharged by |\n|---|---|\n"
        "| Scope B: B1, B2, B3 | #1720 |\n"
    )

    # Act
    found = _script.has_disposition(body, [])

    # Assert
    assert not found, (
        "detecting this shape would mean reading content, which the decision "
        "ruled out; the convention is the marker, not the heading"
    )


# ── The verdict ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerdictCase:
    """A closure set, the subset lacking an artifact, and the exit status."""

    checked: frozenset[int]
    missing: frozenset[int]
    expected: int


@oxi.parametrize(
    every_closure_carries_one=VerdictCase(frozenset({2057}), frozenset(), 0),
    one_of_two_is_bare=VerdictCase(frozenset({2057, 2058}), frozenset({2058}), 1),
    both_orphans_bare=VerdictCase(frozenset({1750, 1805}), frozenset({1750, 1805}), 1),
    dependabot_closes_nothing=VerdictCase(frozenset(), frozenset(), 0),
)
def test_verdict_refuses_only_on_a_bare_closing_issue(case: VerdictCase) -> None:
    """AC10, both directions: refuse a bare closing issue, pass a marked one."""
    # Arrange
    report = _script.Report(checked=case.checked, missing=case.missing)

    # Act
    status = _script.verdict(report)

    # Assert
    assert status == case.expected, (
        "a wrong verdict here either lets an issue close with an unowned "
        "remainder, or blocks a merge whose author already published the table"
    )


# ── Rendering, which a reader sees only when the gate refuses ────────────────


def test_the_refusal_names_the_bare_issue_and_the_token_to_paste() -> None:
    """An invisible marker is undiscoverable, so the refusal must supply it."""
    # Arrange
    report = _script.Report(checked=frozenset({2057, 2058}), missing=frozenset({2058}))

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert "#2058" in rendered, (
        "a refusal that does not name the issue leaves the author checking "
        "every closure by hand to find which one it meant"
    )
    assert _MARKER in rendered, (
        "the marker is invisible in a rendered issue, so an author who has "
        "never hit this gate has no other way to learn the exact token"
    )


@dataclass(frozen=True, slots=True)
class PassingCase:
    """A passing run's closure set, and the substring its report must carry."""

    checked: frozenset[int]
    expected: str


@oxi.parametrize(
    one_closure_is_named=PassingCase(frozenset({2057}), "#2057"),
    no_closures_say_none=PassingCase(frozenset(), "none"),
)
def test_the_passing_report_says_what_it_checked(case: PassingCase) -> None:
    """A passing run must say which closures it cleared, or that there were none.

    Both halves guard the same failure: a success line that names nothing reads
    identically whether every closure carried a table or the query returned no
    closures at all.
    """
    # Arrange
    report = _script.Report(checked=case.checked, missing=frozenset())

    # Act
    rendered = _script.format_report(report)

    # Assert
    assert case.expected in rendered, (
        "a bare success line cannot be told apart from a run that found no "
        "closing issues at all"
    )


# ── The failure the gate must not confuse with a finding ─────────────────────


@dataclass
class FakeGh:
    """A ``gh`` stand-in that answers by subcommand.

    A dataclass rather than ``MagicMock``, per the testing guidelines, and
    injected rather than monkeypatched so a failing assertion cannot leak the
    seam into a later test.
    """

    pr_payload: str
    issue_payloads: dict[int, str] = field(default_factory=dict)

    def __call__(self, args: list[str]) -> str:
        """Answer a ``pr view`` or an ``issue view <number>`` call."""
        if args[0] == "pr":
            return self.pr_payload
        return self.issue_payloads[int(args[2])]


@dataclass(frozen=True, slots=True)
class PayloadCase:
    """A ``gh pr view`` payload the gate cannot read."""

    payload: str


@oxi.parametrize(
    missing_closures=PayloadCase('{"title": "chore: x (#1)"}'),
    closure_entry_has_no_number=PayloadCase('{"closingIssuesReferences": [{}]}'),
    not_an_object=PayloadCase("[]"),
    closures_not_a_list=PayloadCase('{"closingIssuesReferences": 7}'),
)
def test_an_unreadable_pull_request_cannot_answer_rather_than_refuse(
    case: PayloadCase,
    cap: StdCapture,
) -> None:
    """Exit 1 means an issue is bare; an unreadable payload must not borrow it.

    Python exits 1 on an uncaught exception, and 1 is ``EXIT_MISSING``. So an
    unguarded ``KeyError`` would report an infrastructure failure as a finding
    about the author's work — sending them to hunt for a disposition table that
    was never the problem.

    ``cap`` is not decoration. ``main`` writes to the real stdout, and this
    suite shares one worker process, so an uncaptured write here lands inside
    whichever later test captures that descriptor.
    """
    # Arrange / Act
    status = _script.main([], fetch=FakeGh(pr_payload=case.payload))
    cap.readouterr()

    # Assert
    assert status == _script.EXIT_CANNOT_ANSWER, (
        "an infrastructure failure reported as a finding is the same defect "
        "this gate exists to prevent, one layer down"
    )


def test_an_unreadable_issue_cannot_answer_rather_than_refuse(
    cap: StdCapture,
) -> None:
    """The second fetch is inside the guard too, not only the first.

    A pull request can read cleanly while the issue behind it does not. An
    issue payload with no ``comments`` key must not read as an issue with no
    disposition table.
    """
    # Arrange
    fetch = FakeGh(
        pr_payload=json.dumps({"closingIssuesReferences": [{"number": 2057}]}),
        issue_payloads={2057: json.dumps({"body": "the issue"})},
    )

    # Act
    status = _script.main([], fetch=fetch)
    cap.readouterr()

    # Assert
    assert status == _script.EXIT_CANNOT_ANSWER, (
        "a half-read issue reported as bare would refuse a merge whose author "
        "may well have published the table"
    )


def test_a_bare_closing_issue_refuses_end_to_end(cap: StdCapture) -> None:
    """The whole path, from closure set to refusal, with no network."""
    # Arrange
    fetch = FakeGh(
        pr_payload=json.dumps({"closingIssuesReferences": [{"number": 2057}]}),
        issue_payloads={2057: json.dumps({"body": "no table here", "comments": []})},
    )

    # Act
    status = _script.main([], fetch=fetch)
    captured = cap.readouterr()

    # Assert
    assert status == _script.EXIT_MISSING, (
        "this is the whole point of the gate: an issue about to close with no "
        "record of where its undelivered scope went"
    )
    assert "#2057" in captured.out, (
        "the refusal must reach stdout — every command here runs through "
        "direnv exec, which writes 31 lines to stderr on each invocation"
    )


def test_a_marked_closing_issue_passes_end_to_end(cap: StdCapture) -> None:
    """The seam must not change the answer for an issue that complied."""
    # Arrange
    fetch = FakeGh(
        pr_payload=json.dumps({"closingIssuesReferences": [{"number": 2057}]}),
        issue_payloads={
            2057: json.dumps(
                {
                    "body": "the issue",
                    "comments": [{"body": f"{_MARKER}\n\n| AC | Owner |"}],
                }
            )
        },
    )

    # Act
    status = _script.main([], fetch=fetch)
    cap.readouterr()

    # Assert
    assert status == _script.EXIT_OK, (
        "a guard that refuses every payload would pass the unreadable cases "
        "above while making the merge sequence unusable"
    )
