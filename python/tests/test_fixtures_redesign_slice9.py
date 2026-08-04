"""Slice-9 acceptance: autouse fixtures end-to-end (#1716).

Runs oxitest as a subprocess and asserts on a log the fixtures write
themselves, rather than on reporter output — the question is what each fixture
actually did, and an autouse fixture is by definition never named by the test.

The data project declares its four autouse fixtures **narrowest first**, so
declaration order and firing order disagree. That matters: registration order
is what the pre-#1716 implementation yielded, so the wrong answer here is the
plausible one rather than an exotic one.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"
_TIERS = _DATA_ROOT / "slice9_autouse_tiers"
_OPTOUT = _DATA_ROOT / "slice9_autouse_optout"
_PROCESS = _DATA_ROOT / "slice9_autouse_process"

#: Four modules x two tests, baked into the process data project.
_PROCESS_TESTS = 8

#: Three plain modules x two tests, plus the one gamma test that also requests
#: the fixture explicitly.
_TOTAL_TESTS = 5

#: Two modules hold the plain tests; gamma is the third.
_TOTAL_MODULES = 3


def _fires(run: helpers.EventLogRun, name: str) -> list[str]:
    """Every ``FIRE <name> <pid>`` line for one fixture, in order."""
    return run.lines(f"FIRE {name} ")


def _run_tiers(tmp: TempDir, log_name: str, *extra_args: str) -> helpers.EventLogRun:
    """Run the tiers data-project with its own log file.

    *log_name* is per-run on purpose: the fixtures append, so two runs sharing
    one name would read the first run's events back as part of the second and
    double every count.
    """
    return helpers.run_with_event_log(
        _TIERS, tmp, "SLICE9_LOG", *extra_args, log_name=log_name
    )


def test_autouse_fires_once_per_lifetime_boundary(tmp: TempDir) -> None:
    """Each tier fires at its own boundary, without any test requesting it."""
    # Act
    run = _run_tiers(tmp, "tiers-boundaries.log", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.lines("TEST ")) == _TOTAL_TESTS, (
        f"every test must have run, or the counts below are measured against a "
        f"suite that partly did not execute; got {run.lines('TEST ')}"
    )
    assert len(_fires(run, "per_test")) == _TOTAL_TESTS, (
        f"a function-lifetime autouse fixture fires once per test in its B1 "
        f"boundary; got {_fires(run, 'per_test')}"
    )
    assert len(_fires(run, "per_module")) == _TOTAL_MODULES, (
        f"a module-lifetime autouse fixture fires once per module boundary — "
        f"the scope cache is what collapses the other tests in each module; "
        f"got {_fires(run, 'per_module')}"
    )
    assert len(_fires(run, "per_package")) == 1, (
        f"rootdir package lifetime is the only exactly-once-per-run tier; "
        f"got {_fires(run, 'per_package')}"
    )
    assert len(_fires(run, "per_process")) == 1, (
        f"a serial run is one process, so the process tier fires once here. "
        f"This is the serial control and proves nothing about parallel "
        f"semantics alone — the per-resolving-process test is what "
        f"distinguishes per-process from per-run; got "
        f"{_fires(run, 'per_process')}"
    )


def test_autouse_fires_widest_lifetime_first(tmp: TempDir) -> None:
    """Firing order is the tier order, not the declaration order (#1716).

    The data project declares narrowest-first, so this ordering is the exact
    reverse of the file. A registration-order implementation yields the
    declaration order and fails here.
    """
    # Act
    run = _run_tiers(tmp, "tiers-order.log", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    first_test = run.events.index(run.lines("TEST ")[0])
    before_first_test = [
        event.split()[1]
        for event in run.events[:first_test]
        if event.startswith("FIRE ")
    ]
    assert before_first_test == [
        "per_process",
        "per_package",
        "per_module",
        "per_test",
    ], (
        "the first test must see all four tiers fire widest-first, so a "
        "narrower autouse fixture can rely on a wider one having run. "
        "Declaration order in __fixtures__.py is the exact reverse, which is "
        f"what a registration-order implementation yields; got {before_first_test}"
    )


def test_autouse_and_explicit_request_share_one_instance(tmp: TempDir) -> None:
    """Autouse is additive, not duplicative (#1716, pins #1775's cache).

    ``_cache_key`` is keyed on the definition with no route discriminator, so
    the autouse pass and the ``fx.`` proxy land in one cache entry. A future
    change adding a route discriminator would double-build every autouse
    fixture a test also requests — and for a side-effect fixture that means the
    side effect happening twice, silently.

    The data project's gamma test asserts the *value* it receives is the
    autouse instance; this asserts the *count*. Both halves are needed: equal
    values with two builds is exactly what a second identical instance looks
    like.
    """
    # Act
    run = _run_tiers(tmp, "tiers-additive.log", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the run must pass — gamma's own assertion that the injected value is "
        f"the autouse instance runs inside it; rc={run.rc}\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(_fires(run, "per_test")) == _TOTAL_TESTS, (
        f"gamma both fires per_test by autouse and requests it via fx., so a "
        f"double build would show {_TOTAL_TESTS + 1} fires for {_TOTAL_TESTS} "
        f"tests; got {_fires(run, 'per_test')}"
    )


def test_a_nested_declaration_opts_its_subtree_out(tmp: TempDir) -> None:
    """Shadowing with a non-autouse declaration is the supported opt-out (#1716).

    The nested package declares ``setup`` without ``autouse``. Inside that
    subtree it is the deepest visible declaration, so it is what resolution
    returns — and nothing queues it, so the ancestor's autouse fixture does not
    fire there. Outside the subtree the nested declaration is invisible and the
    ancestor still fires.
    """
    # Act
    run = helpers.run_with_event_log(
        _OPTOUT, tmp, "SLICE9_LOG", "--serial", log_name="optout.log"
    )

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.lines("TEST ")) == 2, (
        f"both the inner and outer test must run, or the fire count below is "
        f"measured against a suite that partly did not execute; got "
        f"{run.lines('TEST ')}"
    )
    assert len(_fires(run, "setup")) == 1, (
        "the autouse fixture fires for the outer module and not for the nested "
        "one. Two fires means the opt-out stopped working and a package that "
        "deliberately declined an ancestor's autouse silently got it back; "
        "zero means the suppression leaked outside its boundary, which "
        "disables the fixture for tests that cannot even see the nested "
        f"declaration; got {_fires(run, 'setup')}"
    )


def test_the_optout_is_announced(tmp: TempDir) -> None:
    """The registration notice says autouse was suppressed (#1716).

    Shadowing is legitimate and documented, so this stays a NOTICE. It exists
    for the case the user did *not* intend: two unrelated fixtures picking the
    same name silently disable one of them for a whole subtree, and this is the
    only signal until the inspect autouse-firing view ships (slice 15, #1722).

    ``--warnings`` is not optional. Without it the notice is counted but never
    rendered, and an assertion on its text passes against output that never
    contained the message.
    """
    # Act
    run = helpers.run_with_event_log(
        _OPTOUT,
        tmp,
        "SLICE9_LOG",
        "--serial",
        "--warnings",
        log_name="optout-warnings.log",
    )

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    output = run.stdout + run.stderr
    assert "no longer fires" in output, (
        "the notice must name the consequence, not just the fact of shadowing "
        "— 'shadows definition in X' reads as a naming nit for what is actually "
        f"a fixture that stopped running. Output was:\n{output}"
    )


def _run_process(tmp: TempDir, log_name: str, *extra_args: str) -> helpers.EventLogRun:
    """Run the process-tier data-project with its own log file."""
    return helpers.run_with_event_log(
        _PROCESS, tmp, "SLICE9_PROC_LOG", *extra_args, log_name=log_name
    )


def test_process_autouse_fires_once_in_a_serial_run(tmp: TempDir) -> None:
    """The serial control. Correct before this slice too — it proves nothing alone.

    A serial run has one process for everything, so this outcome is equally
    consistent with per-run, per-worker and per-process semantics. It is here to
    catch the opposite failure: a fixture that fires per *test* shows eight.
    """
    # Act
    run = _run_process(tmp, "process-serial.log", "--serial")

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.uses) == _PROCESS_TESTS, (
        f"every test must have run, or the build count is measured against a "
        f"suite that partly did not execute; got {len(run.uses)} USE lines"
    )
    assert len(run.setups) == 1, (
        f"one process means one build; {_PROCESS_TESTS} would mean the fixture "
        f"is firing per test and the process tier collapsed to function "
        f"lifetime; got {run.setups}"
    )


def test_the_parallel_run_actually_uses_more_than_one_process(tmp: TempDir) -> None:
    """Non-vacuity guard.

    If the scheduler puts everything on one worker, the per-process assertion
    below is satisfiable by a run-scoped implementation and proves nothing. The
    data project sets ``min_parallel_tests = 1`` and ``auto_arrange = false``
    precisely so this cannot happen quietly.
    """
    # Act
    run = _run_process(tmp, "process-nonvacuity.log", "-n", "2")

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.running_pids) > 1, (
        f"the run executed on {run.running_pids} — a single PID means the "
        f"parallel path was never exercised, so the per-process assertion "
        f"cannot distinguish per-process from per-run semantics"
    )


def test_process_autouse_fires_once_per_resolving_process(tmp: TempDir) -> None:
    """One build per process that ran a test — derived, not bounded (#1716).

    The promise is ``<= 1 + N``, a range: a worker that never receives a task
    reaching the boundary never fires the fixture, and which worker gets which
    task group is a scheduling outcome. Asserting the range would pass even if
    the fixture fired once per test, so the expected count is derived from the
    PIDs that actually executed something.

    Nothing in the data project requests this fixture. If it stopped firing
    entirely, no test would fail and no error would be raised — which is why
    the first assertion checks the set is non-empty by comparing against the
    running PIDs rather than by counting.
    """
    # Act
    run = _run_process(tmp, "process-parallel.log", "-n", "2")

    # Assert
    assert run.rc == 0, (
        f"the run must pass; rc={run.rc}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert set(run.setup_pids) == run.running_pids, (
        f"exactly the processes that ran a test must have built the fixture. A "
        f"missing PID means tests ran without their autouse setup — silently, "
        f"since no test names it. An extra PID means a process built it without "
        f"running anything it was needed for; setups={run.setup_pids} "
        f"ran={run.running_pids}"
    )
    assert len(run.setup_pids) == len(set(run.setup_pids)), (
        f"no process may build a process-lifetime fixture twice. A bare count "
        f"cannot see this — one process building twice and two processes "
        f"building once give the same total, which is why the log ids are "
        f"PID-qualified; got {run.setup_pids}"
    )
