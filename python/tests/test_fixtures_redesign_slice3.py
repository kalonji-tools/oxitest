"""Slice-3 acceptance: lifetime="package" end-to-end (#1710).

Runs oxitest as a subprocess and asserts on a log the fixture writes itself,
rather than on reporter output — the question is what the fixture actually did.

This is the acceptance boundary for slice 3, and the parallel assertion is the
one that matters. The serial answer was already correct before this slice; the
tier exists because the parallel answer was not. A serial-only proof would pass
vacuously. ``test_package_lifetime_scope.py`` gives the fast unit-level feedback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"
_PROJECT = _DATA_ROOT / "slice3_package_lifetime"

#: Test counts baked into the data-project's three modules.
_ALPHA_TESTS = 2
_BETA_TESTS = 2
_GAMMA_TESTS = 1
_TOTAL_TESTS = _ALPHA_TESTS + _BETA_TESTS + _GAMMA_TESTS


@dataclass(frozen=True)
class _Run:
    """One acceptance run: the process output plus the fixture's own log."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    @property
    def setups(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("SETUP"))

    @property
    def teardowns(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("TEARDOWN"))

    @property
    def uses(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("USE"))

    def ids(self, kind: str) -> tuple[str, ...]:
        prefix = f"{kind} "
        return tuple(e[len(prefix) :] for e in self.events if e.startswith(prefix))

    @property
    def used_instance_ids(self) -> set[str]:
        """The instance id each USE line observed."""
        return {e.split(" ", 2)[2] for e in self.uses}


def _run_project(tmp: TempDir, *extra_args: str) -> _Run:
    """Run the slice-3 data-project with a fresh log file."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "SLICE3_LOG": str(log)}
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT, *extra_args, env=env)
    events = helpers.read_event_log(log)
    return _Run(stdout=stdout, stderr=stderr, rc=rc, events=events)


def test_declared_in_init_py_and_shared_across_the_package(tmp: TempDir) -> None:
    """One instance serves every module in the package and its subdirectory."""
    # Act
    run = _run_project(tmp)

    # Assert
    assert run.rc == 0, (
        f"the data-project must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert len(run.uses) == _TOTAL_TESTS, (
        f"every test must resolve the fixture, got {len(run.uses)} of "
        f"{_TOTAL_TESTS} — a missing USE means the fixture was never injected, "
        f"which would make the count assertions below meaningless"
    )
    assert len(run.setups) == 1, (
        f"package lifetime means exactly one instance per run, got "
        f"{len(run.setups)}: {run.setups}. More than one is the silent duplicate "
        f"this tier exists to prevent"
    )
    assert len(run.used_instance_ids) == 1, (
        f"every test must observe the same instance, saw "
        f"{run.used_instance_ids} — distinct ids mean the value was rebuilt"
    )


def test_the_fixture_is_found_in_init_py(tmp: TempDir) -> None:
    """`__init__.py` works as a declaration home, not just `__fixtures__.py`."""
    # Act
    run = _run_project(tmp)

    # Assert — the data-project declares its fixture only in __init__.py. If
    # prescan scanned solely __fixtures__.py the fixture would not exist and
    # every test would error on resolution, so a clean run is the proof.
    assert run.rc == 0, (
        f"a fixture declared in __init__.py must be discovered; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert run.setups, (
        "the fixture declared in __init__.py never ran — prescan is not scanning "
        "that file as a declaration home"
    )


def test_a_subdirectory_without_init_py_shares_the_ancestor_instance(
    tmp: TempDir,
) -> None:
    """The boundary is the directory; `__init__.py` does not define it."""
    # Act
    run = _run_project(tmp)

    # Assert — sub/ carries no __init__.py. Under ADR-0009's original wording it
    # would not be part of any package; under the amended wording (#1746) it is
    # a descendant directory of the anchor and must reuse the ancestor's value.
    gamma = [e for e in run.uses if e.startswith("USE gamma")]
    assert len(gamma) == _GAMMA_TESTS, (
        f"the subdirectory's test did not run or did not resolve the fixture: {gamma}"
    )
    assert len(run.used_instance_ids) == 1, (
        f"the subdirectory built its own instance, ids={run.used_instance_ids} — "
        f"a descendant directory must reuse the anchor's value"
    )


def test_disposal_happens_once_after_every_module(tmp: TempDir) -> None:
    """Teardown fires once, at package exit rather than per module."""
    # Act
    run = _run_project(tmp)

    # Assert
    assert len(run.teardowns) == 1, (
        f"expected one disposal for one instance, got {len(run.teardowns)}: "
        f"{run.teardowns}"
    )
    last_use = max(i for i, e in enumerate(run.events) if e.startswith("USE"))
    teardown_at = next(i for i, e in enumerate(run.events) if e.startswith("TEARDOWN"))
    assert teardown_at > last_use, (
        f"teardown ran at index {teardown_at}, before the last USE at "
        f"{last_use} — a package-lifetime value must outlive every test in the "
        f"package, including those in descendant directories"
    )


def test_the_collapse_is_announced(tmp: TempDir) -> None:
    """The parallelism the tier costs is stated, not hidden."""
    # Act
    run = _run_project(tmp, "--warnings")

    # Assert — choosing a structural guarantee means the tier never lies about
    # how many instances exist. Staying silent about the cost would reinstate
    # the lie from the other side: a suite dropping to one worker because of a
    # single decorator, diagnosed weeks later by whoever bisects CI times.
    assert 'engine (lifetime="package")' in run.stdout, (
        f"the warning must name the fixture responsible; a file can declare "
        f"several and only some at package lifetime.\nstdout:\n{run.stdout}"
    )
    assert "co-locates 3 modules onto one worker" in run.stdout, (
        f"the warning must quote how many modules were merged — that number is "
        f"what tells the user whether the cost matters.\nstdout:\n{run.stdout}"
    )
    assert 'drop to lifetime="module"' in run.stdout, (
        f"the warning must name a way out, or it is just bad news.\n"
        f"stdout:\n{run.stdout}"
    )


def test_no_collapse_warning_without_a_package_declaration(tmp: TempDir) -> None:
    """A suite that does not use the tier is not warned about it."""
    # Arrange — the slice-2 project declares only lifetime="module".
    log = Path(tmp) / "events.log"
    env = {**os.environ, "SLICE2_LOG": str(log)}
    stdout, _stderr, _rc = helpers.run_oxitest(
        _DATA_ROOT / "slice2_module_lifetime", "--warnings", env=env
    )

    # Assert — a warning here would be noise that trains users to ignore the
    # message, and builtins must not trigger it either: TempDirFactory is
    # session-scoped and would otherwise flag every run oxitest ever does.
    assert "co-locates" not in stdout, (
        f"a suite with no package-lifetime declaration must not be warned about "
        f"co-location.\nstdout:\n{stdout}"
    )


def test_inprocess_inside_a_package_is_rejected_at_collection(tmp: TempDir) -> None:
    """The one combination that cannot honour exactly-once is refused up front.

    `@oxi.mark.inprocess` splits marked items into a phase that runs on the
    coordinator's session while the rest of the package runs on a worker's, so
    the fixture is built once in each. Reproduced before this check existed:
    one marked test plus `-n 4` produced two SETUPs with different PIDs.

    Rejected at collection rather than warned about, following ADR-0009's
    precedent for B1 violations — the guarantee cannot hold, and a tier that
    quietly stops being exactly-once under load is the failure this whole slice
    exists to prevent.
    """
    # Arrange
    log = Path(tmp) / "events.log"
    env = {**os.environ, "SLICE3_LOG": str(log)}

    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _DATA_ROOT / "slice3_inprocess_reject", env=env
    )

    # Assert
    assert rc != 0, (
        f"the combination must fail collection, not run; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    combined = stdout + stderr
    assert "inprocess" in combined and 'lifetime="package"' in combined, (
        f"the error must name both halves of the conflict, or the user cannot "
        f"tell which of the two to change.\n{combined}"
    )
    assert "test_marked" in combined, (
        f"the error must name the offending test — a package can hold many.\n{combined}"
    )


def test_exactly_once_survives_parallel_execution(tmp: TempDir) -> None:
    """The guarantee holds when the scheduler distributes work across workers.

    This is the assertion the whole slice exists for. The data-project sets
    ``min_parallel_tests = 1`` and arranges nothing, so the run genuinely
    goes through worker subprocesses rather than falling back to the main
    process, where the answer was already correct before this slice.
    """
    # Act — force multiple workers explicitly rather than trusting the default.
    run = _run_project(tmp, "-n", "4")

    # Assert
    assert run.rc == 0, (
        f"the parallel run must pass; rc={run.rc}\nstdout:\n{run.stdout}\n"
        f"stderr:\n{run.stderr}"
    )
    assert len(run.setups) == 1, (
        f"the package fixture was built {len(run.setups)} times under parallel "
        f"execution: {run.setups}. Instance ids are PID-qualified, so distinct "
        f"ids here mean the scheduler split the package across workers and the "
        f"co-location that makes this tier a guarantee is not holding"
    )
    assert len(run.used_instance_ids) == 1, (
        f"tests observed {run.used_instance_ids} — every test in the package "
        f"must see the same instance no matter which worker ran it"
    )
