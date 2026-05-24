"""Tests for fixture_lister module."""

from __future__ import annotations

from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry
from oxitest._bridge.fixture_lister import list_fixtures_from_registry

# All tests use use_color=False to avoid ANSI codes in assertions.


def _make_def(
    name: str,
    *,
    namespace: str = "",
    shared: bool = False,
    autouse: bool = False,
    is_async: bool = False,
    conftest_path: str = "",
    doc: str = "",
    params: list | None = None,
) -> FixtureDef:
    def _fn() -> None:
        pass

    _fn.__name__ = name
    _fn.__doc__ = doc
    _fn.__module__ = "conftest" if conftest_path else "oxitest._bridge._builtins"
    return FixtureDef(
        name=name,
        func=_fn,
        autouse=autouse,
        params=params,
        conftest_path=conftest_path,
        shared=shared,
        namespace=namespace,
        is_async=is_async,
    )


class TestListFixturesQuiet:
    """Verbosity 0 (quiet/minimal): name + tags in box."""

    def test_empty_registry_shows_builtins(self):
        reg = FixtureRegistry()
        result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
        assert "TempDir" in result, f"built-in TempDir missing: {result!r}"

    def test_filter_no_match_shows_message(self):
        reg = FixtureRegistry()
        result = list_fixtures_from_registry(
            reg, verbosity=0, pattern="zzz_no_match", use_color=False
        )
        assert "no fixtures match" in result, f"expected no-match message: {result!r}"

    def test_single_builtin(self):
        reg = FixtureRegistry()
        reg.register(_make_def("tmp_dir"))
        result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
        assert "tmp_dir" in result, f"fixture name missing: {result!r}"

    def test_filter_includes_match(self):
        reg = FixtureRegistry()
        reg.register(_make_def("tmp_dir"))
        reg.register(_make_def("db", conftest_path="conftest.py", namespace="myapp"))
        result = list_fixtures_from_registry(
            reg, verbosity=0, pattern="tmp", use_color=False
        )
        assert "tmp_dir" in result, "filtered fixture must appear"
        assert "db" not in result, "non-matching fixture must be excluded"

    def test_filter_excludes_non_match(self):
        reg = FixtureRegistry()
        reg.register(_make_def("tmp_dir"))
        result = list_fixtures_from_registry(
            reg, verbosity=0, pattern="xyz", use_color=False
        )
        assert "no fixtures match" in result, f"expected no-match message: {result!r}"

    def test_shared_tag_shown(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="c.py", shared=True))
        result = list_fixtures_from_registry(reg, verbosity=0, use_color=False)
        assert "shared" in result, f"shared tag missing: {result!r}"


class TestListFixturesStandard:
    """Verbosity 1 (standard/default): name, tags, first-line docstring."""

    def test_shows_docstring(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py", doc="Database conn."))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "Database conn." in result, f"docstring missing: {result!r}"

    def test_shows_shared_tag(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py", shared=True))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "shared" in result, f"shared tag missing: {result!r}"

    def test_docstring_pipe_prefix(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py", doc="A fixture."))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "│" in result, f"expected pipe prefix: {result!r}"


class TestListFixturesRich:
    """Verbosity 2 (rich): all metadata."""

    def test_shows_autouse(self):
        reg = FixtureRegistry()
        reg.register(_make_def("setup", conftest_path="conftest.py", autouse=True))
        result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
        assert "autouse" in result, f"autouse missing: {result!r}"

    def test_shows_async_tag(self):
        reg = FixtureRegistry()
        reg.register(_make_def("adb", conftest_path="conftest.py", is_async=True))
        result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
        assert "async" in result, f"async tag missing: {result!r}"

    def test_shows_params(self):
        reg = FixtureRegistry()
        reg.register(
            _make_def(
                "browser", conftest_path="conftest.py", params=["chrome", "firefox"]
            )
        )
        result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
        assert "chrome" in result, f"param 'chrome' missing: {result!r}"
        assert "firefox" in result, f"param 'firefox' missing: {result!r}"

    def test_shows_full_docstring(self):
        reg = FixtureRegistry()
        reg.register(
            _make_def("db", conftest_path="conftest.py", doc="Line one.\nLine two.")
        )
        result = list_fixtures_from_registry(reg, verbosity=2, use_color=False)
        assert "Line one." in result, f"first doc line missing: {result!r}"
        assert "Line two." in result, f"second doc line missing: {result!r}"


class TestBoxStructure:
    """Box-style headers and footers."""

    def test_has_box_top_and_bottom(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py"))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "╭─" in result, f"missing box top: {result!r}"
        assert "╰" in result, f"missing box bottom: {result!r}"

    def test_builtin_before_conftest(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py"))
        reg.register(_make_def("tmp_dir"))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        builtin_pos = result.find("tmp_dir")
        conftest_pos = result.find("db")
        assert builtin_pos < conftest_pos, (
            f"built-in must appear before conftest: {result!r}"
        )

    def test_namespace_grouping_within_origin(self):
        reg = FixtureRegistry()
        reg.register(_make_def("a_fix", conftest_path="conftest.py", namespace="alpha"))
        reg.register(_make_def("b_fix", conftest_path="conftest.py", namespace="beta"))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "alpha" in result, f"alpha namespace missing: {result!r}"
        assert "beta" in result, f"beta namespace missing: {result!r}"


class TestSummary:
    """Fixture count summary line."""

    def test_shows_total_count(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py"))
        reg.register(_make_def("cache", conftest_path="conftest.py"))
        result = list_fixtures_from_registry(reg, verbosity=1, use_color=False)
        assert "fixture" in result, f"count summary missing: {result!r}"

    def test_shows_filtered_count(self):
        reg = FixtureRegistry()
        reg.register(_make_def("db", conftest_path="conftest.py"))
        reg.register(_make_def("cache", conftest_path="conftest.py"))
        result = list_fixtures_from_registry(
            reg, verbosity=1, pattern="db", use_color=False
        )
        assert "of" in result, f"filtered count missing: {result!r}"
