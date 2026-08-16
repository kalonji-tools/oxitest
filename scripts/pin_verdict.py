#!/usr/bin/env python3
"""Report a pull request's required CI contexts, pinned to a locally-resolved SHA.

The merge sequence has always said to pin an asynchronously-fetched verdict to
its subject before reading it. It named ``gh pr view "$PR" --json headRefOid``
as the way to resolve that subject. **That instrument is itself stale.**

Measured on #2139 on 2026-08-16, reading every instrument in one block
immediately after a force-push that moved the head from ``a9c94125`` to
``204df0ff``::

    0s  pr_view=a9c94125  api=a9c94125  rev-parse=204df0ff  ls-remote=204df0ff
    6s  pr_view=204df0ff  api=204df0ff  rev-parse=204df0ff  ls-remote=204df0ff

Both GitHub API instruments served the **previous** head. Both local-git
instruments were correct. The sampling interval was 6 s, so the stale window is
bounded as ``(0, 6]`` and its exact width is not known — nor does it matter. The
design turns on a window existing at all, because that is long enough for an
agent to read a complete green tally belonging to a commit it did not push
(WF-113, 21 occurrences across E37 to E78).

The branch *name* is read from the API and that is safe: a force-push rewrites
the commits under a ref, it does not rename the ref. Confirmed in the same
probe, correct at every sample including 0 s.

Why this is a command rather than a clearer sentence: the finding is past the
workflow-evals three-asks threshold, which refuses a fourth prose remedy for a
finding that recurs. Every recorded instance was produced by an agent that had
the clause in context (#2137).

Required contexts are read from branch protection at run time. A list here
would be a second copy of a fact the platform owns, and the two would disagree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

EXIT_PASSING = 0
EXIT_FAILING = 1
EXIT_CANNOT_ANSWER = 2

# A required context that does not exist yet is the failure mode this whole
# script is for: an empty rollup reads as "nothing pending". `Tests (required)`
# is a roll-up that does not appear until the Python matrix and Rust tests
# finish, so "every check I can see is green" is a false green early on.
ABSENT = "absent"
PENDING = "pending"
FAILING = "failing"
PASSING = "passing"

# GitHub reports a check run as queued/in_progress/completed, and only a
# completed run carries a conclusion. Anything that concluded without success —
# failure, timed_out, cancelled, action_required — is a failure for our purpose.
_SUCCESS_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


@dataclass(frozen=True, slots=True)
class Verdict:
    """One required context, resolved against one commit at one time."""

    context: str
    state: str


def classify(required: list[str], check_runs: list[dict]) -> list[Verdict]:
    """Resolve each required context against a check-runs payload.

    Pure, so every case is testable without touching the network. The payload
    is whatever ``/commits/{sha}/check-runs`` returned; ``required`` is
    whatever branch protection declared.

    When a context name appears more than once — a re-run leaves both — the
    **last** entry wins, because that is the one the platform reports.
    """
    by_name: dict[str, dict] = {}
    for run in check_runs:
        name = run.get("name")
        if name is not None:
            by_name[name] = run

    verdicts = []
    for context in required:
        run = by_name.get(context)
        if run is None:
            verdicts.append(Verdict(context, ABSENT))
        elif run.get("status") != "completed":
            verdicts.append(Verdict(context, PENDING))
        elif run.get("conclusion") in _SUCCESS_CONCLUSIONS:
            verdicts.append(Verdict(context, PASSING))
        else:
            verdicts.append(Verdict(context, FAILING))
    return verdicts


def exit_code(verdicts: list[Verdict]) -> int:
    """Any failure outranks any gap; a gap outranks success.

    Reporting `0` while a required context is absent is the defect, so an
    incomplete answer can never exit `0`.
    """
    if any(v.state == FAILING for v in verdicts):
        return EXIT_FAILING
    if any(v.state in (ABSENT, PENDING) for v in verdicts):
        return EXIT_CANNOT_ANSWER
    return EXIT_PASSING


def _run(args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    return completed.returncode, completed.stdout.strip()


def _gh_json(args: list[str]) -> object:
    """One parsed JSON document from `gh`, or None if it did not produce one."""
    code, out = _run(["gh", *args])
    if code != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def resolve_sha(
    pull_request: str,
    gh_json: Callable[[list[str]], object] | None = None,
    run: Callable[[list[str]], tuple[int, str]] | None = None,
) -> tuple[str | None, str]:
    """Resolve the head SHA through local git, never through the API.

    Returns ``(sha, branch)``. ``sha`` is ``None`` when the remote branch does
    not resolve — after a merge with ``--delete-branch``, or any deletion.

    **An absent branch is refused, never worked around.** Falling back to a
    local ref or to the pulls API would reintroduce exactly the staleness this
    script exists to remove, and it would do so silently. The two callables are
    injected so that refusal is testable without a network.
    """
    fetch_json = gh_json or _gh_json
    invoke = run or _run

    payload = fetch_json(["pr", "view", pull_request, "--json", "headRefName"])
    branch = _field(payload, "headRefName")
    if not isinstance(branch, str) or not branch:
        return None, ""

    # `ls-remote` asks the remote directly and needs no local fetch, so it
    # cannot answer from a stale object store.
    code, out = invoke(["git", "ls-remote", "origin", f"refs/heads/{branch}"])
    if code != 0 or not out:
        return None, branch
    return out.split()[0], branch


def _field(payload: object, key: str) -> object:
    """One key from a decoded JSON object. `{owner}`/`{repo}` stay gh's job."""
    if not isinstance(payload, dict):
        return None
    # `isinstance` narrows to `dict[Unknown, Unknown]`, so ty infers the key
    # type as `Never`. A JSON object always has string keys, so the cast states
    # a fact the decoder guarantees rather than suppressing the check.
    return cast("dict[str, object]", payload).get(key)


def required_contexts() -> list[str]:
    """The contexts branch protection declares. Never a list held here."""
    payload = _gh_json(["api", "repos/{owner}/{repo}/branches/main/protection"])
    contexts = _field(_field(payload, "required_status_checks"), "contexts")
    if not isinstance(contexts, list):
        return []
    return [name for name in contexts if isinstance(name, str)]


def check_runs(
    sha: str,
    gh_json: Callable[[list[str]], object] | None = None,
) -> list[dict]:
    """Every check run GitHub holds against one commit, across every page.

    `--slurp` is load-bearing. Without it `--paginate` emits one JSON document
    per page and `json.loads` raises on the second — measured with
    `per_page=5` against 17 runs. That failure direction is safe, because an
    unparsable payload reports every context `absent`, but it is still wrong.
    """
    fetch_json = gh_json or _gh_json
    pages = fetch_json(
        [
            "api",
            f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs",
            "--paginate",
            "--slurp",
        ]
    )
    if not isinstance(pages, list):
        return []
    runs: list[dict] = []
    for page in pages:
        found = _field(page, "check_runs")
        if isinstance(found, list):
            runs.extend(run for run in found if isinstance(run, dict))
    return runs


def main() -> int:
    """Report each required context, pinned to a locally-resolved head."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pull_request", help="pull request number")
    args = parser.parse_args()

    sha, branch = resolve_sha(args.pull_request)
    read_at = datetime.now(UTC).isoformat(timespec="seconds")
    if sha is None:
        detail = (
            f"branch {branch!r} is absent from the remote"
            if branch
            else "pull request not found"
        )
        print(f"CANNOT ANSWER — {detail}")
        print(f"read_at: {read_at}")
        return EXIT_CANNOT_ANSWER

    required = required_contexts()
    if not required:
        print("CANNOT ANSWER — main declares no required contexts")
        print(f"read_at: {read_at}")
        return EXIT_CANNOT_ANSWER

    verdicts = classify(required, check_runs(sha))
    width = max(len(v.context) for v in verdicts)
    for verdict in verdicts:
        print(f"  {verdict.context:<{width}}  {verdict.state}")
    # A verdict is a subject and a time, never a bare identifier. A `gh run
    # list` conclusion has been observed changing from failure to success under
    # one id after a job re-run, which retroactively falsified a claim made
    # from the earlier read (E60).
    print(f"\nsha:     {sha}  ({branch})")
    print(f"read_at: {read_at}")
    return exit_code(verdicts)


if __name__ == "__main__":
    raise SystemExit(main())
