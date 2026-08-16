"""Tests for the citation checker in ``check_citations.py``.

A bare ``path:line`` citation rots silently: it resolves to whatever moved into
its place, so it reads as correct and is acted on. #1996 remedied this in prose
at count 3; the finding is now at 22 and 18 of those occurrences post-date that
clause (#2138).

Two rules carry the design, and they pull in opposite directions:

* a citation inside a **fenced block** is an example, so it does not count;
* a citation inside a **code span** *does* count, because backticks are the
  ordinary way to write a path. A first draft stripped code spans and found
  **zero** citations on #2131 — the thread whose six bare citations filed the
  issue.

Scanning and resolution are pure, so every case is testable offline.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import oxitest as oxi
from oxitest import TempDir

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_citations.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_citations.py`` as a module.

    ``scripts/`` is not a package, so the directory goes on ``sys.path`` for the
    script's own ``_markdown`` import.
    """
    scripts_dir = str(_SCRIPT_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    spec = importlib.util.spec_from_file_location(
        "check_citations_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script_module()


# ── What counts as a citation ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScanCase:
    """One text, and how many citations it must yield."""

    text: str
    expected: int


@oxi.parametrize(
    bare_in_prose=ScanCase(text="See src/exit.rs:28 for the arm.", expected=1),
    inside_a_code_span_still_counts=ScanCase(
        text="See `src/exit.rs:28` for the arm.", expected=1
    ),
    inside_a_backtick_fence_does_not=ScanCase(
        text="Example:\n\n```\nsrc/exit.rs:28\n```\n", expected=0
    ),
    inside_a_tilde_fence_does_not=ScanCase(
        text="Example:\n\n~~~\nsrc/exit.rs:28\n~~~\n", expected=0
    ),
    a_line_range=ScanCase(text="src/drain.rs:42-44 is the span.", expected=1),
    # The dash is escaped rather than typed: authors produce an en dash and the
    # scanner must accept it, but a literal one here is an ambiguous character.
    an_en_dash_range=ScanCase(text="src/drain.rs:42\u201344 is the span.", expected=1),
    pinned_to_a_commit=ScanCase(text="src/drain.rs:42@a9c94125 holds.", expected=1),
    a_clock_time_is_not_a_citation=ScanCase(
        text="It closed at 10:02 today.", expected=0
    ),
    a_word_without_an_extension_is_not=ScanCase(text="the path:line form", expected=0),
    several_in_one_line=ScanCase(
        text="`CLAUDE.md:105` and src/exit.rs:28 both.", expected=2
    ),
)
def test_a_citation_is_counted_where_it_is_used_not_displayed(case: ScanCase) -> None:
    """A fence displays the form; a code span writes a path."""
    found = _script.scan(case.text)

    assert len(found) == case.expected, (
        f"{case.text!r} yielded {len(found)} citations, expected {case.expected}"
    )


def test_this_docstring_style_text_quotes_every_refusal_and_stays_clean() -> None:
    """A checker for a text convention must survive a document describing it.

    The gate for #2057 established the problem: every issue about a convention
    quotes it. Each of the three refusals appears below inside a fence, and none
    may be counted.
    """
    text = (
        "The three refusals are shown here:\n\n"
        "```\n"
        "CLAUDE.md:105\n"
        "src/does_not_exist.rs:12\n"
        "scripts/check_citations.py:99999\n"
        "```\n\n"
        "None of those is a citation, because a fence displays the form.\n"
    )

    found = _script.scan(text)

    assert found == [], f"a self-documenting text must yield no citations, got {found}"


# ── The three refusals ───────────────────────────────────────────────────────


def test_a_bare_churning_file_citation_is_refused() -> None:
    """The clause forbids this form by name, so the checker enforces it."""
    findings = _script.review("see `CLAUDE.md:105` for the rule", _REPO_ROOT)

    assert len(findings) == 1, f"expected one finding, got {len(findings)}"
    assert findings[0].refused, "a bare CLAUDE.md line citation must be refused"
    assert _script.exit_code(findings) == _script.EXIT_REFUSED, (
        "a refusal must exit non-zero"
    )


def test_a_qualified_path_that_does_not_exist_is_refused() -> None:
    """A path carrying a directory is unambiguously repo-relative."""
    findings = _script.review("see src/no_such_file.rs:12", _REPO_ROOT)

    assert findings[0].refused, "a citation to an absent file must be refused"
    assert "does not exist" in findings[0].detail, (
        f"the detail must name the cause, got {findings[0].detail!r}"
    )


def test_an_unqualified_basename_is_reported_rather_than_refused() -> None:
    """Refusing correct work is the costlier error.

    A bare basename may be shorthand for a file further down the tree, or an
    illustration. The citation clause itself quotes ``drain.rs:42-44`` as an
    example of a citation that rotted — a checker that refused its own clause
    would be the finding in a new form.
    """
    findings = _script.review("a cited `drain.rs:42-44` had become `));`", _REPO_ROOT)

    assert len(findings) == 1, f"expected one finding, got {len(findings)}"
    assert not findings[0].refused, (
        f"an unqualified basename must not be refused, got {findings[0].detail!r}"
    )
    assert "no directory component" in findings[0].detail, (
        f"the detail must say why it was not resolved, got {findings[0].detail!r}"
    )


def test_a_line_past_the_end_of_a_file_is_refused(tmp: TempDir) -> None:
    """The loud half of position rot: the file resolved, the line did not."""
    (tmp.path / "short.py").write_text("one\ntwo\n", encoding="utf-8")

    findings = _script.review("see short.py:900", tmp.path)

    assert findings[0].refused, "a line past end of file must be refused"
    assert "past end of file" in findings[0].detail, (
        f"the detail must name the cause, got {findings[0].detail!r}"
    )


# ── What is reported rather than refused ─────────────────────────────────────


def test_a_resolvable_citation_reports_the_line_it_lands_on(tmp: TempDir) -> None:
    """The checker cannot know intent, so it shows the author the content.

    This is the limit stated in the script's docstring: a citation resolving to
    the wrong line is reported, never refused.
    """
    (tmp.path / "sample.py").write_text("first\nsecond\nthird\n", encoding="utf-8")

    findings = _script.review("see sample.py:2", tmp.path)

    assert not findings[0].refused, "a resolvable citation is reported, not refused"
    assert findings[0].detail == "second", (
        f"expected the resolved line, got {findings[0].detail!r}"
    )
    assert _script.exit_code(findings) == _script.EXIT_OK, (
        "a text with no refusal must exit 0"
    )


def test_a_commit_pinned_citation_is_not_resolved_against_the_tree() -> None:
    """``@commit`` pins the content, so the current tree is the wrong subject."""
    findings = _script.review("see src/anything.rs:9999@a9c94125", _REPO_ROOT)

    assert not findings[0].refused, "a commit-pinned citation is durable"
    assert findings[0].detail == "pinned to a commit", (
        f"expected the pinned detail, got {findings[0].detail!r}"
    )
