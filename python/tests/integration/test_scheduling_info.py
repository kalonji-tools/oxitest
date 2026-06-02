"""Integration tests for scheduling diagnostics with -v."""

from __future__ import annotations

from conftest import helpers
from oxitest import TempDir


def test_verbose_shows_scheduling_decision(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.common.run_oxitest(tmp, "-v")
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_contains(err, "scheduling:")


def test_verbose_shows_serial_reason(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.common.run_oxitest(tmp, "-v", "--serial")
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_contains(err, "serial")


def test_verbose_shows_strategy(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.common.run_oxitest(tmp, "-v")
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_contains(err, "strategy")


def test_no_scheduling_info_without_verbose(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )
    _out, err, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_excludes(err, "scheduling:")
