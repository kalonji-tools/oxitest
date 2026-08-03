"""Slice-4 acceptance: lifetime="process" end-to-end (#1711).

Runs oxitest as a subprocess and asserts on a log the fixture writes itself,
rather than on reporter output — the question is what the fixture actually did.

The parallel assertion is the one that matters. A serial run has a single
fixture session for everything, so a serial-only proof is equally consistent
with per-run, per-worker, and per-task semantics. That is exactly how slice 3's
package-teardown bug survived its first test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"
_PROJECT = _DATA_ROOT / "slice4_session_lifetime"
_REJECT_PROJECT = _DATA_ROOT / "slice4_session_below_root"

#: Four modules x two tests, baked into the data project.
_TOTAL_TESTS = 8


@dataclass(frozen=True)
class _Run:
    """One acceptance run: the process output plus the fixture's own log."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    @property
    def setups(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("SETUP "))

    @property
    def teardowns(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("TEARDOWN "))

    @property
    def uses(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("USE "))

    @property
    def setup_pids(self) -> list[str]:
        """The PID of every SETUP, duplicates kept — that is the bug shape."""
        return [e.split()[1].split("-")[0] for e in self.setups]

    @property
    def running_pids(self) -> set[str]:
        """Every PID that actually ran a test. ``USE <label> <pid> <id>``."""
        return {e.split()[2] for e in self.uses}

    @property
    def setup_ids(self) -> set[str]:
        return {e.split()[1] for e in self.setups}

    @property
    def teardown_ids(self) -> set[str]:
        return {e.split()[1] for e in self.teardowns}


def _run_project(tmp: TempDir, *extra_args: str) -> _Run:
    """Run the slice-4 data-project with a fresh log file."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "SLICE4_LOG": str(log)}
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT, *extra_args, env=env)
    events = tuple(log.read_text().splitlines()) if log.exists() else ()
    return _Run(stdout=stdout, stderr=stderr, rc=rc, events=events)


def test_session_fixture_is_built_once_in_a_serial_run(tmp: TempDir) -> None:
    """The serial control. Correct before this slice too — it proves nothing alone."""
    # Act
    run = _run_project(tmp, "--serial")

    # Assert
    assert run.rc == 0, (
        f"the serial run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert len(run.setups) == 1, (
        f"a serial run has one fixture session, so the session fixture must be "
        f"built exactly once; got {run.setups}"
    )
    assert len(run.uses) == _TOTAL_TESTS, (
        f"every test must observe the fixture; got {len(run.uses)} USE lines "
        f"for {_TOTAL_TESTS} tests"
    )


def test_the_parallel_run_actually_uses_more_than_one_worker(tmp: TempDir) -> None:
    """Non-vacuity guard.

    If the scheduler puts everything on one worker, every other parallel
    assertion below is satisfiable by a run-scoped implementation and proves
    nothing. The data project sets ``min_parallel_tests = 1`` and
    ``auto_arrange = false`` precisely so this cannot happen quietly.
    """
    # Act
    run = _run_project(tmp, "-n", "4")

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert len(run.running_pids) > 1, (
        f"the run executed on {run.running_pids} — a single PID means the "
        f"parallel path was never exercised, so the per-worker assertions "
        f"cannot distinguish per-worker from per-run semantics"
    )


@dataclass(frozen=True)
class _WorkerCount:
    """One ``-n`` value for the per-process assertion."""

    label: str
    workers: str


_ONE_WORKER = _WorkerCount(label="-n 1", workers="1")
#: Two workers over four modules — the count that makes a worker drain more
#: than one task group, and the only one of the three that discriminates
#: per-process from per-task-group.
_TWO_WORKERS = _WorkerCount(label="-n 2", workers="2")
_FOUR_WORKERS = _WorkerCount(label="-n 4", workers="4")


@oxi.parametrize(one=_ONE_WORKER, two=_TWO_WORKERS, four=_FOUR_WORKERS)
def test_the_session_fixture_is_built_once_per_worker(
    tmp: TempDir, case: _WorkerCount
) -> None:
    """The assertion this slice exists for.

    Scheduling decides how many workers receive work, so asserting a count
    would be flaky. The invariant is per-PID: every PID that ran a test built
    the fixture exactly once.

    **Parameterised over worker counts on purpose (#1777).** The data project
    has four modules, so at ``-n 4`` the old per-task-group behaviour is
    indistinguishable from the per-process contract — one module per worker
    means one build per worker either way. Only a worker count that does *not*
    divide the module count exercises a worker draining more than one task
    group, and ``-n 2`` did fail deterministically here until the worker built
    its session once per process rather than once per task. Pinning only
    ``-n 4`` is what let that gap sit unnoticed; see #1843, which owns the
    finding.
    """
    # Act
    run = _run_project(tmp, "-n", case.workers)

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass at {case.label}; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert sorted(run.setup_pids) == sorted(run.running_pids), (
        f"at {case.label}: SETUP fired on PIDs {sorted(run.setup_pids)} but tests "
        f"ran on {sorted(run.running_pids)}. this asserts one build per worker PID: "
        f"a PID appearing twice means the fixture was rebuilt within one worker — "
        f"the per-task-group behaviour — and a running PID missing entirely means "
        f"a worker served tests without it"
    )


def test_every_test_sees_its_own_workers_instance(tmp: TempDir) -> None:
    """A session value must not leak between processes, nor be rebuilt within one.

    The two guards below are load-bearing, not ceremony: this assertion is over
    a set of offenders, so an empty log satisfies it. Without them the test
    passed against a run that collected zero tests.
    """
    # Act
    run = _run_project(tmp, "-n", "4")

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert len(run.uses) == _TOTAL_TESTS, (
        f"every test must observe the fixture, or the offender check below has "
        f"nothing to inspect; got {len(run.uses)} USE lines for {_TOTAL_TESTS} tests"
    )
    by_pid: dict[str, set[str]] = {}
    for line in run.uses:
        _, _label, pid, instance = line.split()
        by_pid.setdefault(pid, set()).add(instance)
    offenders = {pid: ids for pid, ids in by_pid.items() if len(ids) != 1}
    assert not offenders, (
        f"these PIDs observed more than one instance: {offenders}. Within a "
        f"worker the session fixture is built once, so every test on that PID "
        f"must see the same value"
    )


def test_every_instance_is_torn_down(tmp: TempDir) -> None:
    """Teardown symmetry.

    Slice 3 shipped a disposal bug that instance-count assertions alone did not
    catch, which is why this is asserted separately from the build count.

    The two guards below are load-bearing: comparing two sets is satisfied by
    two empty sets, so without them the test passed against a run that built
    nothing at all.
    """
    # Act
    run = _run_project(tmp, "-n", "4")

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert run.setup_ids, (
        "no SETUP was recorded, so the symmetry check below would compare two "
        "empty sets and pass without the fixture ever being built"
    )
    assert run.setup_ids == run.teardown_ids, (
        f"built {sorted(run.setup_ids)} but tore down {sorted(run.teardown_ids)}. "
        f"Every session instance must be disposed at its worker's teardown; a "
        f"missing TEARDOWN leaks the resource for the life of the process"
    )


def test_session_below_the_rootdir_package_is_rejected(tmp: TempDir) -> None:
    """The cap engine's one reachable violation today.

    ``__fixtures__.py`` at ``nested/`` is below the testpath root, so it may
    declare at most ``package``. The message must name the fixture, the file,
    the tier, and the legal exits — a bare non-zero exit tells a user nothing
    about which declaration to change or what to change it to.
    """
    # Arrange
    log = Path(tmp) / "unused.log"
    env = {**os.environ, "SLICE4_LOG": str(log)}

    # Act
    stdout, stderr, rc = helpers.run_oxitest(_REJECT_PROJECT, env=env)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f"a session declaration below the rootdir package must fail the run; "
        f"rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    # The tier is matched as the full `lifetime="process"` literal, not as the
    # bare word. Every path the diagnostic prints contains
    # `slice4_session_below_root`, so a bare-word check matched the directory
    # name rather than the message and stayed green through the #1777 rename
    # while asserting nothing about the text.
    for expected in ("engine", "__fixtures__.py", 'lifetime="process"', "Hint"):
        assert expected in output, (
            f"the diagnostic must name {expected!r} so the user can act on it "
            f"without reading oxitest's source; got:\n{output}"
        )
    # The rootdir package is derived from the collected tree, so the user cannot
    # read it off their config — "move it to a rootdir package" is unactionable
    # unless the message says which directory that is.
    assert "slice4_session_below_root\n" not in output, (
        f"guard against the hint naming a bare directory with no path; got:\n{output}"
    )
    assert str(_REJECT_PROJECT / "slice4_session_below_root") in output, (
        f"the hint must name the directory that IS the rootdir package, not just "
        f"say one is required; got:\n{output}"
    )
