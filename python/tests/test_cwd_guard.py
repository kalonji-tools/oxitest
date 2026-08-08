"""The CWD-liveness guard: contains the #1957 cascade and names the offender."""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

import oxitest as oxi
from oxitest import Patcher, TempDir
from oxitest._bridge import _cwd_guard
from tests import helpers

#: Deletes the directory it is standing in, and reports whether that worked.
#: Run as a subprocess on purpose: if it succeeds it leaves its own working
#: directory deleted, which is precisely the hazard these tests are about, and
#: doing it inline would poison the worker running them.
_CWD_DELETE_PROBE = (
    "import os, sys, tempfile\n"
    "d = tempfile.mkdtemp()\n"
    "os.chdir(d)\n"
    "try:\n"
    "    os.rmdir(d)\n"
    "except OSError:\n"
    # Step out before retrying: the directory is undeletable *because* it is
    # this process's cwd, so without the chdir the probe leaks one temp
    # directory per worker on every run of the suite.
    "    os.chdir(tempfile.gettempdir())\n"
    "    os.rmdir(d)\n"
    "    sys.exit(1)\n"
    "sys.exit(0)\n"
)


@functools.cache
def _platform_can_delete_the_process_cwd() -> bool:
    """Whether this OS lets a process delete the directory it is sitting in.

    Measured rather than inferred from ``sys.platform``. Windows holds a handle
    on the working directory and refuses to unlink it, so the #1957 scenario —
    chdir into a directory, then delete it — cannot be constructed there, the
    directory survives, and the guard correctly reports nothing (#1989).

    Written as a capability probe so the skip expires by itself: if a future
    Windows ever permits it, these tests start running again without anyone
    remembering to delete a ``sys.platform`` check.
    """
    result = subprocess.run(
        [sys.executable, "-c", _CWD_DELETE_PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def _skip_unless_the_cwd_can_be_deleted() -> None:
    """Skip when the platform cannot construct the poisoning scenario at all."""
    if not _platform_can_delete_the_process_cwd():
        oxi.skip(
            "this platform refuses to delete a process's own working directory, "
            "so the poisoning scenario cannot be constructed and there is "
            "nothing for the guard to observe; the guard's own behaviour stays "
            "covered by test_restore_reports_when_the_captured_directory_is_gone"
        )


_PROJECT = Path(__file__).parent / "data" / "cwd_poison"

#: A project whose poisoning test *passes*. Without it, asserting on the exit
#: code is vacuous — ``cwd_poison``'s poisoner fails on its own assert, so
#: ``rc != 0`` holds with or without the guard (measured: that assertion passed
#: against an unguarded tree).
_QUIET_PROJECT = Path(__file__).parent / "data" / "cwd_poison_quiet"

#: A project whose *module-lifetime* fixture teardown deletes the working
#: directory. That drain runs outside ``run_test``, so the function-tier guard
#: cannot see it.
_WIDE_PROJECT = Path(__file__).parent / "data" / "cwd_poison_wide"


def test_the_victim_survives_a_poisoned_working_directory() -> None:
    """The guard restores the directory, so the next test's subprocess lives."""
    # Arrange
    _skip_unless_the_cwd_can_be_deleted()

    # Act
    out, _err, _rc = helpers.run_oxitest(_PROJECT, "--serial", "--warnings")

    # Assert
    assert "test_bbb_victim" not in out, (
        "the victim spawns a subprocess and must not inherit a deleted working "
        "directory — if it appears in the output at all it failed, which means "
        "the guard did not restore and the cascade is still live"
    )


def test_the_poisoning_test_is_named() -> None:
    """A diagnostic that does not identify the offender is not actionable."""
    # Arrange
    _skip_unless_the_cwd_can_be_deleted()

    # Act
    out, _err, _rc = helpers.run_oxitest(_PROJECT, "--serial", "--warnings")

    # Assert
    assert "test_aaa_poisoner left the worker's working directory deleted" in out, (
        "without the offending node id the next reader gets the same 200-failure "
        "haystack this issue started as, which is the whole cost being avoided"
    )


def test_a_quietly_poisoning_test_does_not_report_success() -> None:
    """An ERROR diagnostic alone exits 0 — measured — so the test must fail."""
    # Arrange
    _skip_unless_the_cwd_can_be_deleted()

    # Act
    out, _err, rc = helpers.run_oxitest(_QUIET_PROJECT, "--serial", "--warnings")

    # Assert
    assert rc != 0, (
        "a test that poisons the shared worker must not leave the run green; a "
        f"diagnostic without a status change is invisible by default\n{out}"
    )


def test_a_wide_tier_teardown_that_deletes_the_cwd_is_reported() -> None:
    """Module-tier and wider fixtures drain outside ``run_test``'s finally.

    There is no per-test result at that boundary, so this site reports and
    repairs but cannot fail a test — unlike the function-tier site.
    """
    # Arrange
    _skip_unless_the_cwd_can_be_deleted()

    # Act
    out, _err, _rc = helpers.run_oxitest(_WIDE_PROJECT, "--serial", "--warnings")

    # Assert
    assert "working directory deleted" in out, (
        "a wide-tier fixture teardown can delete the directory a test chdir'd "
        "into, and the drain loop is the only boundary that observes it; "
        "without a check there the next test is blamed, or nobody is"
    )


def test_restore_reports_when_the_captured_directory_is_gone(
    tmp: TempDir, patch: Patcher
) -> None:
    """``restore_cwd`` returns None rather than raising a second OSError.

    A guard that raises while handling a dead directory replaces one silent
    failure with a louder one at a worse moment.
    """
    # Arrange
    doomed = tmp.path / "doomed"
    doomed.mkdir()
    patch.setattr(_cwd_guard, "_SAFE_CWD", str(doomed))
    doomed.rmdir()

    # Act
    restored = _cwd_guard._restore_cwd()  # noqa: SLF001 — reaching the "nowhere to restore" branch through report_and_repair would need the real process cwd to be dead, which poisons the worker under parallel execution

    # Assert
    assert restored is None, (
        "with the captured directory deleted there is nowhere to restore to; "
        "returning None is what lets the caller report the situation instead "
        f"of raising from inside the guard, got {restored!r}"
    )
