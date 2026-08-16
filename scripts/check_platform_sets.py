#!/usr/bin/env python3
"""Check that the three platform declarations agree with each other.

Three files each encode oxitest's platform set and nothing compared them, so
they disagreed for the project's whole life: `classifiers` was absent entirely,
`publish.yml` shipped three targets, and `test.yml` tested one (#1946).

ADR-0013 settles the direction. `supported(X)` means CI runs the full suite on
X; wheel targets and `classifiers` are derived from that set, never the reverse.
This checker holds the derivation.

The three files share no vocabulary -- `test.yml` names runner jobs,
`publish.yml` names maturin targets, and trove classifiers carry no
architecture axis at all -- so PLATFORMS below declares one canonical (os, arch)
identity per platform and the mapping each file takes into it. That table is
ADR-0013 Rule 3 in machine form.

Four checks, in the order they are reported:

1. Nothing undeclared. Every job in the required rollup, every wheel target and
   every OS classifier is accounted for by PLATFORMS or by NON_PLATFORM_JOBS.
   This runs first because the other three read the table, and a file entry the
   table cannot see is invisible to a set comparison rather than caught by it.
2. Ship what you test. The canonical set from `test.yml` equals the canonical
   set from `publish.yml`.
3. Promise what you test. The OS projection of the tested set equals the
   classifier set.
4. Vacuity. All three derived sets are non-empty, and every allowlist entry
   really appears in the rollup. A parser that silently returns nothing passes
   a set comparison while guarding nothing, and a standing exemption for a job
   that no longer exists is the same hazard from the other side.

Output is pure ASCII. `prek` runs this as a child with stdout piped, so the
locale codec encodes what it prints -- one em dash sank PR #2019's Windows job.

Exits 0 when the three declarations agree, 1 with a report naming each
disagreement.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent

ADR = "docs/adr/0013-platform-support-is-what-ci-tests.md"

# Rollups are identified by display name, not job id: the job is called
# `required` in every workflow, and only the `name:` is what branch protection
# matches on.
REQUIRED_JOB_SUFFIX = " (required)"

# Only this action produces a wheel, so only its `target:` is a wheel target.
# The sdist job uses the same action with `command: sdist` and no target.
MATURIN_ACTION = "PyO3/maturin-action"


@dataclass(frozen=True)
class Platform:
    """One canonical platform, and how each of the three files spells it."""

    canonical: str
    # Every job that must be in the required rollup for the suite to count as
    # run here. Linux x86_64 takes two because its Rust and Python halves are
    # separate jobs; every other platform runs both through one composite.
    test_jobs: tuple[str, ...]
    publish_target: str
    classifier: str


# ADR-0013 Rule 3. Changing this table is changing the decision.
PLATFORMS = (
    Platform(
        canonical="linux-x86_64",
        test_jobs=("rust-tests", "python-tests"),
        publish_target="x86_64",
        classifier="Operating System :: POSIX :: Linux",
    ),
    Platform(
        canonical="linux-aarch64",
        test_jobs=("linux-arm",),
        publish_target="aarch64",
        classifier="Operating System :: POSIX :: Linux",
    ),
    # One wheel, two canonical platforms: `universal2` carries both slices, so
    # the shipped set is a many-to-one image of the target tokens.
    Platform(
        canonical="macos-arm64",
        test_jobs=("macos-arm",),
        publish_target="universal2-apple-darwin",
        classifier="Operating System :: MacOS :: MacOS X",
    ),
    Platform(
        canonical="macos-x86_64",
        test_jobs=("macos-intel",),
        publish_target="universal2-apple-darwin",
        classifier="Operating System :: MacOS :: MacOS X",
    ),
    Platform(
        canonical="windows-x86_64",
        test_jobs=("windows",),
        publish_target="x86_64-pc-windows-msvc",
        classifier="Operating System :: Microsoft :: Windows",
    ),
)

# Jobs in the required rollup that confer no platform support.
#
# `changes` is the paths filter that decides what runs at all. `tmpdir-symlink`
# runs on ubuntu-latest with TMPDIR pointed at a symlink -- a configuration
# variant, as the comment above that job says, not a platform.
#
# `wheel-manifest` builds one wheel on ubuntu-latest and asserts the two
# source-controlled properties of the Distribution band (#2177). It confers no
# platform support: it covers what the source puts in a wheel, not where the
# wheel runs.
NON_PLATFORM_JOBS = frozenset({"changes", "tmpdir-symlink", "wheel-manifest"})

# Only this prefix is compared. A classifier about licences or topics says
# nothing about platforms and must not be dragged into the comparison.
OS_CLASSIFIER_PREFIX = "Operating System :: "


def load_yaml(text: str) -> dict[str, Any]:
    """Parse workflow YAML, yielding an empty mapping for anything else.

    Separate from file IO so tests can pass a literal. `check-yaml` already
    gates syntax and this checker should not be the thing that reports it --
    but an unparsable file must not read as "declares nothing", which the
    vacuity check below turns into a failure rather than a pass.
    """
    loaded = yaml.safe_load(text)
    return loaded if isinstance(loaded, dict) else {}


def rollup_needs(workflow: dict[str, Any]) -> set[str]:
    """Job ids in the `needs:` of every `* (required)` job in one workflow.

    `needs:` may be a scalar or a sequence; both are normalised. A job id that
    reaches this set is a job the rollup waits on, which per ADR-0013 Rule 1 is
    what makes a platform job confer support.
    """
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return set()

    needs: set[str] = set()
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        if not str(job.get("name", "")).endswith(REQUIRED_JOB_SUFFIX):
            continue
        declared = job.get("needs")
        if declared is None:
            continue
        if isinstance(declared, str):
            needs.add(declared)
        else:
            needs.update(str(item) for item in declared)
    return needs


def _maturin_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The maturin steps of one job, or an empty list if it has none."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith(MATURIN_ACTION)
    ]


def _matrix_targets(job: dict[str, Any]) -> set[str]:
    """`strategy.matrix.target` entries, which a step interpolates."""
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return set()
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return set()
    return {str(entry) for entry in matrix.get("target", []) or []}


def _literal_step_targets(steps: list[dict[str, Any]]) -> set[str]:
    """`with.target` values that are literals rather than interpolations.

    An interpolation is skipped: resolving `${{ matrix.target }}` by hand means
    reimplementing expression evaluation to learn what the matrix already
    states, and admitting the expression itself would put a token in the
    shipped set that matches no platform.
    """
    targets: set[str] = set()
    for step in steps:
        with_block = step.get("with")
        if not isinstance(with_block, dict):
            continue
        target = with_block.get("target")
        if isinstance(target, str) and "${{" not in target:
            targets.add(target)
    return targets


def wheel_targets(workflow: dict[str, Any]) -> set[str]:
    """Literal maturin target tokens in one workflow.

    Two spellings reach a maturin step: a literal in the step's `with.target`,
    and a matrix entry the step interpolates. Both are read. A job with no
    maturin step ships no wheel and contributes nothing -- the sdist job runs
    the same action with `command: sdist` and no target at all.
    """
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return set()

    targets: set[str] = set()
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = _maturin_steps(job)
        if not steps:
            continue
        targets |= _matrix_targets(job)
        targets |= _literal_step_targets(steps)
    return targets


def os_classifiers(pyproject: dict[str, Any]) -> set[str]:
    """`Operating System ::` classifiers declared in `[project]`."""
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return set()
    declared = project.get("classifiers")
    if not isinstance(declared, list):
        return set()
    return {
        text
        for entry in declared
        if (text := str(entry)).startswith(OS_CLASSIFIER_PREFIX)
    }


def _undeclared(needs: set[str], targets: set[str], classifiers: set[str]) -> list[str]:
    """Check 1 -- entries in the three files that PLATFORMS cannot see.

    Runs before every comparison below it. An entry the table does not know is
    invisible to a set comparison rather than caught by one: add a wheel target
    with no PLATFORMS row and both derived sets simply omit it, so they stay
    equal and the gate passes on the drift it exists to find.
    """
    known_jobs = {job for platform in PLATFORMS for job in platform.test_jobs}
    known_targets = {platform.publish_target for platform in PLATFORMS}
    known_classifiers = {platform.classifier for platform in PLATFORMS}

    problems = [
        f"test.yml: job `{job}` is in the required rollup but is not in "
        f"PLATFORMS or NON_PLATFORM_JOBS. A job that gates every pull request "
        f"either supports a platform or does not; say which."
        for job in sorted(needs - known_jobs - NON_PLATFORM_JOBS)
    ]
    problems.extend(
        f"publish.yml: wheel target `{target}` is not in PLATFORMS. Either "
        f"that platform gets a test job, or the target goes away."
        for target in sorted(targets - known_targets)
    )
    problems.extend(
        f"pyproject.toml: classifier `{classifier}` is not in PLATFORMS. "
        f"Classifiers are derived from the tested set, not chosen."
        for classifier in sorted(classifiers - known_classifiers)
    )
    return problems


def _vacuity(
    needs: set[str], tested: set[str], shipped: set[str], classifiers: set[str]
) -> list[str]:
    """Check 4 -- a set that is empty makes every comparison hold for free.

    Both directions. An empty derived set is a parser that read nothing; a
    NON_PLATFORM_JOBS entry that matches no job is a standing exemption nobody
    re-reads. Neither is caught by comparing the sets.
    """
    problems: list[str] = []
    if not tested:
        problems.append(
            "test.yml: no platform job was found in a `* (required)` rollup. "
            "Every comparison would hold vacuously and guard nothing."
        )
    if not shipped:
        problems.append(
            "publish.yml: no wheel target was found. Every comparison would "
            "hold vacuously and guard nothing."
        )
    if not classifiers:
        problems.append(
            "pyproject.toml: no `Operating System ::` classifier was found. "
            "Every comparison would hold vacuously and guard nothing."
        )
    problems.extend(
        f"NON_PLATFORM_JOBS exempts `{job}`, which is not in any required "
        f"rollup. A standing exemption for a job that does not exist is an "
        f"exemption nobody re-reads."
        for job in sorted(NON_PLATFORM_JOBS - needs)
    )
    return problems


def _ship_what_you_test(tested: set[str], shipped: set[str]) -> list[str]:
    """Check 2 -- the tested set and the shipped set are the same platforms."""
    problems = [
        f"{canonical} ships a wheel but no required job tests it. Either add "
        f"the job, or drop the wheel target."
        for canonical in sorted(shipped - tested)
    ]
    problems.extend(
        f"{canonical} is tested by a required job but ships no wheel. Wheel "
        f"targets are derived from the tested set."
        for canonical in sorted(tested - shipped)
    )
    return problems


def _promise_what_you_test(promised: set[str], classifiers: set[str]) -> list[str]:
    """Check 3 -- the classifiers are the OS projection of the tested set."""
    problems = [
        f"pyproject.toml: `{classifier}` is missing. A tested platform is "
        f"promised in `classifiers`."
        for classifier in sorted(promised - classifiers)
    ]
    problems.extend(
        f"pyproject.toml: `{classifier}` promises a platform no required job tests."
        for classifier in sorted(classifiers - promised)
    )
    return problems


def check(needs: set[str], targets: set[str], classifiers: set[str]) -> list[str]:
    """Every disagreement between the three declarations, in report order."""
    tested = {
        platform.canonical for platform in PLATFORMS if set(platform.test_jobs) <= needs
    }
    shipped = {
        platform.canonical
        for platform in PLATFORMS
        if platform.publish_target in targets
    }
    promised = {
        platform.classifier for platform in PLATFORMS if platform.canonical in tested
    }

    return [
        *_undeclared(needs, targets, classifiers),
        *_vacuity(needs, tested, shipped, classifiers),
        *_ship_what_you_test(tested, shipped),
        *_promise_what_you_test(promised, classifiers),
    ]


def main() -> int:
    """Compare the three platform declarations under `<root>`."""
    parser = argparse.ArgumentParser(
        description="Check the platform declarations agree."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to check (defaults to this script's own repo)",
    )
    args = parser.parse_args()

    workflows = args.root / ".github" / "workflows"
    needs = rollup_needs(
        load_yaml((workflows / "test.yml").read_text(encoding="utf-8"))
    )
    targets = wheel_targets(
        load_yaml((workflows / "publish.yml").read_text(encoding="utf-8"))
    )
    classifiers = os_classifiers(
        tomllib.loads((args.root / "pyproject.toml").read_text(encoding="utf-8"))
    )

    problems = check(needs, targets, classifiers)
    if not problems:
        return 0

    print("The three platform declarations disagree:\n")
    for problem in problems:
        print(f"  - {problem}")
    print(
        f"\nThis is a decision, not a typo. {ADR} settles the direction: a "
        f"platform is supported when CI runs the full suite on it, and wheel "
        f"targets and classifiers are derived from that set. Whichever file "
        f"you just changed, the question is whether that platform is supported."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
