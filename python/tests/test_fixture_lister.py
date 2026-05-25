"""Tests for fixture_lister module."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_fixture_def
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge.fixture_lister import list_fixtures_from_registry

# All tests use use_color=False to avoid ANSI codes in assertions.


# Verbosity 0 (quiet/minimal): name + tags in box.


def test_quiet_empty_registry_shows_builtins():
    reg = FixtureRegistry()
    result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "TempDir" in result, f"built-in TempDir missing: {result!r}"


def test_quiet_filter_no_match_shows_message():
    reg = FixtureRegistry()
    result = list_fixtures_from_registry(
        reg, verbosity=0, pattern="zzz_no_match", use_color=False
    )
    assert "no fixtures match" in result, f"expected no-match message: {result!r}"


def test_quiet_single_builtin():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("tmp_dir"))
    result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "tmp_dir" in result, f"fixture name missing: {result!r}"


def test_quiet_filter_includes_match():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("tmp_dir"))
    reg.register(make_fixture_def("db", conftest_path="conftest.py", namespace="myapp"))
    result = list_fixtures_from_registry(
        reg, verbosity=0, pattern="tmp", use_color=False
    )
    assert "tmp_dir" in result, "filtered fixture must appear"
    assert "db" not in result, "non-matching fixture must be excluded"


def test_quiet_filter_excludes_non_match():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("tmp_dir"))
    result = list_fixtures_from_registry(
        reg, verbosity=0, pattern="xyz", use_color=False
    )
    assert "no fixtures match" in result, f"expected no-match message: {result!r}"


def test_quiet_shared_tag_shown():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="c.py", shared=True))
    result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
    assert "shared" in result, f"shared tag missing: {result!r}"


# Verbosity 1 (standard/default): name, tags, first-line docstring.


def test_standard_shows_docstring():
    reg = FixtureRegistry()
    reg.register(
        make_fixture_def("db", conftest_path="conftest.py", doc="Database conn.")
    )
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "Database conn." in result, f"docstring missing: {result!r}"


def test_standard_shows_shared_tag():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py", shared=True))
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "shared" in result, f"shared tag missing: {result!r}"


def test_standard_docstring_pipe_prefix():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py", doc="A fixture."))
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "│" in result, f"expected pipe prefix: {result!r}"


# Verbosity 2 (rich): all metadata.


def test_rich_shows_autouse():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("setup", conftest_path="conftest.py", autouse=True))
    result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "autouse" in result, f"autouse missing: {result!r}"


def test_rich_shows_async_tag():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("adb", conftest_path="conftest.py", is_async=True))
    result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "async" in result, f"async tag missing: {result!r}"


def test_rich_shows_params():
    reg = FixtureRegistry()
    reg.register(
        make_fixture_def(
            "browser",
            conftest_path="conftest.py",
            params=["chrome", "firefox"],
        )
    )
    result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "chrome" in result, f"param 'chrome' missing: {result!r}"
    assert "firefox" in result, f"param 'firefox' missing: {result!r}"


def test_rich_shows_full_docstring():
    reg = FixtureRegistry()
    reg.register(
        make_fixture_def("db", conftest_path="conftest.py", doc="Line one.\nLine two.")
    )
    result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
    assert "Line one." in result, f"first doc line missing: {result!r}"
    assert "Line two." in result, f"second doc line missing: {result!r}"


# Box-style headers and footers.


def test_box_has_box_top_and_bottom():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py"))
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "╭─" in result, f"missing box top: {result!r}"
    assert "╰" in result, f"missing box bottom: {result!r}"


def test_box_builtin_before_conftest():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py"))
    reg.register(make_fixture_def("tmp_dir"))
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    builtin_pos = result.find("tmp_dir")
    conftest_pos = result.find("db")
    assert builtin_pos < conftest_pos, (
        f"built-in must appear before conftest: {result!r}"
    )


def test_box_namespace_grouping_within_origin():
    reg = FixtureRegistry()
    reg.register(
        make_fixture_def("a_fix", conftest_path="conftest.py", namespace="alpha")
    )
    reg.register(
        make_fixture_def("b_fix", conftest_path="conftest.py", namespace="beta")
    )
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "alpha" in result, f"alpha namespace missing: {result!r}"
    assert "beta" in result, f"beta namespace missing: {result!r}"


# Fixture count summary line.


def test_summary_shows_total_count():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py"))
    reg.register(make_fixture_def("cache", conftest_path="conftest.py"))
    result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
    assert "fixture" in result, f"count summary missing: {result!r}"


def test_summary_shows_filtered_count():
    reg = FixtureRegistry()
    reg.register(make_fixture_def("db", conftest_path="conftest.py"))
    reg.register(make_fixture_def("cache", conftest_path="conftest.py"))
    result = list_fixtures_from_registry(
        reg, verbosity=1, pattern="db", use_color=False
    )
    assert "of" in result, f"filtered count missing: {result!r}"
