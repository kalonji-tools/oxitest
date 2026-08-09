#!/usr/bin/env python3
"""Refuse to merge when a pull request closes issues its title does not name.

GitHub's closing-keyword parser does not read negation: a body sentence written
to disclaim an issue still closes it. PR #2002 disclaimed #2001 in prose and
closed #2001 on merge. The wording rule in CLAUDE.md cannot reach the case that
matters most — a document explaining this defect has to quote the bad form, and
the pull request fixing it did exactly that at creation — so this gate compares
what GitHub actually parsed against what the title says.

Comparison is set equality. GitHub returns closures in its own order: PR #1980
reports [1940, 1972] against a title reading (#1972, #1940), so a sequence
compare would refuse a pull request that is correct.

The branch name is parsed as a cross-check and reported, never enforced. It is
a free-text slug, so a digit inside one can look like an issue number, and a
false refusal would be worse than the silence.

Run by `just merge-ready` at merge-sequence step 4 (#2005).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_CANNOT_ANSWER = 2

# `#123` anywhere in a title; `chore/123-slug` and `fix/123-124-slug` in a
# branch name. The branch pattern anchors each number to a `/` or `-` boundary
# so that a slug word such as `utf8` or `cp1252` cannot masquerade as one.
_TITLE_REF = re.compile(r"#(\d+)")
_BRANCH_REF = re.compile(r"(?:^|/|-)(\d{2,})(?=-|$)")


@dataclass(frozen=True, slots=True)
class Report:
    """What the gate found on one pull request, ready to render."""

    intended: frozenset[int]
    actual: frozenset[int]
    branch: frozenset[int]


def parse_issue_refs(title: str) -> frozenset[int]:
    """Return every issue number a pull-request title names."""
    return frozenset(int(number) for number in _TITLE_REF.findall(title))


def parse_branch_refs(branch: str) -> frozenset[int]:
    """Return every issue number a branch name carries.

    Deliberately not the ``head -1`` form: this repo uses multi-issue branches
    such as ``fix/1863-1864-slug``, and keeping only the first is the
    silent-drop defect filed as #2011 against a sibling tool.
    """
    return frozenset(int(number) for number in _BRANCH_REF.findall(branch))


def verdict(intended: frozenset[int], actual: frozenset[int]) -> int:
    """Map the two issue sets to an exit status.

    Set equality, checked in both directions. A closure the title does not name
    is the #2002 defect. A title naming an issue the pull request does not close
    is a title that lies about its own scope. Both refuse.
    """
    return EXIT_OK if intended == actual else EXIT_MISMATCH


def format_report(report: Report) -> str:
    """Render the verdict for whoever is standing at the merge.

    The branch note is appended on both paths. Rendering it only on the refusal
    path put it where it is noise and hid it where it is information: a branch
    name that disagrees with an otherwise-correct title is the case the
    cross-check exists to surface, and that case does not refuse.
    """
    if report.intended == report.actual:
        listed = ", ".join(f"#{n}" for n in sorted(report.actual)) or "none"
        lines = [f"merge-ready: closures agree with the title ({listed})"]
        return "\n".join(lines + _branch_note(report))

    unnamed = sorted(report.actual - report.intended)
    unclosed = sorted(report.intended - report.actual)
    lines = ["merge-ready: REFUSED — the title and the parsed closures disagree."]
    if unnamed:
        refs = ", ".join(f"#{n}" for n in unnamed)
        lines.append(
            f"  closes but does not name: {refs}\n"
            f"    A closing keyword reached these. Check the body for a negated\n"
            f"    keyword: a sentence of the form 'does not fix #N' still closes\n"
            f"    #N. Write 'does not address #N', or name them in the title."
        )
    if unclosed:
        refs = ", ".join(f"#{n}" for n in unclosed)
        lines.append(
            f"  names but does not close: {refs}\n"
            f"    Add a 'Closes #N' per issue, or correct the title."
        )
    return "\n".join(lines + _branch_note(report))


def _branch_note(report: Report) -> list[str]:
    """Return the advisory branch-name line, or nothing when it agrees."""
    if not report.branch or report.branch == report.intended:
        return []
    refs = ", ".join(f"#{n}" for n in sorted(report.branch))
    return [f"  note: the branch name carries {refs} (not enforced)"]


def _gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or "gh failed"
        raise RuntimeError(msg)
    return result.stdout


def main(
    argv: list[str] | None = None,
    *,
    fetch: Callable[[list[str]], str] = _gh,
) -> int:
    """Compare a pull request's parsed closures against its title.

    ``argv`` is a parameter so that a test can call this without argparse
    reading the test runner's own command line and exiting 2. ``fetch`` is the
    seam that lets a test supply a payload: injecting it beats reassigning the
    module global, which leaks into later tests when an assertion fails first.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number (default: infer from the current branch)",
    )
    args = parser.parse_args(argv)

    query = ["pr", "view", "--json", "title,closingIssuesReferences,headRefName"]
    if args.pr is not None:
        query.insert(2, str(args.pr))

    # The payload reads live inside the try with the fetch. A missing key means
    # the gate could not answer, and Python exits 1 on an uncaught exception —
    # which is EXIT_MISMATCH, so an infrastructure failure would report itself
    # as a finding about the pull request.
    try:
        payload = json.loads(fetch(query))
        report = Report(
            intended=parse_issue_refs(payload["title"]),
            actual=frozenset(
                ref["number"] for ref in payload["closingIssuesReferences"]
            ),
            branch=parse_branch_refs(payload["headRefName"]),
        )
    except (RuntimeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"merge-ready: CANNOT ANSWER — cannot read this pull request ({exc})",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

    # Always stdout: every command here runs through `direnv exec`, which writes
    # 31 lines to stderr on each invocation (#2003), so a refusal sent there is
    # the hardest line to find in the run that most needs it read.
    print(format_report(report))
    return verdict(report.intended, report.actual)


if __name__ == "__main__":
    sys.exit(main())
