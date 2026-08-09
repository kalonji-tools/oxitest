#!/usr/bin/env python3
"""Record a stage-8 finding's disposition and, for a fix, resolve its thread.

The six verbs — Fixed, Refuted, Superseded, Accepted, Deferred, No change — are
the vocabulary already in use on this repo's pull requests. GitHub's resolve bit
is binary and orthogonal to them, so the verb goes in the reply and the bit
closes the thread (#2007).

Resolution is split, and the split is enforced here rather than asked for. Only
``Fixed`` is resolvable by the agent, because only ``Fixed`` is verifiable from
the diff. Every other verb posts its reply and then refuses, leaving the Resolve
button to the maintainer. That guard exists as code rather than as a sentence in
CLAUDE.md because a sentence is what the previous arrangement already was: the
disposition obligation was prose, and a finding still went missing between a
pass and its table (WF-079).

Known limit, by design: this proves a disposition was *recorded*, not that it is
*correct*. A reply reading "Fixed — done" resolves a thread exactly as well as
one carrying evidence. The gate counts dispositions; a reader judges them.

Exits 0 when the disposition is recorded, 1 when the marker matches no thread on
the pull request, and 2 when it cannot reach the pull request at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

EXIT_OK = 0
EXIT_NO_SUCH_FINDING = 1
EXIT_CANNOT_ANSWER = 2

VERBS = ("Fixed", "Refuted", "Superseded", "Accepted", "Deferred", "No change")

# Only a fix is verifiable from the diff. Everything else is a judgement about
# whether a finding should be acted on, and the same actor must not raise,
# answer and close it.
AGENT_RESOLVABLE = frozenset({"Fixed"})

# The thread id is a GitHub-issued node id, not user input, so interpolating it
# would not be exploitable — but the query below already demonstrates the safe
# form, and the one place that abandons it is the one a future reader copies.
_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) { thread { isResolved } }
}
"""

_THREAD_QUERY = """
query($owner: String!, $name: String!, $pr: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes { id isResolved comments(first: 1) { nodes { databaseId body } } }
      }
    }
  }
}
"""


def agent_may_resolve(verb: str) -> bool:
    """Whether the agent may close this thread itself.

    Only ``Fixed``. A refuted, deferred or accepted finding is a judgement, and
    its Resolve button belongs to the maintainer — that is the whole point of
    moving review onto GitHub rather than into a table the agent writes.
    """
    return verb in AGENT_RESOLVABLE


def reply_body(verb: str, reason: str) -> str:
    """Render the closing reply, verb first so it is greppable."""
    return f"**{verb}** — {reason}"


def find_thread(nodes: list[dict], slug: str, finding_id: int) -> dict | None:
    """Locate a thread by its namespaced marker, or ``None`` if absent.

    Matching on the number alone would collide across passes, because each pass
    numbers its findings from one.
    """
    wanted = f"**{slug} #{finding_id}**"
    for node in nodes:
        comments = node["comments"]["nodes"]
        if comments and comments[0]["body"].startswith(wanted):
            return node
    return None


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


def main() -> int:
    """Post a finding's disposition, resolving the thread only for a fix."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="pass slug, e.g. ponytail or improve")
    parser.add_argument(
        "finding_id", type=int, help="the finding's number within that pass"
    )
    parser.add_argument("verb", choices=VERBS, help="the disposition")
    parser.add_argument("reason", help="why — this is the durable half")
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number (default: infer from the current branch)",
    )
    args = parser.parse_args()

    try:
        repo = json.loads(_gh(["repo", "view", "--json", "nameWithOwner"]))[
            "nameWithOwner"
        ]
        owner, name = repo.split("/", 1)
        pr = (
            args.pr
            if args.pr is not None
            else json.loads(_gh(["pr", "view", "--json", "number"]))["number"]
        )
    except (RuntimeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"review-dispose: CANNOT ANSWER — no pull request for this branch ({exc})",
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
    nodes = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    thread = find_thread(nodes, args.slug, args.finding_id)
    if thread is None:
        print(
            f"review-dispose: no thread on PR #{pr} carries the marker "
            f"'{args.slug} #{args.finding_id}'",
            file=sys.stderr,
        )
        return EXIT_NO_SUCH_FINDING

    comment_id = thread["comments"]["nodes"][0]["databaseId"]
    _gh(
        [
            "api",
            f"repos/{repo}/pulls/{pr}/comments/{comment_id}/replies",
            "-X",
            "POST",
            "-f",
            f"body={reply_body(args.verb, args.reason)}",
        ]
    )
    print(f"disposition recorded: {args.slug} #{args.finding_id} — {args.verb}")

    if not agent_may_resolve(args.verb):
        print(
            f"not resolving: '{args.verb}' is a judgement call, so the Resolve "
            f"button is the maintainer's."
        )
        return EXIT_OK

    _gh(
        [
            "api",
            "graphql",
            "-F",
            f"threadId={thread['id']}",
            "-f",
            f"query={_RESOLVE_MUTATION}",
        ]
    )
    print("thread resolved")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
