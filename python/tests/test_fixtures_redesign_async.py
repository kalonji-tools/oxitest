"""Acceptance: async @oxi.fixture at both implemented lifetime tiers (#1733).

Runs oxitest as a subprocess against two data-projects and asserts on a log
the fixtures write themselves, rather than on reporter output — the question
is what each fixture actually did, not how a reporter phrased it.

The event *shape* carries most of the weight here. Counting setups and
teardowns alone would still pass if every disposal were deferred to the end of
the run, which is precisely the failure mode the survey in
``docs/agents/notes/async-fixture-scope-semantics.md`` found in every framework
that got this wrong.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"
_PROJECT = _DATA_ROOT / "async_lifetimes"
_REJECT_PROJECT = _DATA_ROOT / "async_sync_reject"

#: Test counts baked into the data-project's two modules.
_ALPHA_TESTS = 4
_BETA_TESTS = 2

#: Per-tier instance counts the project's usage implies. See the module
#: docstrings in ``data/async_lifetimes/async_lifetimes/`` for which test
#: touches which fixture.
_PER_TEST_BUILDS = 3  # alpha_one, alpha_two, alpha_double_await
_PER_TEST_GEN_BUILDS = 1  # alpha_generators only
_PER_MODULE_BUILDS = 2  # one per module
_PER_MODULE_GEN_BUILDS = 2  # alpha_generators + beta_one, one per module


@dataclass(frozen=True)
class _Run:
    """One acceptance run: the process output plus the fixtures' own log."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    def setups(self, kind: str) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith(f"SETUP {kind}-"))

    def teardowns(self, kind: str) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith(f"TEARDOWN {kind}-"))

    @property
    def uses(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("USE "))

    def pids(self, kind: str) -> set[str]:
        """Worker PIDs that built *kind*. Ids are ``<kind>-<pid>-<counter>``."""
        return {e.removeprefix("SETUP ").split("-")[1] for e in self.setups(kind)}

    def index_of(self, prefix: str) -> int:
        """Position of the first event starting with *prefix*, or -1."""
        for i, event in enumerate(self.events):
            if event.startswith(prefix):
                return i
        return -1


def _run_project(tmp: TempDir, *extra_args: str) -> _Run:
    """Run the async-lifetimes data-project with a fresh log file."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "ASYNC_LIFETIMES_LOG": str(log)}
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT, *extra_args, env=env)
    events = tuple(log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()
    return _Run(stdout=stdout, stderr=stderr, rc=rc, events=events)


def test_async_fixtures_arrive_awaited(tmp: TempDir) -> None:
    """The whole project passes, which means every fixture arrived awaited.

    Each test asserts on its own injected value's shape, so a raw coroutine
    fails the run rather than silently comparing unequal.
    """
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, (
        f"acceptance run failed (rc={run.rc}) — an async fixture did not "
        f"arrive awaited\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.uses) == _ALPHA_TESTS + _BETA_TESTS, (
        f"expected {_ALPHA_TESTS + _BETA_TESTS} tests to run, saw "
        f"{len(run.uses)} ({run.uses}) — every count below is meaningless if "
        f"the project did not run as written\nstdout:\n{run.stdout}"
    )


def test_no_never_awaited_warning(tmp: TempDir) -> None:
    """A coroutine reaching GC un-awaited is the #1733 symptom, and is silent.

    rc == 0 alone would not catch it: the warning does not fail a run, so this
    has to look for it explicitly.
    """
    run = _run_project(tmp, "--serial")

    combined = run.stdout + run.stderr
    assert "never awaited" not in combined, (
        "a coroutine was garbage-collected without being awaited — some "
        f"resolution path still hands back the raw coroutine\n{combined}"
    )


def test_function_lifetime_rebuilds_per_test(tmp: TempDir) -> None:
    """Function lifetime means a fresh instance for every requesting test."""
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, f"acceptance run failed (rc={run.rc})\n{run.stdout}"
    builds = run.setups("per_test")
    assert len(builds) == _PER_TEST_BUILDS, (
        f"function-lifetime fixture was built {len(builds)} times ({builds}), "
        f"expected {_PER_TEST_BUILDS} — a lower count means it was cached "
        "across tests, a higher one means it was rebuilt within a single test"
    )
    assert len(set(builds)) == len(builds), (
        f"two tests received the same function-lifetime instance ({builds}) — "
        "function lifetime must not share"
    )


def test_module_lifetime_builds_once_per_module(tmp: TempDir) -> None:
    """Module lifetime means exactly one instance per module, not per test."""
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, f"acceptance run failed (rc={run.rc})\n{run.stdout}"
    builds = run.setups("per_module")
    assert len(builds) == _PER_MODULE_BUILDS, (
        f"module-lifetime fixture was built {len(builds)} times ({builds}), "
        f"expected {_PER_MODULE_BUILDS} — a higher count is a per-test "
        "rebuild, a lower one is cross-module sharing"
    )
    assert len(set(builds)) == _PER_MODULE_BUILDS, (
        f"the two modules shared an instance ({builds}) — each module must get its own"
    )


def test_module_generator_is_disposed_at_the_module_boundary(tmp: TempDir) -> None:
    """The first module's instance is disposed before the second's is built.

    This is the assertion that distinguishes correct behaviour from deferring
    every async teardown to session end. Both produce the same counts; only
    the ordering differs.
    """
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, f"acceptance run failed (rc={run.rc})\n{run.stdout}"

    setups = run.setups("per_module_gen")
    teardowns = run.teardowns("per_module_gen")
    assert len(setups) == _PER_MODULE_GEN_BUILDS, (
        f"expected {_PER_MODULE_GEN_BUILDS} module-lifetime generator builds, "
        f"saw {setups}"
    )
    assert len(teardowns) == _PER_MODULE_GEN_BUILDS, (
        f"expected {_PER_MODULE_GEN_BUILDS} disposals, saw {teardowns} — an "
        "async generator whose teardown never ran leaks for the rest of the run"
    )

    first_id = setups[0].removeprefix("SETUP ")
    first_teardown = run.index_of(f"TEARDOWN {first_id}")
    second_setup = run.index_of(setups[1])
    assert first_teardown != -1, (
        f"the first module's instance {first_id!r} was never disposed\n"
        f"events: {run.events}"
    )
    assert first_teardown < second_setup, (
        f"the first module's instance {first_id!r} was disposed at position "
        f"{first_teardown}, after the second module's instance was built at "
        f"{second_setup} — teardown is being deferred past the module "
        f"boundary, which is the exact defect pytest-asyncio fixed across "
        f"0.23.3/0.25.1/0.25.3 and anyio in 4.1.0/4.14.1\nevents: {run.events}"
    )


def test_function_generator_is_disposed_within_its_test(tmp: TempDir) -> None:
    """A function-lifetime async generator tears down before the run ends."""
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, f"acceptance run failed (rc={run.rc})\n{run.stdout}"
    setups = run.setups("per_test_gen")
    teardowns = run.teardowns("per_test_gen")
    assert len(setups) == _PER_TEST_GEN_BUILDS, (
        f"expected {_PER_TEST_GEN_BUILDS} build, saw {setups}"
    )
    assert len(teardowns) == len(setups), (
        f"built {setups} but disposed {teardowns} — every function-lifetime "
        "async generator must run its post-yield half"
    )


def test_async_module_lifetime_holds_in_parallel(tmp: TempDir) -> None:
    """Same build and disposal counts under ``-n 2`` as serially.

    The worker path resolves fixtures in a subprocess with its own event loop
    and its own ``FixtureSession``, so none of the serial assertions carry
    over for free — #1732 was a fixture-visibility bug that existed only
    there. Promotion, the boundary drain, and the session clamp all have to
    hold per worker.

    The PID guard is load-bearing: if the scheduler collapses this to one
    process, every assertion below would pass on the serial path and prove
    nothing about workers.
    """
    run = _run_project(tmp, "-n", "2")

    assert run.rc == 0, (
        f"parallel run failed (rc={run.rc})\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.pids("per_module")) > 1, (
        f"all tests ran in one process ({run.pids('per_module')}) — the "
        f"scheduler collapsed this run to serial, so nothing below is "
        f"evidence about the worker path\nstdout:\n{run.stdout}"
    )
    assert len(run.setups("per_module")) == _PER_MODULE_BUILDS, (
        f"module-lifetime fixture was built {run.setups('per_module')} — "
        f"expected {_PER_MODULE_BUILDS}, one per module, in workers as "
        "serially"
    )
    assert len(run.teardowns("per_module_gen")) == _PER_MODULE_GEN_BUILDS, (
        f"disposed {run.teardowns('per_module_gen')} of "
        f"{run.setups('per_module_gen')} — a worker returned without draining "
        "its module scope, so the fixture's teardown never ran at all"
    )
    assert len(run.teardowns("per_test_gen")) == _PER_TEST_GEN_BUILDS, (
        f"function-lifetime generator disposed "
        f"{run.teardowns('per_test_gen')} times — the in-body drain has to "
        "work in a worker's loop too"
    )


def test_sync_test_reaching_an_async_fixture_is_rejected() -> None:
    """ADR-0006's one illegal cell, reached through the ``fx.`` proxy.

    The old API rejects this at arrange time for parameter injection. The
    proxy path had no guard at all, which is how #1733 stayed silent.
    """
    stdout, stderr, rc = helpers.run_oxitest(_REJECT_PROJECT, "--serial")
    combined = stdout + stderr

    assert rc != 0, (
        "a sync test reached a function-lifetime async fixture and the run "
        f"passed (rc={rc}) — this cell must be rejected, not silently handed "
        f"a coroutine\nstdout:\n{stdout}"
    )
    assert "async" in combined.lower(), (
        f"the failure does not mention async at all, so it is probably a "
        f"different error than the one under test\n{combined}"
    )
    assert "async def" in combined, (
        "the diagnostic must name its legal exits — ADR-0006 requires "
        "'make the test async def' among them, so the user is not left "
        f"guessing\n{combined}"
    )
