#!/usr/bin/env python3
"""Refuse to merge when a closing issue carries no disposition artifact.

An issue can ship part of its scope and close as COMPLETED. The part it did not
ship then has no owner, and nothing detects that. Three instances were found by
hand on 2026-08-11; two left an unowned remainder, and all three were closed by
a merged pull request — so a gate at merge time is in a position to see them
(#2057).

Detection is presence-only, deliberately. A gate that judged the table's
contents would refuse correct work at the most expensive moment, and it would
need acceptance criteria in a machine-readable form this repo does not have. An
author who writes a dishonest table is not a problem a gate solves; an author
who never writes one is, and that is what this catches.

The rule is one sentence: **the marker counts only where it renders as
nothing.** ``<!-- disposition -->`` is an HTML comment, invisible in a rendered
issue — unless it sits inside a fenced block or a code span, where it renders as
visible text. A visible marker is a display of the convention, not a use of it.
That is what stops this gate from passing its own documentation: every issue
about this convention quotes the marker, and #2057 would otherwise satisfy the
gate off its own spec comment.

Known limit, by design: a marker written bare in prose counts, whatever follows
it. Nothing here can tell that from a real table without reading content, which
the decision ruled out. Read a green result as "an artifact was deliberately
placed", never as "the remainder is owned".

Second known limit: ``gh issue view --json comments`` returns one page and
exposes no truncation signal, so an issue with more comments than that page
holds would hide a marker in a later comment and refuse an author who complied.
Nothing here can read a truncation signal. The busiest issue in this repository
carries 17 comments, measured with ``gh issue list --state all --limit 400
--json number,comments``, so the case is far off. Move to GraphQL if it ever
approaches.

Run by ``just merge-ready`` at merge-sequence step 4, after
``check_closing_issues.py`` — this gate's question is only meaningful once the
closure set is known to agree with the title. Nothing else runs it: a CI context
carrying this check was built and removed on #2072.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from _markdown import strip_quoted

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_CANNOT_ANSWER = 2

DISPOSITION_MARKER = "<!-- disposition -->"

# The span rule — what renders as visible text, and so does not count — lives in
# `_markdown.py`. `check_citations.py` needs the same fact, and a second copy of
# a regex whose subtlety already cost one false refusal is a defect waiting to
# happen.


@dataclass(frozen=True, slots=True)
class Report:
    """What the gate found on one pull request, ready to render.

    The pull request number is deliberately absent: nothing renders it, and a
    field no reader ever sees is a field that can drift from the run it claims
    to describe.
    """

    checked: frozenset[int]
    missing: frozenset[int]


def has_disposition(body: str, comments: list[str]) -> bool:
    """Return whether an issue carries the marker where it renders as nothing."""
    return any(DISPOSITION_MARKER in strip_quoted(text) for text in (body, *comments))


def verdict(report: Report) -> int:
    """Map the report to an exit status. Any bare closing issue refuses."""
    return EXIT_MISSING if report.missing else EXIT_OK


def format_report(report: Report) -> str:
    """Render the verdict for whoever is standing at the merge.

    The passing line names what it checked, so "every closure carries one" can
    be told apart from "there were no closures". The refusal carries the literal
    marker, because the marker is invisible in a rendered issue and an author
    who has never hit this gate has no other way to learn the exact token.
    """
    if not report.missing:
        listed = ", ".join(f"#{n}" for n in sorted(report.checked)) or "none"
        return (
            f"merge-ready: every closing issue carries a disposition table ({listed})"
        )

    refs = ", ".join(f"#{n}" for n in sorted(report.missing))
    return (
        "merge-ready: REFUSED — a closing issue carries no disposition table.\n"
        f"  no disposition: {refs}\n"
        "    Comment on each with one row per acceptance criterion, naming\n"
        "    where each went after this merge — shipped, discharged by another\n"
        "    issue, filed as a new one, or ruled out of scope. Mark it with:\n"
        f"      {DISPOSITION_MARKER}\n"
        "    The marker counts only where it renders as nothing. Inside a code\n"
        "    fence or a code span it is a display of the convention, not a use."
    )


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
    """Refuse when any issue this pull request closes carries no artifact.

    ``argv`` is a parameter so a test can call this without argparse reading the
    test runner's own command line and exiting 2. ``fetch`` is the seam that
    lets a test supply payloads: injecting it beats reassigning the module
    global, which leaks into later tests when an assertion fails first.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number (default: infer from the current branch)",
    )
    args = parser.parse_args(argv)

    query = ["pr", "view", "--json", "closingIssuesReferences"]
    if args.pr is not None:
        query.insert(2, str(args.pr))

    # Every read sits inside the try. Python exits 1 on an uncaught exception,
    # and 1 here means "an issue is bare", so an unguarded KeyError would report
    # an infrastructure failure as a finding about the author's work.
    try:
        payload = json.loads(fetch(query))
        checked = frozenset(
            int(ref["number"]) for ref in payload["closingIssuesReferences"]
        )
        missing = set()
        for number in sorted(checked):
            issue = json.loads(
                fetch(["issue", "view", str(number), "--json", "body,comments"])
            )
            comments = [comment["body"] for comment in issue["comments"]]
            if not has_disposition(issue["body"] or "", comments):
                missing.add(number)
    except (RuntimeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"merge-ready: CANNOT ANSWER — cannot read this pull request ({exc})",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

    report = Report(checked=checked, missing=frozenset(missing))

    # Always stdout: every command here runs through `direnv exec`, which writes
    # 31 lines to stderr on each invocation (#2003), so a refusal sent there is
    # the hardest line to find in the run that most needs it read.
    print(format_report(report))
    return verdict(report)


if __name__ == "__main__":
    sys.exit(main())
