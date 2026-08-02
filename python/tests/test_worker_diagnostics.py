"""Parallel workers must report fixture teardown failures like --serial does.

Regression coverage for #1840, where three defects in series meant a failing
teardown inside a worker reported nothing at all: the worker never wrote the
diagnostic to its pipe, the coordinator stopped reading before the worker's
tail, and anything that did arrive reached only ``tracing``.
"""

from __future__ import annotations

from pathlib import Path

from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_PROJECT = _TESTS_ROOT / "data" / "worker_diag_teardown"

#: Raised by the data-project's ``exploding`` fixture on teardown.
_MARKER = "TEARDOWN EXPLODED"

#: One teardown per module in the data-project.
_MODULE_COUNT = 6


def _teardown_reports(*extra_args: str) -> int:
    """Count teardown-failure reports in a run of the data-project."""
    stdout, stderr, _rc = helpers.run_oxitest(_PROJECT, "--warnings", *extra_args)
    return (stdout + stderr).count(_MARKER)


def test_serial_reports_every_teardown_failure() -> None:
    """Pins the baseline the parallel path is measured against."""
    # Act
    reported = _teardown_reports("--serial")

    # Assert
    assert reported == _MODULE_COUNT, (
        "the serial path is the reference for what a user should see; if this "
        "count moves, the parity assertion below is measuring the wrong thing"
    )


def test_parallel_reports_kept_tmp_notices() -> None:
    """The other payload #1840 loses: a NOTICE, not a teardown failure.

    Scope, stated precisely because it was measured rather than assumed: this
    pins the *sink*, not the tail window. A worker emits one ``end_task``
    per task group, and with more modules than workers most of those land in a
    non-final group, where the next group's drain still collects them. Disabling
    the tail read leaves this test green while the parity test below goes red.
    The tail window is that test's job; this one's is that NOTICE-severity
    diagnostics reach the reporter at all.
    """
    # Act
    stdout, stderr, _rc = helpers.run_oxitest(
        _PROJECT, "--warnings", "--keep-tmp=always", "-n", "2"
    )

    # Assert
    assert "KEPT" in (stdout + stderr), (
        "a --keep-tmp notice tells the user where preserved directories went; "
        "losing it in parallel leaves them with files on disk and no path"
    )


def test_parallel_reports_teardown_failures_like_serial() -> None:
    """The whole point of #1840: parallel must not silently swallow teardowns."""
    # Act
    parallel = _teardown_reports("-n", "2")
    serial = _teardown_reports("--serial")

    # Assert
    assert parallel == serial, (
        f"a worker's teardown failure must reach the user exactly as the "
        f"serial path's does; got {parallel} in parallel vs {serial} serial. "
        f"A shortfall of one-per-worker means the output tail is being "
        f"discarded; zero means diagnostics never reach the pipe at all"
    )
