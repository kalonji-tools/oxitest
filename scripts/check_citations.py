#!/usr/bin/env python3
"""Resolve every ``path:line`` citation in a text before it is published.

Issue comments are this repository's home for specs and research, so its most
durable records carry its weakest referential integrity. A bare ``path:line``
rots the moment the branch it describes lands, and it rots **silently**: a stale
quote returns zero hits and is unambiguous, while a stale line number resolves
to whatever moved into its place. Every measured instance landed on plausible
neighbouring prose, so it read as correct and was acted on (WF-116).

#1996 remedied this in prose on 2026-08-08, when the finding stood at count 3.
It shipped a clause naming the failure and forbidding the form for one file.
**The finding is now at 22, and 18 of those occurrences are in entries dated
after that clause merged.** The session that filed #2138 published six citations
of the forbidden form while arguing that the finding needed a mechanism.

A rule that is read, quoted, and then broken in one session is not
under-specified. It is unenforced. This is `WP-130` — *"re-resolve every
citation against the tree in the minutes before publishing"* — made into a
command, because a practice that works only when a person remembers it is the
prose remedy with extra steps.

**What this cannot do.** It cannot know what a citation *claims*, so it cannot
refuse a line that resolves to the wrong thing. It puts the current content in
front of the author and refuses only the three cases that need no intent:

* a bare ``CLAUDE.md:<line>`` citation, which the clause already forbids by name;
* a citation to a file that does not exist;
* a line number past the end of its file.

Read a clean result as "every citation was shown to its author", never as
"every citation is correct".

Citations inside a **fenced block** are ignored: a fence holds examples and
sample output, which display the form rather than use it.

A citation inside a **code span is counted**, and that is a deliberate departure
from ``check_disposition.py``. Backticks are the ordinary way to write a path,
so a backticked ``CLAUDE.md`` line reference is a citation and not a display of
one. The first draft of this script stripped code spans, was run against #2131,
and reported **zero** citations on the very thread whose six bare citations
filed #2138. The rule that protects a marker gate blinds a citation checker.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _markdown import strip_fenced

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ANSWER = 2

# The file this repository edits continuously, and so the one whose line numbers
# rot fastest. The clause names it; this constant is not a second policy.
CHURNING_FILE = "CLAUDE.md"

# A citation is a path with an extension, a colon, and a line number. Requiring
# the extension is what keeps a clock time (`10:02`) and a URL port out of the
# match. An en dash is accepted in the range because authors type one.
_CITATION = re.compile(
    r"(?P<path>[\w./+-]+\.[A-Za-z0-9]+)"
    r":(?P<line>\d+)"
    r"(?:[-\u2013](?P<end>\d+))?"
    r"(?:@(?P<commit>[0-9a-fA-F]{7,40}))?"
)


@dataclass(frozen=True, slots=True)
class Citation:
    """One ``path:line`` reference found where it renders as a reference."""

    path: str
    line: int
    end: int | None
    commit: str | None
    text: str  # exactly as written, for the report


@dataclass(frozen=True, slots=True)
class Finding:
    """What one citation resolves to now, and whether it is refused."""

    citation: Citation
    refused: bool
    detail: str


def scan(text: str) -> list[Citation]:
    """Every citation in ``text`` that is used rather than displayed."""
    found = []
    for match in _CITATION.finditer(strip_fenced(text)):
        end = match.group("end")
        found.append(
            Citation(
                path=match.group("path"),
                line=int(match.group("line")),
                end=int(end) if end else None,
                commit=match.group("commit"),
                text=match.group(0),
            )
        )
    return found


def resolve(citation: Citation, repo_root: Path) -> Finding:
    """Resolve one citation against the working tree.

    A citation carrying ``@commit`` is durable by construction, so it is
    reported without being resolved: the commit pins the content, and the line
    may legitimately differ from the current tree.
    """
    if citation.commit is not None:
        return Finding(citation, refused=False, detail="pinned to a commit")

    if citation.path == CHURNING_FILE:
        return Finding(
            citation,
            refused=True,
            detail=(
                f"bare line number into {CHURNING_FILE} — "
                "cite the clause's opening words instead"
            ),
        )

    target = repo_root / citation.path
    if not target.is_file():
        # A path with no directory component is not necessarily a repo-relative
        # citation. It may be a shorthand for a file further down the tree, or
        # an illustration — the citation clause itself quotes `drain.rs:42-44`
        # as an example of a citation that rotted. Refusing correct work is the
        # costlier error, so an unqualified basename is reported, never refused.
        if "/" not in citation.path:
            return Finding(
                citation,
                refused=False,
                detail="not resolved — no directory component",
            )
        return Finding(citation, refused=True, detail="file does not exist")

    lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
    last = citation.end or citation.line
    if last > len(lines):
        return Finding(
            citation,
            refused=True,
            detail=f"line {last} is past end of file ({len(lines)} lines)",
        )

    resolved = lines[citation.line - 1].strip()
    return Finding(citation, refused=False, detail=resolved or "(blank line)")


def review(text: str, repo_root: Path) -> list[Finding]:
    """Resolve every citation in ``text`` against ``repo_root``."""
    return [resolve(citation, repo_root) for citation in scan(text)]


def exit_code(findings: list[Finding]) -> int:
    """Non-zero when any citation was refused."""
    return EXIT_REFUSED if any(finding.refused for finding in findings) else EXIT_OK


def format_findings(findings: list[Finding]) -> str:
    """Render every citation, its verdict, and what it resolves to."""
    if not findings:
        return "no citations found"
    width = max(len(finding.citation.text) for finding in findings)
    lines = []
    for finding in findings:
        mark = "REFUSED" if finding.refused else "       "
        lines.append(f"  {mark}  {finding.citation.text:<{width}}  {finding.detail}")
    refused = sum(finding.refused for finding in findings)
    lines.append("")
    lines.append(f"{len(findings)} citation(s), {refused} refused")
    return "\n".join(lines)


def _read_issue(number: str) -> str | None:
    """The body and every comment of one issue, joined.

    No `--repo`: `gh` resolves the current repository, so naming one here would
    be a second copy of a fact git already holds.
    """
    completed = subprocess.run(
        ["gh", "issue", "view", number, "--json", "body,comments"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    parts = [payload.get("body") or ""]
    parts += [comment.get("body") or "" for comment in payload.get("comments") or []]
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Read a text and report every citation it contains."""
    parser = argparse.ArgumentParser(
        description="Resolve path:line citations in a text."
    )
    parser.add_argument("target", nargs="?", help="a file, or - for stdin")
    parser.add_argument("--issue", help="read an issue's body and comments instead")
    parser.add_argument("--repo-root", default=None, help="tree to resolve against")
    args = parser.parse_args(argv)

    if args.issue:
        text = _read_issue(args.issue)
        if text is None:
            print(f"CANNOT ANSWER — could not read issue {args.issue}")
            return EXIT_CANNOT_ANSWER
    elif args.target == "-":
        text = sys.stdin.read()
    elif args.target:
        path = Path(args.target)
        if not path.is_file():
            print(f"CANNOT ANSWER — {args.target} is not a file")
            return EXIT_CANNOT_ANSWER
        text = path.read_text(encoding="utf-8")
    else:
        parser.error("give a file, -, or --issue")

    repo_root = (
        Path(args.repo_root)
        if args.repo_root
        else Path(__file__).resolve().parent.parent
    )
    findings = review(text, repo_root)
    print(format_findings(findings))
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
