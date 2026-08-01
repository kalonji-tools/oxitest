"""Integration tests for scheduling diagnostics with -v."""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_verbose_shows_scheduling_decision(tmp: TempDir) -> None:
    """-v should emit a 'scheduling:' line explaining how tests are dispatched."""
    integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.run_oxitest(tmp, "-v")
    integ.assert_passed(_out, rc)
    integ.assert_contains(err, "scheduling:")


def test_verbose_shows_serial_reason(tmp: TempDir) -> None:
    """-v --serial should mention 'serial' in the scheduling diagnostic output."""
    integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.run_oxitest(tmp, "-v", "--serial")
    integ.assert_passed(_out, rc)
    integ.assert_contains(err, "serial")


def test_verbose_shows_strategy(tmp: TempDir) -> None:
    """-v should include the scheduling strategy name in the diagnostic output."""
    integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.run_oxitest(tmp, "-v")
    integ.assert_passed(_out, rc)
    integ.assert_contains(err, "strategy")


def test_no_scheduling_info_without_verbose(tmp: TempDir) -> None:
    """Without -v, the scheduling diagnostic section should not appear."""
    integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(_out, rc)
    integ.assert_excludes(err, "scheduling:")
