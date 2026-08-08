"""Ctrl-C still runs process-lifetime teardowns (#1777, #1962).

The worker drains its process tier from a ``try/finally`` around the stdin
loop, and the ``finally`` is load-bearing rather than tidy: the worker installs
no ``atexit`` hook and no signal handler, so it is the only construct that
survives the ``KeyboardInterrupt`` a Ctrl-C delivers.

**Getting this to actually test the interrupt path took two attempts, and the
first one looked convincing.** A test that calls
``os.kill(os.getpid(), signal.SIGINT)`` in its own body produces a log showing
SETUP and TEARDOWN on every PID — but the executor *catches* the
``KeyboardInterrupt`` and reports it as a test error, so the worker finishes its
loop and exits normally. The teardown observed there is the ordinary path, and
the test would pass with the drain placed after the loop instead of in a
``finally``.

What a shell does on Ctrl-C is signal the whole foreground process group. These
tests reproduce that: they start the run in its own session, wait until the
fixture is actually in use, and then signal the group.

**Two things about the assertion, both learned the hard way (#1962).**

*It waits for every worker it observes.* The original waited for the **first**
``SETUP`` line and then signalled, so on a fast machine the second worker had
not reached the fixture yet: 28 of 30 local runs observed a single worker and
could not have failed. Its CI failure rate was the rate at which it accidentally
satisfied its own precondition.

*It asserts on ``USE``, not on ``SETUP``.* ``SETUP`` is recorded **before** the
``yield``, so a fixture can legitimately log it and never be torn down — an
interrupt arriving before the ``yield`` means setup never completed, and
oxitest deliberately does not run a teardown for it (the
``contextlib.contextmanager`` contract; see ``_boundary.setup_completed``).
Asserting ``teardown_pids == setup_pids`` therefore encodes a promise oxitest
does not make, which is a second, independent reason the original was flaky.
``USE`` is written from the test body, which runs only once the fixture has
been fully resolved and registered, so it is the honest observable.

The registration window itself is covered deterministically by unit tests —
``test_fixture_outcome.py`` and ``test_boundary.py`` — because no signal-based
test can place an interrupt inside a specific few-bytecode span on demand.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from oxitest import TempDir

_MODULES = ("a", "b", "c", "d")
#: Long enough that the signal lands while tests are still running, short
#: enough that a missed signal fails the test rather than hanging CI.
_TEST_SLEEP_SECONDS = 3
_USE_POLL_TIMEOUT_SECONDS = 60
#: Both workers must be in the fixture before signalling, or the test observes
#: one worker and proves nothing about the other.
_WORKERS = 2

_SYNC_FIXTURES = """\
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    with Path(os.environ["INTERRUPT_LOG"]).open("a") as fh:
        fh.write(event + "\\n")


@oxi.fixture(lifetime="process")
def resource() -> Iterator[str]:
    _record(f"SETUP {os.getpid()}")
    yield "resource"
    _record(f"TEARDOWN {os.getpid()}")
"""

_ASYNC_FIXTURES = """\
import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    with Path(os.environ["INTERRUPT_LOG"]).open("a") as fh:
        fh.write(event + "\\n")


@oxi.fixture(lifetime="process")
async def resource() -> AsyncIterator[str]:
    _record(f"SETUP {os.getpid()}")
    yield "resource"
    _record(f"TEARDOWN {os.getpid()}")
"""

_SYNC_TEST = """\
import os
import time
from pathlib import Path

from oxitest import Fixture


def _record(event: str) -> None:
    with Path(os.environ["INTERRUPT_LOG"]).open("a") as fh:
        fh.write(event + "\\n")


def test_NAME(resource: Fixture[str]) -> None:
    assert resource, "the process-lifetime fixture must be injected"
    _record("USE " + str(os.getpid()))
    time.sleep(SLEEP)
"""

_ASYNC_TEST = """\
import asyncio
import os
from pathlib import Path

from oxitest import Fixture


def _record(event: str) -> None:
    with Path(os.environ["INTERRUPT_LOG"]).open("a") as fh:
        fh.write(event + "\\n")


async def test_NAME(resource: Fixture[str]) -> None:
    assert resource, "the process-lifetime fixture must be injected"
    _record("USE " + str(os.getpid()))
    await asyncio.sleep(SLEEP)
"""

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["interrupted"]
python_files = ["test_*.py"]
auto_arrange = false
min_parallel_tests = 1
"""


def _write_project(root: Path, fixtures: str, test_template: str) -> None:
    """Four slow modules, so the signal lands mid-run rather than after it."""
    pkg = root / "interrupted"
    pkg.mkdir(parents=True)
    (pkg / "__fixtures__.py").write_text(fixtures)
    body = test_template.replace("SLEEP", str(_TEST_SLEEP_SECONDS))
    for name in _MODULES:
        (pkg / f"test_{name}.py").write_text(body.replace("NAME", name))
    (root / "pyproject.toml").write_text(_PYPROJECT)


def _pids(log: Path, prefix: str) -> set[str]:
    """Distinct PIDs that have written *prefix* so far."""
    try:
        lines = log.read_text().splitlines()
    except FileNotFoundError:
        return set()
    return {line.split()[1] for line in lines if line.startswith(prefix)}


def _wait_for_use(log: Path) -> bool:
    """Block until every worker is inside a test body, or the deadline passes.

    Polling ``USE`` rather than ``SETUP``: the fixture is only fully resolved
    and registered once the body it feeds starts running, so this is the point
    from which a teardown is actually owed (#1962).
    """
    deadline = time.monotonic() + _USE_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if len(_pids(log, "USE ")) >= _WORKERS:
            return True
        time.sleep(0.05)
    return False


def _run_interrupted(root: Path, log: Path) -> tuple[set[str], set[str]]:
    """Run the project under ``-n 2``, SIGINT the group, return (used, torn)."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(root),
            "--color",
            "never",
            "-n",
            str(_WORKERS),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "INTERRUPT_LOG": str(log)},
    )
    try:
        reached = _wait_for_use(log)
        assert reached, (
            f"only {len(_pids(log, 'USE '))} of {_WORKERS} workers entered a "
            f"test body within {_USE_POLL_TIMEOUT_SECONDS}s, so interrupting "
            f"now would observe fewer workers than the test claims to check"
        )
        # start_new_session gives the run its own process group, so the signal
        # reaches the coordinator and its workers exactly as a shell's Ctrl-C
        # would, and cannot reach the test runner driving this.
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    return _pids(log, "USE "), _pids(log, "TEARDOWN ")


def _assert_every_user_was_torn_down(used: set[str], torn: set[str]) -> None:
    missing = sorted(used - torn)
    assert not missing, (
        f"workers {missing} ran a test against the process-lifetime fixture "
        f"but never tore it down (used={sorted(used)}, torn={sorted(torn)}). A "
        f"process-lifetime teardown skipped on interrupt is unrecoverable — no "
        f"other process runs it — and it is what happens if the drain moves out "
        f"of main()'s finally, since the worker has no atexit hook and no "
        f"signal handler, or if a teardown is registered only after its "
        f"generator has been advanced (#1962)"
    )


def test_an_interrupted_run_still_tears_down_the_process_tier(
    tmp: TempDir,
) -> None:
    """SIGINT to the process group must not skip `end_process`."""
    # Arrange
    root = Path(tmp) / "proj"
    _write_project(root, _SYNC_FIXTURES, _SYNC_TEST)
    log = Path(tmp) / "events.log"

    # Act
    used, torn = _run_interrupted(root, log)

    # Assert
    assert len(used) == _WORKERS, (
        f"the guard in _run_interrupted should have caught this; used={sorted(used)}"
    )
    _assert_every_user_was_torn_down(used, torn)


def test_an_interrupted_run_tears_down_an_async_process_fixture(
    tmp: TempDir,
) -> None:
    """The async half of the same contract — untested until #1962.

    Async fixtures reach the drain by a different route entirely: the
    generator is advanced on the shared session's loop and its teardown is
    queued through ``SharedAsyncManager``, not through the sync scope's
    teardown list. Before #1962 this path lost the teardown far more often
    than the sync one, because the advance suspends to the event loop —
    which is exactly where a pending signal is delivered.
    """
    # Arrange
    root = Path(tmp) / "proj_async"
    _write_project(root, _ASYNC_FIXTURES, _ASYNC_TEST)
    log = Path(tmp) / "events_async.log"

    # Act
    used, torn = _run_interrupted(root, log)

    # Assert
    assert len(used) == _WORKERS, (
        f"the guard in _run_interrupted should have caught this; used={sorted(used)}"
    )
    _assert_every_user_was_torn_down(used, torn)
