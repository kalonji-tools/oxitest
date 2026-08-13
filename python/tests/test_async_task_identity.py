"""Task identity and phase order for async fixtures at function lifetime.

ADR-0006 requires that a fixture's setup, the test body and the fixture's
teardown share one event loop. One loop is necessary and not sufficient: a
fixture holding an ``anyio.CancelScope``, an ``asyncio.TaskGroup`` or a
``ContextVar`` across its ``yield`` depends on them sharing one **task**
(kalonji-tools/oxitest#1740).

These assert on a log the fixtures write themselves, because no reporter
reports which task ran a phase. The data project's fixtures record task
**names**, which come from a monotonic counter — ``id()`` cannot tell one task
from three, because CPython can give a freed task's address to the next one.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "arranged_task_identity"


def _run(tmp: TempDir, *extra: str) -> tuple[tuple[str, ...], int]:
    """Run the data project and return its event log and its exit code."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "TASK_IDENTITY_LOG": str(log)}
    _stdout, _stderr, rc = helpers.run_oxitest(_PROJECT, *extra, env=env)
    events: list[str] = []
    for path in sorted(Path(tmp).glob("events.log.*")):
        events.extend(path.read_text(encoding="utf-8").splitlines())
    return tuple(events), rc


def _tasks(events: tuple[str, ...]) -> dict[str, str]:
    """Map each phase label to the task name it recorded."""
    return {
        line.split()[1]: line.split("task=")[1].split()[0]
        for line in events
        if "task=" in line
    }


def test_phase_order_is_lifo(tmp: TempDir) -> None:
    """Teardowns fire in the reverse of their setup order."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_ordering)")

    # Assert
    order = [line.split()[0] for line in events]
    assert order == ["1", "2", "3", "4", "5", "6", "7", "8", "9"], (
        f"phase order was {order} — a fixture's teardown must run before the "
        "teardown of anything set up before it, or a teardown can touch a "
        "resource an earlier fixture already released"
    )


def test_arranged_fixture_shares_the_body_task(tmp: TempDir) -> None:
    """An arranged async fixture sets up and tears down in the body task."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_ordering)")

    # Assert
    tasks = _tasks(events)
    assert tasks["ARRANGED-SETUP"] == tasks["BODY"] == tasks["ARRANGED-TEARDOWN"], (
        f"the three phases ran in {tasks} — a fixture holding a CancelScope, a "
        "TaskGroup or a ContextVar across its yield depends on one task, and "
        "anyio raises 'Attempted to exit cancel scope in a different task than "
        "it was entered in' when that does not hold"
    )


def test_parameter_fixture_shares_the_body_task(tmp: TempDir) -> None:
    """A parameter-injected async fixture shares the body task."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_ordering)")

    # Assert
    tasks = _tasks(events)
    assert tasks["PARAM-SETUP"] == tasks["BODY"] == tasks["PARAM-TEARDOWN"], (
        f"the three phases ran in {tasks} — this route already held the "
        "invariant before #1740, so a break here is a regression in the route "
        "that route's fix was modelled on"
    )


def test_arranged_fixture_context_reaches_its_teardown(tmp: TempDir) -> None:
    """A ContextVar set in an arranged setup survives to its teardown."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_ordering)")

    # Assert
    teardown = next(e for e in events if e.startswith("8 ARRANGED-TEARDOWN"))
    assert "reads=set-in-setup" in teardown, (
        f"the teardown read {teardown} — a ContextVar set during setup is lost "
        "when the teardown runs in a different task, and the loss is silent"
    )


def test_parameter_fixture_context_crosses_the_body(tmp: TempDir) -> None:
    """A ContextVar propagates setup to body and body to teardown."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_ordering)")

    # Assert
    body = next(e for e in events if e.startswith("5 BODY"))
    teardown = next(e for e in events if e.startswith("7 PARAM-TEARDOWN"))
    assert "reads=set-in-setup" in body, (
        f"the body read {body} — a ContextVar set during setup must reach the "
        "test, which is the half a user notices first"
    )
    assert "reads=set-in-body" in teardown, (
        f"the teardown read {teardown} — propagation runs both ways in one "
        "task, and a teardown that cannot see the body's writes cannot clean "
        "up after them"
    )


def test_arranged_setup_failure_is_an_error_and_skips_the_body(tmp: TempDir) -> None:
    """An arranged setup failure is an ERROR, exits 1, and skips the body."""
    # Arrange / Act
    events, rc = _run(tmp, "--serial", "-E", "name(test_arranged_setup_failure)")

    # Assert
    assert rc == 1, (
        f"exit code was {rc} — an arranged fixture that raises during setup is "
        "a setup error and exits 1; exit 4 is reserved for a wiring error "
        "(#1761), so a 4 here means the failure was reclassified"
    )
    assert not any(e.startswith("FAILURE-BODY") for e in events), (
        f"the body ran (events={events}) — a test whose arranged fixture failed "
        "setup must not execute, or the failure reads as a test failure"
    )


def test_plain_coroutine_arranged_fixture_runs_in_the_body_task(tmp: TempDir) -> None:
    """A plain-coroutine arranged fixture is awaited in the body task."""
    # Arrange / Act
    events, _rc = _run(tmp, "--serial", "-E", "name(test_plain_coroutine_arranged)")

    # Assert
    tasks = _tasks(events)
    assert tasks["PLAIN-SETUP"] == tasks["BODY"], (
        f"setup and body ran in {tasks} — a plain-coroutine arranged fixture "
        "registers no teardown, so nothing else in the suite would report a "
        "split between its setup and the test"
    )
