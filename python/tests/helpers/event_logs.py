"""Run a data project and read back the event log its fixtures wrote.

Several acceptance suites answer the same shape of question — *how many times
was this fixture built, and in which process* — and reporter output cannot
answer it. They run oxitest as a subprocess against a checked-in project whose
fixtures append lines to a log, then assert on the log.

The scaffolding for that was copied four times before it moved here. The
copies had already drifted: three spelled ``running_pids`` identically and the
fourth read a different prefix, and one of them appended two runs to a single
log because its ``_run_project`` hard-coded the filename.

Line conventions this understands, both ``<prefix> <payload>``:

- ``SETUP <pid>-<n>`` / ``TEARDOWN <pid>-<n>`` — a PID-qualified instance id,
  so a rebuild inside one process is distinguishable from a build in another
- ``USE <label> <pid> <instance-id>`` — one per test that observed the fixture

A project that needs other prefixes uses :meth:`EventLogRun.lines` directly
rather than bending its log to fit these.
"""

from __future__ import annotations

__all__ = ["EventLogRun", "run_with_event_log"]

import os
from dataclasses import dataclass
from pathlib import Path

from oxitest import TempDir
from tests.helpers.runners import run_oxitest


@dataclass(frozen=True)
class EventLogRun:
    """One subprocess run, plus the log its fixtures wrote."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    def lines(self, prefix: str) -> list[str]:
        """Every event beginning with *prefix*, in the order recorded."""
        return [e for e in self.events if e.startswith(prefix)]

    @property
    def setups(self) -> tuple[str, ...]:
        """Every ``SETUP <pid>-<n>`` line, in order."""
        return tuple(self.lines("SETUP "))

    @property
    def teardowns(self) -> tuple[str, ...]:
        """Every ``TEARDOWN <pid>-<n>`` line, in order."""
        return tuple(self.lines("TEARDOWN "))

    @property
    def uses(self) -> tuple[str, ...]:
        """Every ``USE <label> <pid> <instance-id>`` line, in order."""
        return tuple(self.lines("USE "))

    @property
    def setup_pids(self) -> list[str]:
        """The PID of every SETUP, **duplicates kept** — that is the bug shape.

        A set would hide the defect these suites exist to catch: one process
        building a wide-lifetime fixture more than once.
        """
        return [e.split()[1].split("-")[0] for e in self.setups]

    @property
    def setup_ids(self) -> set[str]:
        """Distinct instance ids built."""
        return {e.split()[1] for e in self.setups}

    @property
    def teardown_ids(self) -> set[str]:
        """Distinct instance ids torn down."""
        return {e.split()[1] for e in self.teardowns}

    @property
    def running_pids(self) -> set[str]:
        """Every PID that actually ran a test. ``USE <label> <pid> <id>``."""
        return {e.split()[2] for e in self.uses}


def run_with_event_log(
    project: Path,
    tmp: TempDir,
    env_var: str,
    *args: str,
    log_name: str = "events.log",
) -> EventLogRun:
    """Run *project* as a subprocess with its event log pointed into *tmp*.

    *env_var* is the variable the project's fixtures read the log path from.

    *log_name* is not decoration: the fixtures **append**, so a test that runs
    the same project twice under one ``tmp`` would read the first run's events
    back as part of the second's and see every count doubled. Pass a distinct
    name per run.
    """
    log = Path(tmp) / log_name
    env = {**os.environ, env_var: str(log)}
    stdout, stderr, rc = run_oxitest(project, *args, env=env)
    events = tuple(log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()
    return EventLogRun(stdout=stdout, stderr=stderr, rc=rc, events=events)
