"""Detect and repair a working directory deleted out from under a worker.

Tests inside one worker share a process, and ``os.chdir`` is process-global.
If a test leaves the working directory pointing at a directory that is then
deleted, every subprocess spawned afterwards in that worker dies during
interpreter startup — ``coverage`` calls ``os.path.abspath(os.curdir)`` at
import, which raises ``FileNotFoundError`` on a removed directory (#1957).

The predicate is **liveness, not change**: with up to 78 tests in flight per
worker, a test legitimately holding a ``patch.chdir`` into a live directory
must never be blamed. Asking "did the directory change?" blamed innocent
concurrent tests in 5 of 6 measured trials; asking "is it still there?"
blamed none and still caught the poisoning.
"""

from __future__ import annotations

__all__ = ["report_and_repair"]

import os
from pathlib import Path

from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge.result import DiagnosticSeverity


def _capture_safe_cwd() -> str | None:
    """Record where the process started, or None if that is already gone.

    ``Path.cwd()`` raises when the working directory has been deleted, so a
    bare call at module scope would make *this* module the crash site in the
    one situation it exists to handle. Today something else in the import
    chain raises first, which masks it — that is a coincidence, not a design.
    """
    try:
        return str(Path.cwd())
    except OSError:
        return None


# Captured at import, before any test runs. This is the only restore target
# reachable from both the serial PyO3 path and the parallel worker path --
# `rootdir` reaches only worker.py, and the serial path never goes through it.
_SAFE_CWD = _capture_safe_cwd()


def _restore_cwd() -> str | None:
    """Move back to the directory captured at import, or None if it is gone.

    Split out from :func:`report_and_repair` so the "nowhere to restore to"
    branch is testable without making the real process directory dead —
    doing that under parallel execution would poison the worker, which is
    the defect this module exists to catch.
    """
    if _SAFE_CWD is None:
        return None
    try:
        os.chdir(_SAFE_CWD)
    except OSError:
        return None
    return _SAFE_CWD


def report_and_repair(subject: str) -> str | None:
    """Repair and report a dead working directory; return the message, or None.

    ``None`` means the directory is alive and nothing happened — the common
    case, and the one every caller checks first. Otherwise the directory is
    restored, an ``ERROR`` diagnostic is emitted, and the message is returned
    so a caller that owns a test result can reuse it verbatim.

    *subject* names whatever left the directory deleted: a test's node id at
    the function tier, or a description of the boundary at wider tiers.
    """
    try:
        Path.cwd()
    except OSError:
        pass
    else:
        return None

    # Reported rather than raised: a guard whose whole purpose is to survive
    # one OSError must not raise a second one from inside itself.
    restored = _restore_cwd()
    where = (
        f" Restored to {restored}."
        if restored
        else (
            " Nothing was restored — the directory this process started in is"
            " gone too, so every subprocess from here on will die at import."
        )
    )

    msg = (
        f"{subject} left the worker's working directory deleted.{where}"
        " Tests in a worker share one process, so this would have killed every"
        " subprocess spawned afterwards."
    )
    emit_diagnostic(DiagnosticSeverity.ERROR, "cwd", msg)
    return msg
