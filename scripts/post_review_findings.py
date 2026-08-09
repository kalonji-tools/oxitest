#!/usr/bin/env python3
"""Post one stage-8 review pass onto a pull request as review threads.

Each finding becomes a thread anchored where the finding is, so a reader clicks
straight to the code rather than hunting for a symbol named in a table. The
thread's Resolve button records the disposition, and ``check_review_threads.py``
refuses to merge while any thread is unresolved (#2007).

Anchoring is narrower than GitHub's documentation suggests. All of the following
were measured against a live pull request, not read:

* An inline comment is accepted only on a line inside a diff hunk. The hunk
  header's ``+start,count`` range *is* the commentable set — git's default
  context is already inside ``count``, so widening it again yields lines the API
  refuses.
* ``subject_type`` is rejected inside a review payload: the GraphQL type
  ``DraftPullRequestReviewComment`` has no such field. File-level threads exist
  only via the standalone ``POST /pulls/{n}/comments`` endpoint, and therefore
  cannot be grouped under a review.
* GraphQL's ``addPullRequestReviewThread`` accepts ``subjectType: FILE`` and
  then returns ``thread: null`` and creates nothing. It is not an alternative.
* A file the branch does not touch cannot be anchored at all. Those findings
  become issues citing ``path:line@commit`` plus a symbol, and appear here only
  as an index row in the review body.
* A file whose diff carries no ``patch`` — binary, or a very large diff — can be
  anchored only at file level.

Order of operations is deliberate: validate everything first, then post the
file-level comments, then the review last. A review body claiming a thread that
does not exist is WF-079's exact failure mode, where the artifact existed and
was wrong. Orphan threads merely block the merge, which fails closed.

Every body this script publishes is written in ASD-STE100 Simplified Technical
English — see the stage-8 clause in ``CLAUDE.md``. That governs the spec files
it consumes, not this module: the script copies ``title`` and ``body`` through
unchanged, so the register is the author's obligation and nothing here can
check it.

Known limit, by design: re-posting a pass is not supported. If any of this
pass's markers already exist the script refuses and names them, rather than
silently doubling every thread. It also cannot tell whether the pass found
everything it should have — it posts what it is given, in the words it is
given.

Exits 0 on success, 1 if validation refuses the spec, and 2 if it cannot reach
the pull request.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ANSWER = 2

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_MARKER = re.compile(r"\*\*([^*]+ #\d+)\*\*")


def commentable_lines(patch: str) -> set[int]:
    """Return the head-file lines GitHub will accept an inline comment on.

    The hunk header's ``+start,count`` already spans the context lines git
    emitted, so the range is used as-is. Widening it by the context size again
    produces line numbers that are refused with 422 — a failure that surfaces as
    an API error rather than as the arithmetic mistake it is.
    """
    lines: set[int] = set()
    for match in _HUNK.finditer(patch):
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        lines.update(range(start, start + count))
    return lines


class DiffIndex:
    """Which paths are in the diff, and which of their lines can be anchored."""

    def __init__(self, patches: dict[str, str | None]) -> None:
        """Build the index from ``filename -> patch`` as the files API returns it.

        A ``None`` patch is meaningful, not missing: GitHub omits the field for
        binary files and very large diffs, and those anchor only at file level.
        """
        self._patches = patches
        self._lines = {
            path: (commentable_lines(patch) if patch is not None else set())
            for path, patch in patches.items()
        }

    def is_in_diff(self, path: str) -> bool:
        """Whether the branch touches this path at all."""
        return path in self._patches

    def has_patch(self, path: str) -> bool:
        """Whether GitHub rendered a diff for it.

        Binary files and very large diffs are returned with no ``patch``, and
        those accept only a file-level anchor.
        """
        return self._patches.get(path) is not None

    def can_anchor_line(self, path: str, line: int) -> bool:
        """Whether an inline comment on this line will be accepted."""
        return line in self._lines.get(path, set())


def marker(slug: str, finding_id: int) -> str:
    """Return the thread's identity.

    Namespaced by pass, because two passes each emit a ``#1`` and an
    un-namespaced marker makes a disposition land on whichever thread matched
    first.
    """
    return f"{slug} #{finding_id}"


def validate(spec: dict, diff: DiffIndex, existing_markers: set[str]) -> list[str]:
    """Return every reason the spec cannot be posted; empty means go.

    Every problem is collected rather than raising on the first, so one run
    names everything wrong instead of sending the author round a fix-and-retry
    loop.
    """
    problems: list[str] = []
    slug = spec["slug"]
    for finding in spec["findings"]:
        tag = marker(slug, finding["id"])
        if tag in existing_markers:
            problems.append(
                f"{tag}: a thread with this marker already exists on the PR"
            )
            continue
        path = finding.get("path")
        if path is None:
            if "issue" not in finding:
                problems.append(
                    f"{tag}: no path and no issue — a finding off the diff must "
                    f"carry the number of the issue it was filed as"
                )
            continue
        if not diff.is_in_diff(path):
            problems.append(
                f"{tag}: {path} is not in the diff, so no API can anchor it. "
                f"File it as an issue and give the finding an 'issue' key"
            )
            continue
        line = finding.get("line")
        if line is None:
            continue
        if not diff.has_patch(path):
            problems.append(
                f"{tag}: {path} has no rendered diff (binary, or too large), so it "
                f"can only be anchored at file level — drop the 'line' key"
            )
            continue
        if not diff.can_anchor_line(path, line):
            problems.append(
                f"{tag}: {path}:{line} is outside every diff hunk, so an inline "
                f"anchor is refused — drop the 'line' key to post file-level"
            )
    return problems


def review_body(spec: dict) -> str:
    """Render the review body, which reconciles what the pass produced.

    File-level threads cannot live inside a review, so a pass's findings are
    physically split across a review and N loose comments. This index is the one
    place that says what the pass produced and where each finding went.
    """
    rows = []
    for finding in spec["findings"]:
        if "line" in finding:
            where = f"inline `{finding['path']}:{finding['line']}`"
        elif "path" in finding:
            where = f"file-level `{finding['path']}`"
        else:
            where = f"off-diff — filed #{finding['issue']}"
        rows.append(f"| {finding['id']} | {finding['title']} | {where} |")

    parts = [f"## {spec['pass']} — {len(spec['findings'])} findings", ""]
    narrative = spec.get("narrative", "")
    if narrative:
        parts += [narrative, ""]
    parts += ["| # | finding | posted as |", "|---|---|---|", *rows, ""]
    parts.append(
        "Every threaded finding above is unresolved. `just merge-ready` blocks the "
        "merge until each one carries a disposition — which may be a refusal or a "
        "deferral, not only a fix."
    )
    return "\n".join(parts)


def comment_body(slug: str, finding: dict) -> str:
    """Render one thread's opening comment, marker first."""
    return (
        f"**{marker(slug, finding['id'])}** — {finding['title']}\n\n{finding['body']}"
    )


def _gh(args: list[str], stdin: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or "gh failed"
        raise RuntimeError(msg)
    return result.stdout


def paged(raw: str) -> list[dict]:
    """Flatten a ``gh api --paginate --slurp`` response into one list.

    Without ``--slurp``, ``gh`` emits each page as a separate top-level JSON
    array, so a multi-page response is ``[…][…]`` and ``json.loads`` raises
    ``Extra data``. With it, the pages arrive wrapped in an outer array and only
    need flattening — a distinction that is invisible on any pull request small
    enough to fit in one page, which is every pull request until it is not.
    """
    return [item for page in json.loads(raw) for item in page]


def existing_markers(comments: list[dict]) -> set[str]:
    """Extract the finding markers already present on the pull request.

    Matches only the ``<slug> #<n>`` shape. ``GET /pulls/{n}/comments`` returns
    disposition replies as well as thread openers, and a reply reads
    ``**Fixed** — reason``; a looser pattern collects those verbs too, and the
    resulting set does not contain what its name says it contains.
    """
    found = set()
    for comment in comments:
        match = _MARKER.match(comment["body"])
        if match:
            found.add(match.group(1))
    return found


def main() -> int:
    """Validate a pass spec, then post its findings as review threads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="path to the pass's findings JSON")
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number (default: infer from the current branch)",
    )
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    try:
        repo = json.loads(_gh(["repo", "view", "--json", "nameWithOwner"]))[
            "nameWithOwner"
        ]
        view = json.loads(_gh(["pr", "view", "--json", "number,headRefOid"]))
        pr = args.pr if args.pr is not None else view["number"]
        head_sha = view["headRefOid"]
    except (RuntimeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"review-post: CANNOT ANSWER — no pull request for this branch ({exc})",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

    files = paged(
        _gh(["api", f"repos/{repo}/pulls/{pr}/files", "--paginate", "--slurp"])
    )
    diff = DiffIndex({entry["filename"]: entry.get("patch") for entry in files})
    existing = paged(
        _gh(["api", f"repos/{repo}/pulls/{pr}/comments", "--paginate", "--slurp"])
    )

    problems = validate(spec, diff, existing_markers(existing))
    if problems:
        print("review-post: REFUSED — nothing was posted:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_REFUSED

    slug = spec["slug"]
    # File-level comments first, the review last. A review body claiming a
    # thread that does not exist is the failure this design exists to prevent;
    # an orphan thread merely blocks the merge, which is the safe direction.
    for finding in spec["findings"]:
        if "path" in finding and "line" not in finding:
            payload = json.dumps(
                {
                    "commit_id": head_sha,
                    "path": finding["path"],
                    "subject_type": "file",
                    "body": comment_body(slug, finding),
                }
            )
            _gh(
                [
                    "api",
                    f"repos/{repo}/pulls/{pr}/comments",
                    "-X",
                    "POST",
                    "--input",
                    "-",
                ],
                stdin=payload,
            )
            print(f"file-level thread posted for {marker(slug, finding['id'])}")

    review = json.dumps(
        {
            "event": "COMMENT",
            "body": review_body(spec),
            "comments": [
                {
                    "path": finding["path"],
                    "line": finding["line"],
                    "side": "RIGHT",
                    "body": comment_body(slug, finding),
                }
                for finding in spec["findings"]
                if "line" in finding
            ],
        }
    )
    _gh(
        ["api", f"repos/{repo}/pulls/{pr}/reviews", "-X", "POST", "--input", "-"],
        stdin=review,
    )
    print(f"review posted for {spec['pass']} ({len(spec['findings'])} findings)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
