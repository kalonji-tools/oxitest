"""Ctrl-C still runs process-lifetime teardowns (#1777).

The worker drains its process tier from a ``try/finally`` around the stdin
loop, and the ``finally`` is load-bearing rather than tidy: the worker installs
no ``atexit`` hook and no signal handler, so it is the only construct that
survives the ``KeyboardInterrupt`` a Ctrl-C delivers. Move that drain anywhere
else — after the loop, into a handler — and an interrupted run silently skips
every process-lifetime teardown.

**Getting this to actually test the interrupt path took two attempts, and the
first one looked convincing.** A test that calls
``os.kill(os.getpid(), signal.SIGINT)`` in its own body produces a log showing
SETUP and TEARDOWN on every PID — but the executor *catches* the
``KeyboardInterrupt`` and reports it as a test error, so the worker finishes its
loop and exits normally. The teardown observed there is the ordinary path, and
the test would pass with the drain placed after the loop instead of in a
``finally``.

What a shell does on Ctrl-C is signal the whole foreground process group. This
test reproduces that: it starts the run in its own session, waits until a
fixture has actually been built, and then signals the group.
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
_SETUP_POLL_TIMEOUT_SECONDS = 30

_FIXTURES = """\
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

_TEST_MODULE = """\
import time

from oxitest import Fixture


def test_{name}(resource: Fixture[str]) -> None:
    assert resource, "the process-lifetime fixture must be injected"
    time.sleep({sleep})
"""

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["interrupted"]
python_files = ["test_*.py"]
auto_arrange = false
min_parallel_tests = 1
"""


def _write_project(root: Path) -> None:
    """Four slow modules, so the signal lands mid-run rather than after it."""
    pkg = root / "interrupted"
    pkg.mkdir(parents=True)
    (pkg / "__fixtures__.py").write_text(_FIXTURES)
    for name in _MODULES:
        (pkg / f"test_{name}.py").write_text(
            _TEST_MODULE.format(name=name, sleep=_TEST_SLEEP_SECONDS)
        )
    (root / "pyproject.toml").write_text(_PYPROJECT)


def _wait_for_setup(log: Path) -> bool:
    """Block until a fixture has been built, or the deadline passes.

    Polling the fixture's own log rather than sleeping a fixed interval: the
    assertion is about teardown, so signalling before anything has been *set
    up* would make the test vacuous in a way a fixed sleep hides on a slow
    machine.
    """
    deadline = time.monotonic() + _SETUP_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if log.exists() and "SETUP " in log.read_text():
            return True
        time.sleep(0.05)
    return False


def test_an_interrupted_run_still_tears_down_the_process_tier(
    tmp: TempDir,
) -> None:
    """SIGINT to the process group must not skip `end_process`."""
    # Arrange
    root = Path(tmp) / "proj"
    _write_project(root)
    log = Path(tmp) / "events.log"

    # Act — start_new_session gives the run its own process group, so the
    # signal reaches the coordinator and its workers exactly as a shell's
    # Ctrl-C would, and cannot reach the test runner driving this.
    proc = subprocess.Popen(
        [sys.executable, "-m", "oxitest", str(root), "--color", "never", "-n", "2"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "INTERRUPT_LOG": str(log)},
    )
    try:
        built = _wait_for_setup(log)
        assert built, (
            f"no fixture was built within {_SETUP_POLL_TIMEOUT_SECONDS}s, so "
            f"interrupting now would prove nothing about teardown"
        )
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)

    # Assert
    events = log.read_text().splitlines()
    setup_pids = [e.split()[1] for e in events if e.startswith("SETUP ")]
    teardown_pids = [e.split()[1] for e in events if e.startswith("TEARDOWN ")]
    assert setup_pids, f"the guard above should have caught this; got {events}"
    assert sorted(teardown_pids) == sorted(setup_pids), (
        f"built the fixture on {sorted(setup_pids)} but tore it down on "
        f"{sorted(teardown_pids)}. A process-lifetime teardown skipped on "
        f"interrupt is unrecoverable — no other process runs it — and it is what "
        f"happens the moment the drain moves out of main()'s finally, since the "
        f"worker has no atexit hook and no signal handler to fall back on"
    )
