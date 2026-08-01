"""Integration tests for ``oxitest run --collection-profile``."""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def _write_simple_project(tmp: TempDir) -> None:
    """Write a simple test project into tmp."""
    integ.write_project(
        tmp,
        tests={"test_a.py": "def test_one(): pass\n"},
    )


def test_collection_profile_output(tmp: TempDir) -> None:
    """--collection-profile should output prescan and collection timing sections."""
    integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\n",
        },
    )
    _out, err, rc = helpers.run_oxitest(tmp, "--collection-profile")
    integ.assert_passed(_out, rc)
    integ.assert_contains(err, "Collection profile", "prescan:", "collection:")


def test_collection_profile_shows_slowest_files(tmp: TempDir) -> None:
    """--collection-profile output should list individual test file names."""
    _write_simple_project(tmp)
    _out, err, rc = helpers.run_oxitest(tmp, "--collection-profile")
    integ.assert_passed(_out, rc)
    integ.assert_contains(err, "test_a.py")


def test_collection_profile_not_shown_by_default(tmp: TempDir) -> None:
    """Without --collection-profile, the profile section should not appear in output."""
    _write_simple_project(tmp)
    _out, err, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(_out, rc)
    integ.assert_excludes(err, "Collection profile")
