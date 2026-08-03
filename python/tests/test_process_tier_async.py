"""An async process-lifetime fixture lives and dies with its process (#1777).

Decision 6 moves ``SharedAsyncManager`` to the process side, and was recorded
as gated on new acceptance coverage because none existed for async at any wide
tier. This is that coverage.

The async path does not share machinery with the sync one. A sync fixture's
value and teardown both live in a ``_Scope``; an async fixture's value lives in
the scope but its **pending generator** lives in ``SharedAsyncManager``, keyed
by a boundary string, and is resumed on the manager's event loop. Two separate
places therefore had to learn about the process tier, and missing either one
reproduces the same symptom:

- ``register_teardown`` sent every non-module fixture to ``SESSION_BOUNDARY``
- ``resolve`` — the route a wide async fixture actually takes — registered with
  no boundary at all, so fixing only the first changed nothing

Measured before the fix, one worker draining two task groups gave::

    SETUP 4105835-1
    USE b     4105835   <- task group 1
    TEARDOWN 4105835-1  <- disposed at the task boundary
    USE c     4105835   <- reused after teardown
    USE d     4105835
    (never torn down again)

That is a use-after-teardown followed by a leak, and nothing reported either.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "process_tier_async"
_TOTAL_TESTS = 4


def _uses_after_teardown(run: helpers.EventLogRun) -> list[str]:
    """USE lines for an instance whose TEARDOWN already appeared.

    The ordering check, and the reason this file cannot assert on counts alone:
    before the fix the fixture was still built exactly once per process. It was
    *disposed* early and handed out again.
    """
    seen: set[str] = set()
    offenders: list[str] = []
    for event in run.events:
        if event.startswith("TEARDOWN "):
            seen.add(event.split()[1])
        elif event.startswith("USE ") and event.split()[3] in seen:
            offenders.append(event)
    return offenders


def _run_project(tmp: TempDir) -> helpers.EventLogRun:
    """Run the async data project at ``-n 2`` with a fresh log file."""
    return helpers.run_with_event_log(_PROJECT, tmp, "PROC_ASYNC_LOG", "-n", "2")


def test_an_async_process_fixture_is_built_once_per_process(tmp: TempDir) -> None:
    """One build per PID, exactly as the sync tier promises."""
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.uses) == _TOTAL_TESTS, (
        f"every test must observe the fixture, or the counts below have nothing "
        f"to inspect; got {len(run.uses)} USE lines for {_TOTAL_TESTS} tests"
    )
    builds_per_pid = Counter(i.split("-")[0] for i in run.setup_ids)
    rebuilt = {pid: n for pid, n in builds_per_pid.items() if n > 1}
    assert not rebuilt, (
        f"these PIDs built the async fixture more than once: {rebuilt}. The "
        f"process tier must build exactly once per process, so a repeat means "
        f"the fixture was rebuilt between task groups"
    )
    assert set(builds_per_pid) == run.running_pids, (
        f"the fixture was built on {sorted(builds_per_pid)} but tests ran on "
        f"{sorted(run.running_pids)} — a running PID with no build means a "
        f"process served tests without ever resolving the fixture"
    )


def test_an_async_process_fixture_is_never_used_after_teardown(
    tmp: TempDir,
) -> None:
    """The defect this commit fixes, asserted directly on the event order.

    A per-PID build count alone would not catch it: before the fix the fixture
    was still built exactly once per process. It was *disposed* at the first
    task boundary and then handed out again, so the ordering is the evidence.
    """
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    offenders = _uses_after_teardown(run)
    assert not offenders, (
        f"these tests received an async fixture instance that had already been "
        f"torn down: {offenders}. Its teardown fired at the task boundary "
        f"rather than the process boundary, while the cached value survived — "
        f"a use-after-teardown that raises nothing and reports nothing.\n"
        f"full log:\n" + "\n".join(run.events)
    )
    assert sorted(run.teardown_ids) == sorted(run.setup_ids), (
        f"every instance must be torn down exactly once: built {run.setup_ids}, "
        f"tore down {run.teardown_ids}. A missing teardown is the other half of "
        f"the same defect — the boundary that would have run it was drained "
        f"before the instance was registered against it"
    )
