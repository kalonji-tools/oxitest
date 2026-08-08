#!/usr/bin/env python3
"""Check that every `* (required)` rollup reads the results it waits on.

A rollup job holds one invariant in two literals that must agree: its `needs:`
list, and the results its steps inspect. Add a job to `needs:` and forget the
check, and it is waited on but never checked — a silent hole (#1944, #1974 §2).

Only the declared-but-unreferenced direction is checked here. The opposite
direction — referenced but undeclared — is owned by actionlint, which types the
`needs` context *from* the `needs:` list. Both were mutation-tested against
`.github/workflows/test.yml`: dropping a job from the allowlist loop SURVIVED
actionlint, and adding `needs.no-such-job` was KILLED by it. One invariant, two
directions, one tool each.

Scoped to jobs whose `name` matches `* (required)`. A `needs:` that exists purely
for ordering is normal — `docs.yml`'s `deploy` has `needs: build` and references
nothing, which is correct. A wider rule fires on correct code, gets suppressed,
and the gate dies.

Exits 0 if every rollup agrees, 1 with a report naming each unreferenced job.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent

# Rollups are identified by display name, not job id: the job is called
# `required` in all three workflows, but only the `name:` is what branch
# protection matches on.
REQUIRED_JOB_NAME = "* (required)"

# `needs.<job>` — GitHub job ids allow alphanumerics, `-` and `_`.
_NEEDS_REF = re.compile(r"needs\.([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class Violation:
    """One rollup that waits on jobs it never inspects."""

    path: Path
    job_id: str
    job_name: str
    unreferenced: tuple[str, ...]


def load_workflow_text(text: str) -> dict[str, Any]:
    """Parse workflow YAML.

    Separate from file IO so tests can pass a literal. A workflow that does not
    parse to a mapping yields an empty one rather than raising — `check-yaml`
    already gates syntax, and this checker should not be the thing that reports
    it.
    """
    loaded = yaml.safe_load(text)
    return loaded if isinstance(loaded, dict) else {}


def declared_needs(job: dict[str, Any]) -> set[str]:
    """The job ids in `needs:`, normalised across YAML's spellings.

    `needs:` may be a bare scalar, a flow sequence or a block sequence. Only
    flow style appears in this repo today; an unrecognised form must not
    silently resolve to "declares nothing", which would exempt the job from
    the check entirely.
    """
    needs = job.get("needs")
    if needs is None:
        return set()
    if isinstance(needs, str):
        return {needs}
    return {str(item) for item in needs}


def referenced_needs(job: dict[str, Any]) -> set[str]:
    """Job ids reached by a `needs.<id>` expression anywhere in the job.

    Computed over the whole job mapping *minus* its `needs:` key, not over
    `run:` bodies alone: `test.yml` references through a `for` loop,
    `quality.yml` through a `case`, and a job-level `if:` is equally valid.

    Excluding the `needs:` key is required — otherwise every job trivially
    references its own dependencies and the check could never fire.
    """
    body = {key: value for key, value in job.items() if key != "needs"}
    return set(_NEEDS_REF.findall(yaml.safe_dump(body, default_flow_style=False)))


def check_workflow(workflow: dict[str, Any], path: Path) -> list[Violation]:
    """Every `* (required)` job in one workflow that fails the invariant."""
    violations: list[Violation] = []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return violations

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        name = str(job.get("name", ""))
        if not fnmatch.fnmatch(name, REQUIRED_JOB_NAME):
            continue
        missing = declared_needs(job) - referenced_needs(job)
        if missing:
            violations.append(
                Violation(
                    path=path,
                    job_id=str(job_id),
                    job_name=name,
                    unreferenced=tuple(sorted(missing)),
                )
            )
    return violations


def report(violations: list[Violation], root: Path) -> None:
    """Print each unreferenced dependency and what to do about it."""
    print("Rollup jobs wait on jobs they never check:\n")
    for violation in violations:
        relative = violation.path.relative_to(root)
        print(f"  {relative}: job `{violation.job_id}` ({violation.job_name})")
        for job_id in violation.unreferenced:
            print(f"    - `{job_id}` is in `needs:` but no step reads it")
    print(
        "\nEvery job in a rollup's `needs:` must be referenced by some step of "
        "that rollup.\nA job waited on but never checked passes the gate having "
        "tested nothing (#1944)."
    )


def main() -> int:
    """Check every workflow under `<root>/.github/workflows/`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to scan (defaults to this script's own repo)",
    )
    args = parser.parse_args()

    workflow_dir = args.root / ".github" / "workflows"
    violations: list[Violation] = []
    for path in sorted(workflow_dir.glob("*.y*ml")):
        violations.extend(check_workflow(load_workflow_text(path.read_text()), path))

    if not violations:
        return 0

    report(violations, args.root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
