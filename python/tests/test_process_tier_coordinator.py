"""The coordinator is a process too: `<= 1 + N`, not `N` (#1777).

Slice 4's acceptance project covers the worker half. It cannot cover this one:
it has no ``@oxi.mark.inprocess`` test, so the coordinator never resolves the
fixture at all, and a coordinator that rebuilt it once per phase would go
unnoticed there.

The coordinator runs several phases per run — the inprocess one, then each
arranged bucket, then the serial or parallel remainder. ``execute_groups`` is
called once per phase, so draining the process tier inside it fires once per
phase. Two coordinator phases that both resolve a ``lifetime="process"``
fixture then get two instances, which is the coordinator's version of the
per-task-group defect the worker half fixes.

**Getting two coordinator phases is the whole trick, and it is easy to get
wrong.** A project with ``auto_arrange = false`` has exactly one — the
inprocess phase — and measures 3 SETUPs across 3 PIDs both before and after the
fix, so it proves nothing. The data project here keeps arrangement enabled and
declares a ``shared=True`` fixture precisely so the arrange stage pins those
modules to the coordinator, giving it a second phase. Measured against that
project, the drain-per-phase behaviour builds the fixture **twice in one PID**
and the fix builds it **once**.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "process_tier_coordinator"
_TOTAL_TESTS = 3


def _run_project(tmp: TempDir) -> helpers.EventLogRun:
    """Run the coordinator data project at ``-n 2`` with a fresh log file."""
    return helpers.run_with_event_log(_PROJECT, tmp, "PROC_COORD_LOG", "-n", "2")


def test_the_coordinator_builds_the_fixture_at_most_once(tmp: TempDir) -> None:
    """No PID may build the process-lifetime fixture twice — coordinator included.

    Asserting a total count would be scheduling-dependent. The invariant is
    per-PID, exactly as in the worker-half acceptance: every PID that ran a
    test built the fixture exactly once.
    """
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.uses) == _TOTAL_TESTS, (
        f"every test must observe the fixture, or the count below has nothing "
        f"to inspect; got {len(run.uses)} USE lines for {_TOTAL_TESTS} tests"
    )
    assert sorted(run.setup_pids) == sorted(run.running_pids), (
        f"SETUP fired on PIDs {sorted(run.setup_pids)} but tests ran on "
        f"{sorted(run.running_pids)}. A PID appearing twice means the fixture "
        f"was rebuilt within one process — for the coordinator that means the "
        f"process tier drained between two of its phases instead of once after "
        f"all of them"
    )


def test_the_coordinator_actually_ran_two_phases(tmp: TempDir) -> None:
    """Non-vacuity guard for the assertion above.

    If arrangement stops pinning the shared-fixture modules, or the inprocess
    mark stops routing its test to the main process, the coordinator drops to a
    single phase. Every per-phase-drain assertion is then satisfiable by the
    broken implementation too, and this file quietly stops testing anything.

    Both phases run on the coordinator, so the tell is that its PID served the
    inprocess test *and* at least one arranged test.
    """
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    marked = [e for e in run.uses if e.split()[1] == "marked"]
    assert len(marked) == 1, (
        f"the inprocess test must have run exactly once, got {marked} — "
        f"without it the coordinator never resolves the fixture at all"
    )
    coordinator_pid = marked[0].split()[2]
    coordinator_uses = [e for e in run.uses if e.split()[2] == coordinator_pid]
    assert len(coordinator_uses) > 1, (
        f"the coordinator PID {coordinator_pid} served only the inprocess test "
        f"({coordinator_uses}). It needs a second phase — an arranged bucket — "
        f"or there are no two coordinator phases to drain between, and the "
        f"sibling assertion cannot distinguish the fix from the defect"
    )


def test_a_task_scoped_fixture_is_not_reused_across_coordinator_phases(
    tmp: TempDir,
) -> None:
    """Every `shared=True` build on the coordinator is paired with a teardown.

    Not a #1777 requirement — a latent defect this branch happened to fix, kept
    here so it cannot come back.

    ``_Scope.drain()`` used to clear only the teardown stack. The coordinator
    drains ``_shared_scope`` once per *phase*, so before this branch phase 2
    took a cache hit on a value whose teardown had already run in phase 1: one
    SETUP, one TEARDOWN, and a live use in between them. Clearing the cache
    alongside the stack turned that into two properly paired build/teardown
    cycles.

    That does mean the coordinator's ``shared=True`` instance count changed
    from 1 to 2 for a two-phase run. The old number was not a contract worth
    keeping — it was a use-after-teardown.
    """
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    setups = run.lines("SHARED_SETUP ")
    teardowns = run.lines("SHARED_TEARDOWN ")
    assert len(setups) > 1, (
        f"the coordinator must have built the shared fixture more than once "
        f"({setups}) — it drains at end_task and this project gives it two "
        f"phases, so a single build means the value survived a drain and this "
        f"assertion cannot distinguish a paired cycle from a reused corpse"
    )
    assert len(setups) == len(teardowns), (
        f"built the shared fixture {len(setups)} times but tore it down "
        f"{len(teardowns)}. An unpaired build is a value that outlived its own "
        f"teardown: the cache survived the drain, so the next phase was handed "
        f"an already-disposed instance"
    )
