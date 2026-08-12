"""Acceptance: a declaring package subtree never spans two dispatch phases (#2058).

Each dispatch phase owns its own fixture session, so a subtree spread across two
of them builds its ``lifetime="package"`` fixture once in each and the tier's
exactly-once promise silently stops holding. Two independent routes could split
a subtree, and this module has one project per route, one for nested anchors,
and one async twin — async being the dimension every arm that established the
rules held constant.

**These tests count builds, not outcomes.** An exit code cannot see a double
build — before #2058 the split was refused at collection, and after it the run
is green either way. A summary count cannot see it either, and is worse than
useless here: a test that emits a warning is tallied under ``warning`` rather
than ``passed``, so an unrelated warning moves the number a build-count
assertion would never notice.

Every project sets ``min_parallel_tests = 1``. The serial answer was already
correct, so a run that quietly fell back to serial would pass vacuously.

**The rules' *scoping* is pinned in Rust, not here.** That a declaring package
with no mark and no arrangement keeps its worker is what bounds the cost of both
rules, and no exactly-once assertion can detect its loss — a suite that
serialised every declaring package onto the runner would pass every test in this
module. ``test_a_declaring_package_subtree_without_a_mark_keeps_its_parallelism``
in ``src/pipeline/arrange.rs`` is the assertion that can fail.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests.helpers.event_logs import run_with_event_log

_DATA_ROOT = Path(__file__).parent / "data"


def test_an_inprocess_mark_does_not_split_a_declaring_package_subtree(
    tmp: TempDir,
) -> None:
    """Route 1 — the mark sends items to the coordinator, siblings to a worker.

    The marked test and its unmarked sibling live in one declaring package.
    Before #2058 this combination was refused at collection precisely because
    honouring it built the fixture twice; the rule now keeps the subtree whole
    instead, so the combination runs and stays exactly-once.
    """
    # Arrange / Act
    run = run_with_event_log(
        _DATA_ROOT / "slice3_inprocess_inside_package",
        tmp,
        "SLICE3_LOG",
        "-n",
        "2",
    )

    # Assert
    assert run.rc == 0, (
        f"the combination must now run rather than be refused; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.setups) == 1, (
        f"the package fixture must be built exactly once across the whole run — "
        f"two SETUPs means the subtree reached two dispatch phases, which is the "
        f"defect the tier exists to prevent. setups={run.setups}"
    )
    assert len(run.running_pids) == 1, (
        f"both tests must run in one process; two PIDs means the subtree split "
        f"even though only one instance happened to be built. uses={run.uses}"
    )


def test_arrangement_does_not_split_a_declaring_package_subtree(tmp: TempDir) -> None:
    """Route 2 — no mark is involved, which is why route 1 does not cover it.

    ``@oxi.arrange`` on one module of a declaring package used to leave its
    unarranged siblings in the parallel remainder. The subtree now travels whole
    into the component instead of being excluded from arrangement, which also
    restores the co-location ``@oxi.arrange`` asked for.
    """
    # Arrange / Act
    run = run_with_event_log(
        _DATA_ROOT / "package_subtree_arranged",
        tmp,
        "SUBTREE_ARRANGED_LOG",
        "-n",
        "2",
    )

    # Assert
    assert run.rc == 0, (
        f"the project must run green; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.setups) == 1, (
        f"arrangement must not split the subtree — this route needs no mark, so "
        f"the inprocess rule alone leaves it double-building. setups={run.setups}"
    )
    assert len(run.running_pids) == 1, (
        f"the arranged module and its unarranged sibling must share one process. "
        f"uses={run.uses}"
    )


def test_the_outermost_anchor_claims_a_nested_declaring_subtree(tmp: TempDir) -> None:
    """A mark in the inner package must move the outer package's modules too.

    Honouring the inner anchor alone would split the outer subtree across two
    phases and rebuild its value — the duplicate the tier exists to prevent, and
    the rule ``group_by_package`` already follows when it merges.
    """
    # Arrange / Act
    run = run_with_event_log(
        _DATA_ROOT / "package_subtree_nested",
        tmp,
        "SUBTREE_NESTED_LOG",
        "-n",
        "2",
    )

    # Assert
    assert run.rc == 0, (
        f"the project must run green; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    outer_setups = run.lines("SETUP outer-")
    inner_setups = run.lines("SETUP inner-")
    assert len(outer_setups) == 1, (
        f"the outer fixture must be built once — a second build means the inner "
        f"anchor was honoured alone and split its ancestor. setups={outer_setups}"
    )
    assert len(inner_setups) == 1, (
        f"the inner fixture must be built once. setups={inner_setups}"
    )
    assert len(run.running_pids) == 1, (
        f"every module of both packages must share one process. uses={run.uses}"
    )


def test_an_async_package_fixture_is_built_once_across_the_subtree(
    tmp: TempDir,
) -> None:
    """The async twin of route 1.

    The rules partition ``ModuleGroup``s and never touch fixture construction,
    so an async fixture should behave exactly like a sync one. Every arm that
    established the rules built a sync fixture, so that is an argument rather
    than a measurement. This is the measurement.
    """
    # Arrange / Act
    run = run_with_event_log(
        _DATA_ROOT / "package_subtree_async",
        tmp,
        "SUBTREE_ASYNC_LOG",
        "-n",
        "2",
    )

    # Assert
    assert run.rc == 0, (
        f"the async project must run green; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.setups) == 1, (
        f"an async package fixture must be built exactly once across the subtree, "
        f"the same as its sync twin. setups={run.setups}"
    )
    assert len(run.running_pids) == 1, (
        f"both async tests must run in one process. uses={run.uses}"
    )
