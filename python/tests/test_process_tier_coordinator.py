"""The coordinator is a process too: `<= 1 + N`, not `N` (#1777).

Slice 4's acceptance project covers the worker half. It cannot cover this one:
it has no ``@oxi.mark.inprocess`` test, so the coordinator never resolves the
fixture at all, and a coordinator that rebuilt it once per phase would go
unnoticed there.

The coordinator runs several phases per run — the inprocess one, then each
arranged bucket, then the serial or parallel remainder. ``execute_groups`` is
called once per phase, so draining the process tier inside it fires once per
phase. Two coordinator phases that both resolve a ``lifetime="session"``
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

import os
from dataclasses import dataclass
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "process_tier_coordinator"
_TOTAL_TESTS = 3


@dataclass(frozen=True)
class _Run:
    """One run of the data project, with the event log it wrote."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    @property
    def setups(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("SETUP "))

    @property
    def setup_pids(self) -> list[str]:
        """The PID of every SETUP, duplicates kept — that is the bug shape."""
        return [e.split()[1].split("-")[0] for e in self.setups]

    @property
    def uses(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("USE "))

    @property
    def running_pids(self) -> set[str]:
        """Every PID that actually ran a test. ``USE <label> <pid> <id>``."""
        return {e.split()[2] for e in self.uses}


def _run_project(tmp: TempDir) -> _Run:
    """Run the coordinator data project with a fresh log file."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "PROC_COORD_LOG": str(log)}
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT, "-n", "2", env=env)
    events = tuple(log.read_text().splitlines()) if log.exists() else ()
    return _Run(stdout=stdout, stderr=stderr, rc=rc, events=events)


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
