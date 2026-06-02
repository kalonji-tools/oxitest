"""Integration tests for ``oxitest run --collection-profile``."""

from __future__ import annotations

from conftest import helpers
from oxitest import TempDir


def _write_simple_project(tmp: TempDir) -> None:
    """Write a simple test project into tmp."""
    helpers.integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )


def test_collection_profile_output(tmp: TempDir):
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\n",
        },
    )
    _out, err, rc = helpers.common.run_oxitest(tmp, "--collection-profile")
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_contains(err, "Collection profile", "prescan:", "collection:")


def test_collection_profile_shows_slowest_files(tmp: TempDir):
    _write_simple_project(tmp)
    _out, err, rc = helpers.common.run_oxitest(tmp, "--collection-profile")
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_contains(err, "test_a.py")


def test_collection_profile_not_shown_by_default(tmp: TempDir):
    _write_simple_project(tmp)
    _out, err, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(_out, rc)
    helpers.integ.assert_excludes(err, "Collection profile")
