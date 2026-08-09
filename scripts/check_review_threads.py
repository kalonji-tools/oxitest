#!/usr/bin/env python3
"""Refuse to merge while any pull-request review thread is unresolved.

Stage 8 posts every review finding as a GitHub review thread; this is the gate
stage 9's merge sequence runs before ``gh pr merge``. "Resolved" does not mean
"implemented" — a refuted, deferred or accepted finding is resolved once its
reply says so, and the Resolve button is what records that (#2007).

Why a script rather than branch protection alone: ``main`` requires one
approving review and an author cannot approve their own pull request, so every
merge here uses ``gh pr merge --admin``. ``required_conversation_resolution`` is
enabled for the signal it gives in the merge box; this script is the block.

Why it sits at merge-sequence position 4 rather than first: a check run before
the rebase can be true when it runs and false at merge, because a thread opened
while CI is running would never be re-read. Reading the state that will actually
be merged cannot go stale.

Counts every unresolved thread regardless of origin, matching GitHub's own
conversation-resolution semantics. A question the maintainer leaves on the pull
request should block the merge; that is the feature. Filtering to marked
findings would also let a thread with a malformed marker escape, which is a
failure that reads as success.

Known limit, by design: this counts threads that *exist*. A finding a review
pass never emitted is invisible here, and nothing in #2007 can see one. WF-079's
recorded loss — a finding that vanished between the pass and the disposition
table — is made impossible by the posting half, not by this gate. Do not read a
green result as "the review was complete"; read it as "every finding that was
posted has an answer".

Exits 0 when every thread is resolved, 1 with a report naming each unresolved
thread, and 2 when it cannot answer the question at all: more than one page of
threads, or no pull request for the current branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

EXIT_OK = 0
EXIT_UNRESOLVED = 1
EXIT_CANNOT_ANSWER = 2

_THREAD_QUERY = """
query($owner: String!, $name: String!, $pr: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          isOutdated
          path
          originalLine
          subjectType
          comments(first: 1) { nodes { body } }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Thread:
    """One unresolved review thread, reduced to what the report needs."""

    path: str
    original_line: int | None
    is_outdated: bool
    is_file_level: bool
    first_line: str

    def location(self) -> str:
        """Render the anchor, file-level threads included.

        File-level threads report ``originalLine: 1`` rather than null, so the
        subject type is the only reliable discriminator. Keying off the line
        value gives a fallback that never fires and renders every file-level
        finding as ``path:1`` — a line number that is not real.
        """
        if self.is_file_level:
            return f"{self.path} (file-level)"
        return f"{self.path}:{self.original_line}"


def parse_threads(payload: dict) -> tuple[list[Thread], bool]:
    """Reduce a decoded payload to its unresolved threads and a truncation flag.

    ``isOutdated`` is deliberately not a filter. A thread whose code has moved
    still needs a disposition; that is the property the gate depends on.
    """
    review_threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
    truncated = bool(review_threads["pageInfo"]["hasNextPage"])
    unresolved: list[Thread] = []
    for node in review_threads["nodes"]:
        if node["isResolved"]:
            continue
        comments = node["comments"]["nodes"]
        body = comments[0]["body"] if comments else ""
        unresolved.append(
            Thread(
                path=node["path"],
                original_line=node["originalLine"],
                is_outdated=bool(node["isOutdated"]),
                is_file_level=node["subjectType"] == "FILE",
                first_line=body.split("\n")[0],
            )
        )
    return unresolved, truncated


def format_report(unresolved: list[Thread], total: int) -> str:
    """Render the gate's output for either outcome.

    The clean report states the total it checked, so "all resolved" can be told
    apart from "the query returned nothing".
    """
    if not unresolved:
        return f"merge-ready: OK — {total} thread(s), 0 unresolved"
    lines = [
        f"merge-ready: BLOCKED — {len(unresolved)} of {total} thread(s) unresolved",
        "",
    ]
    for thread in unresolved:
        suffix = (
            "  [outdated — code moved, still needs a disposition]"
            if thread.is_outdated
            else ""
        )
        lines.append(f"  - {thread.location()}{suffix}")
        lines.append(f"      {thread.first_line}")
    return "\n".join(lines)


def exit_code(unresolved: list[Thread], *, truncated: bool) -> int:
    """Map a classification to the gate's exit status.

    Truncation outranks everything else. A gate that cannot see every thread
    must refuse rather than report a count it cannot stand behind, so a
    truncated page exits ``EXIT_CANNOT_ANSWER`` even when every thread it *could*
    see is resolved — which is exactly the case that would otherwise read as a
    clean pass.
    """
    if truncated:
        return EXIT_CANNOT_ANSWER
    return EXIT_UNRESOLVED if unresolved else EXIT_OK


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


def _resolve_target(pr: int | None) -> tuple[str, str, int]:
    """Return ``(owner, name, pr)``, inferring the pull request from the branch.

    Inference rather than a required argument: the merge sequence runs this from
    whatever worktree you are standing in, and a wrong number would gate the
    wrong pull request silently. A branch with no pull request raises instead.
    """
    repo = json.loads(_gh(["repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    owner, name = repo.split("/", 1)
    number = (
        pr
        if pr is not None
        else json.loads(_gh(["pr", "view", "--json", "number"]))["number"]
    )
    return owner, name, number


def main() -> int:
    """Report every unresolved review thread on the current pull request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number (default: infer from the current branch)",
    )
    args = parser.parse_args()

    try:
        owner, name, pr = _resolve_target(args.pr)
    except (RuntimeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"merge-ready: CANNOT ANSWER — no pull request for this branch ({exc})",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

    payload = json.loads(
        _gh(
            [
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr}",
                "-f",
                f"query={_THREAD_QUERY}",
            ]
        )
    )
    unresolved, truncated = parse_threads(payload)
    status = exit_code(unresolved, truncated=truncated)

    if status == EXIT_CANNOT_ANSWER:
        print(
            "merge-ready: CANNOT ANSWER — more than 100 review threads and "
            "pagination is not implemented. Refusing rather than reporting a "
            "count it cannot stand behind.",
            file=sys.stderr,
        )
        return status

    total = len(payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"])
    print(format_report(unresolved, total))
    return status


if __name__ == "__main__":
    sys.exit(main())
