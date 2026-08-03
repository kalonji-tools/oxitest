"""What #1777 must *not* have changed, plus serial/`-n 1` parity.

The spec's phrasing is the reason this file exists: the decision to give the
user tier its own scope "is only honest if" the two tiers left behind stay
where they were. A change that quietly hoisted `TempDirFactory` or `shared=True`
to process lifetime would satisfy every positive assertion in the branch while
making every suite hold its temp directories for the life of a worker.

All three assertions need one worker to drain **two task groups**, which is the
only place a per-task tier is distinguishable from a per-process one. Four
modules at `-n 2` forces it; the guard below fails loudly if scheduling does
something else, because every assertion here is satisfiable by the wrong
implementation when each worker gets exactly one group.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture, TempDir, TempDirFactory
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "process_tier_negatives"
_TOTAL_TESTS = 4


@oxi.fixture(lifetime="module")
def parallel_run(factory: Fixture[TempDirFactory]) -> helpers.EventLogRun:
    """One ``-n 2`` run of the negatives project, shared by the assertions below.

    Module lifetime, for two reasons. The three assertions read different
    facets of **one** run — the contrast between the two task-scoped tiers and
    the process tier is the evidence, and three separate runs could in
    principle be scheduled differently and stop contrasting anything. It also
    spends one subprocess here instead of three.

    Depending on the task-scoped ``TempDirFactory`` from a module-lifetime
    fixture is narrower-depends-on-wider, which is the legal direction.
    """
    return helpers.run_with_event_log(
        _PROJECT, factory.mktemp("negatives"), "NEGATIVES_LOG", "-n", "2"
    )


def _pids(run: helpers.EventLogRun, prefix: str, *, field: int) -> list[str]:
    """PID from each *prefix* line, duplicates kept."""
    return [e.split()[field].split("-")[0] for e in run.lines(prefix)]


def _assert_a_worker_drained_two_groups(run: helpers.EventLogRun) -> None:
    """Fail unless some process ran more than one module.

    Shared guard. Without it every assertion in this file passes trivially when
    the scheduler hands each worker a single group — which is exactly the shape
    that hid the original defect from the slice-4 acceptance test.
    """
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    factory_lines = run.lines("FACTORY ")
    assert len(factory_lines) == _TOTAL_TESTS, (
        f"every test must have recorded; got {len(factory_lines)} lines for "
        f"{_TOTAL_TESTS} tests\nstdout:\n{run.stdout}"
    )
    per_pid = Counter(e.split()[2] for e in factory_lines)
    assert any(count > 1 for count in per_pid.values()), (
        f"no worker drained more than one task group ({dict(per_pid)}). A "
        f"per-task tier and a per-process one are indistinguishable in that "
        f"shape, so nothing below can fail"
    )


def test_the_builtin_factory_is_still_rebuilt_per_task_group(
    parallel_run: Fixture[helpers.EventLogRun],
) -> None:
    """``TempDirFactory`` stays on the task boundary (decision 2).

    Hoisting it alongside the user tier would accumulate every temp directory a
    worker ever created until the process exits — the cost decision 2 declined
    to pay. ``factory.dirs`` is per-instance state, so a factory rebuilt per
    task group always reads 1 immediately after its own ``mktemp``; one shared
    across groups would climb.
    """
    # Assert
    _assert_a_worker_drained_two_groups(parallel_run)
    counts = [line.rsplit("=", 1)[1] for line in parallel_run.lines("FACTORY ")]
    assert set(counts) == {"1"}, (
        f"every test must see a factory holding exactly its own directory, got "
        f"{counts}. A count above 1 means the factory outlived a task group, so "
        f"a suite that never declared the process tier is now holding temp "
        f"directories for the life of a worker"
    )


def test_shared_true_is_still_rebuilt_per_task_group(
    parallel_run: Fixture[helpers.EventLogRun],
) -> None:
    """The legacy ``shared=True`` tier stays task-scoped (decision 8).

    Its instance count is a regression check, not a target: #1777 changed the
    tier beside it, and this is what says it did not drag this one along.
    """
    # Assert
    _assert_a_worker_drained_two_groups(parallel_run)
    builds = Counter(_pids(parallel_run, "SHARED_SETUP ", field=1))
    assert any(count > 1 for count in builds.values()), (
        f"shared=True was built {dict(builds)} — once per PID. It drains at "
        f"end_task, so a worker that handled two task groups must have built it "
        f"twice; exactly one per process means it moved to the process boundary "
        f"with the user tier, which decision 8 rejects"
    )


def test_the_process_tier_is_built_once_per_process(
    parallel_run: Fixture[helpers.EventLogRun],
) -> None:
    """The positive contrast, measured in the same run as the two negatives.

    Asserting the negatives alone would be satisfied by a build that never
    reached the process boundary at all.
    """
    # Assert
    _assert_a_worker_drained_two_groups(parallel_run)
    builds = Counter(_pids(parallel_run, "PROCESS_SETUP ", field=1))
    rebuilt = {pid: n for pid, n in builds.items() if n > 1}
    assert not rebuilt, (
        f"these PIDs built the process-lifetime fixture more than once: "
        f"{rebuilt}. In the very same run the two task-scoped tiers above were "
        f"rebuilt per group — that contrast is the whole claim"
    )
    running = {e.split()[2] for e in parallel_run.lines("FACTORY ")}
    assert set(builds) == running, (
        f"built on {sorted(builds)} but tests ran on {sorted(running)}"
    )


def test_serial_and_one_worker_agree(tmp: TempDir) -> None:
    """`--serial` and `-n 1` produce the same instance count.

    Both are a single process, so the tier must not notice which code path got
    there. The coordinator drains after all phases and a worker drains in
    ``main()``'s ``finally`` — two different implementations of one contract,
    and this is what says they agree.
    """
    # Act — distinct log names: the project's fixtures append, so one name
    # would fold the first run's events into the second's counts.
    serial = helpers.run_with_event_log(
        _PROJECT, tmp, "NEGATIVES_LOG", "--serial", log_name="serial.log"
    )
    one_worker = helpers.run_with_event_log(
        _PROJECT, tmp, "NEGATIVES_LOG", "-n", "1", log_name="one_worker.log"
    )

    # Assert
    for label, run in (("--serial", serial), ("-n 1", one_worker)):
        assert run.rc == 0, (
            f"the {label} run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
            f"stderr:\n{run.stderr}"
        )
        assert len(run.lines("FACTORY ")) == _TOTAL_TESTS, (
            f"the {label} run must execute every test; got "
            f"{len(run.lines('FACTORY '))}\nstdout:\n{run.stdout}"
        )
    serial_builds = len(serial.lines("PROCESS_SETUP "))
    one_worker_builds = len(one_worker.lines("PROCESS_SETUP "))
    assert serial_builds == one_worker_builds == 1, (
        f"one process means one instance either way: --serial built "
        f"{serial_builds}, -n 1 built {one_worker_builds}. "
        f"A difference means the coordinator's post-phase drain and the "
        f"worker's finally disagree about the boundary"
    )
