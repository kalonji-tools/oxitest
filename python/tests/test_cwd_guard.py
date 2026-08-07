"""The CWD-liveness guard: contains the #1957 cascade and names the offender."""

from __future__ import annotations

from pathlib import Path

from oxitest import Patcher, TempDir
from oxitest._bridge import _cwd_guard
from tests import helpers

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
    # Arrange / Act
    out, _err, _rc = helpers.run_oxitest(_PROJECT, "--serial", "--warnings")

    # Assert
    assert "test_bbb_victim" not in out, (
        "the victim spawns a subprocess and must not inherit a deleted working "
        "directory — if it appears in the output at all it failed, which means "
        "the guard did not restore and the cascade is still live"
    )


def test_the_poisoning_test_is_named() -> None:
    """A diagnostic that does not identify the offender is not actionable."""
    # Arrange / Act
    out, _err, _rc = helpers.run_oxitest(_PROJECT, "--serial", "--warnings")

    # Assert
    assert "test_aaa_poisoner left the worker's working directory deleted" in out, (
        "without the offending node id the next reader gets the same 200-failure "
        "haystack this issue started as, which is the whole cost being avoided"
    )


def test_a_quietly_poisoning_test_does_not_report_success() -> None:
    """An ERROR diagnostic alone exits 0 — measured — so the test must fail."""
    # Arrange / Act
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
    # Arrange / Act
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
