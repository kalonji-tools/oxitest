"""Tests for conftest discovery, loading, fixture extraction, and session creation."""

from __future__ import annotations

import sys
import textwrap
import types

import oxitest
from oxitest import Fixture, TempDir, Yields, helpers, raises
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._fixture_registry import ConftestSource
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._helper_registry import HelperRegistry
from oxitest._bridge.conftest_loader import (
    _extract_depends_on,
    _extract_fixture_type,
    _extract_helpers,
    _load_conftest_module,
    create_conftest_fixtures,
    create_session,
    find_conftest_paths,
    load_fixtures_from_conftest,
)
from oxitest._bridge.result import Diagnostic

# ── find_conftest_paths ───────────────────────────────────────────────────────


def test_find_conftest_no_conftest_files(tmp: TempDir) -> None:
    """When no conftest.py exists on the path, an empty list is returned."""
    (tmp / "tests").mkdir()
    result = find_conftest_paths(str(tmp / "tests" / "test_foo.py"), str(tmp))
    assert result == [], (
        f"expected no conftest paths when no conftest.py exists, got {result}"
    )


def test_find_conftest_root_only(tmp: TempDir) -> None:
    """A conftest.py at the rootdir is found even when the test is in a subdirectory."""
    (tmp / "conftest.py").write_text("")
    (tmp / "tests").mkdir()
    result = find_conftest_paths(str(tmp / "tests" / "test_foo.py"), str(tmp))
    assert result == [str(tmp / "conftest.py")], (
        f"expected only root conftest.py, got {result}"
    )


def test_find_conftest_root_and_nested(tmp: TempDir) -> None:
    """Both root and nested conftest.py files are returned in the correct order."""
    (tmp / "conftest.py").write_text("")
    tests_dir = tmp / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text("")
    result = find_conftest_paths(str(tests_dir / "test_foo.py"), str(tmp))
    assert result == [
        str(tmp / "conftest.py"),
        str(tests_dir / "conftest.py"),
    ], f"expected root and nested conftest paths in order, got {result}"


def test_find_conftest_root_first_order(tmp: TempDir) -> None:
    """Conftests are returned root-first."""
    a = tmp / "conftest.py"
    a.write_text("")
    sub = tmp / "sub"
    sub.mkdir()
    b = sub / "conftest.py"
    b.write_text("")
    result = find_conftest_paths(str(sub / "test_x.py"), str(tmp))
    assert result.index(str(a)) < result.index(str(b)), (
        f"root conftest should appear before nested conftest, got order: {result}"
    )


def test_find_conftest_test_outside_rootdir_returns_empty(tmp: TempDir) -> None:
    """A test file outside the rootdir yields no conftest paths."""
    other = tmp / "other"
    other.mkdir()
    result = find_conftest_paths(
        str(other / "test_foo.py"),
        str(tmp / "project"),
    )
    assert result == [], (
        f"test outside rootdir should yield no conftest paths, got {result}"
    )


# ── load_fixtures_from_conftest ───────────────────────────────────────────────


def test_load_fixtures_extracts_from_fixtures_instance(tmp: TempDir) -> None:
    """Only @fixtures.fixture-decorated functions are extracted, not plain functions."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def db():
            return "connection"

        @fixtures.fixture
        def app():
            return "app"

        def not_a_fixture():
            pass
    """)
    )
    result = load_fixtures_from_conftest(str(f))
    names = {d.name for d in result}
    assert names == {"db", "app"}, (
        f"expected fixtures {{'db', 'app'}} to be discovered, got {names}"
    )


def test_load_fixtures_name_override(tmp: TempDir) -> None:
    """@fixtures.fixture(name=...) overrides the function name used for the fixture."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture(name="renamed")
        def original():
            pass
    """)
    )
    result = load_fixtures_from_conftest(str(f))
    assert result[0].name == "renamed", (
        f"fixture name override should produce 'renamed', got {result[0].name!r}"
    )


def test_load_fixtures_autouse(tmp: TempDir) -> None:
    """@fixtures.fixture(autouse=True) sets autouse=True on the FixtureDef."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture(autouse=True)
        def setup():
            pass
    """)
    )
    result = load_fixtures_from_conftest(str(f))
    assert result[0].autouse is True, (
        f"fixture declared with autouse=True should have autouse=True, got "
        f"{result[0].autouse!r}"
    )


def test_load_fixtures_conftest_path_recorded(tmp: TempDir) -> None:
    """Each FixtureDef records the path of the conftest.py it was loaded from."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def x():
            pass
    """)
    )
    result = load_fixtures_from_conftest(str(f))
    assert result[0].conftest_path == str(f), (
        f"fixture conftest_path should be {str(f)!r}, got {result[0].conftest_path!r}"
    )


def test_load_fixtures_multiple_instances(tmp: TempDir) -> None:
    """Multiple Fixtures() instances in one conftest are all discovered."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        db_fixtures = oxitest.Fixtures()
        web_fixtures = oxitest.Fixtures()

        @db_fixtures.fixture
        def db():
            pass

        @web_fixtures.fixture
        def client():
            pass
    """)
    )
    result = load_fixtures_from_conftest(str(f))
    names = {d.name for d in result}
    assert names == {"db", "client"}, (
        f"expected {{'db', 'client'}} from multiple Fixtures() instances, got {names}"
    )


# ── create_session ────────────────────────────────────────────────────────────


def test_create_session_empty_returns_session() -> None:
    """create_session([]) returns an empty FixtureSession with no violations."""
    session, violations, _diags = create_session([])
    assert isinstance(session, FixtureSession), (
        f"create_session([]) should return a FixtureSession, got "
        f"{type(session).__name__}"
    )
    assert violations == [], (
        f"create_session([]) should return no violations, got {len(violations)}"
    )


def test_create_session_populates_registry(tmp: TempDir) -> None:
    """Fixtures in conftest are resolvable via the session after create_session."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def db():
            return 42
    """)
    )
    session, _, _diags = create_session([str(f)])

    def fn(db: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["db"] == 42, (
        f"fixture 'db' should resolve to 42 after loading conftest, got "
        f"{kwargs.get('db')!r}"
    )


def test_create_session_later_conftest_overrides_earlier(tmp: TempDir) -> None:
    """A sub-directory conftest fixture overrides same-named root conftest fixture."""
    root_conf = tmp / "conftest.py"
    root_conf.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def val():
            return "root"
    """)
    )
    sub = tmp / "sub"
    sub.mkdir()
    sub_conf = sub / "conftest.py"
    sub_conf.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def val():
            return "local"
    """)
    )
    session, _, _diags = create_session([str(root_conf), str(sub_conf)])

    def fn(val: Fixture[str]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        fn, helpers.common.make_meta(str(sub / "test_x.py"))
    )
    assert kwargs["val"] == "local", (
        f"more-local conftest fixture should override root conftest, got "
        f"{kwargs.get('val')!r}"
    )


@oxitest.arrange("_clean_sys_modules")
@oxitest.mark.inprocess
def test_load_fixtures_registers_conftest_in_sys_modules(tmp: TempDir) -> None:
    """load_fixtures_from_conftest registers the module as sys.modules['conftest']."""
    f = tmp / "conftest.py"
    f.write_text(
        textwrap.dedent("""\
        import oxitest

        fixtures = oxitest.Fixtures()

        @fixtures.fixture
        def my_fixture():
            return 42
    """)
    )
    sys.modules.pop("conftest", None)
    load_fixtures_from_conftest(str(f))
    assert "conftest" in sys.modules, (
        "load_fixtures_from_conftest should register the module under "
        "sys.modules['conftest']"
    )
    assert hasattr(sys.modules["conftest"], "my_fixture"), (
        "sys.modules['conftest'] should expose the 'my_fixture' function"
    )


# ── Namespace stamping ────────────────────────────────────────────────────────


def test_load_fixtures_stamps_namespace_from_variable_name(tmp: TempDir) -> None:
    """The variable name of a Fixtures() instance is used as the fixture namespace."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "@db.fixture\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )
    defs = load_fixtures_from_conftest(str(conftest))
    assert len(defs) == 1, f"expected 1 fixture definition, got {len(defs)}"
    assert defs[0].namespace == "db", (
        f"fixture namespace should be 'db' (from variable name), got "
        f"{defs[0].namespace!r}"
    )


def test_load_fixtures_explicit_name_overrides_variable_name(tmp: TempDir) -> None:
    """Fixtures(name='...') overrides the variable name when stamping the namespace."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures(name='db')\n"
        "@fixtures.fixture\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )
    defs = load_fixtures_from_conftest(str(conftest))
    assert defs[0].namespace == "db", (
        f"explicit name='db' should override variable name for namespace, got "
        f"{defs[0].namespace!r}"
    )


def test_load_fixtures_sets_namespace_on_fixture_def(tmp: TempDir) -> None:
    """The namespace is stored on FixtureDef, not as a private function attribute."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "@db.fixture\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )
    defs = load_fixtures_from_conftest(str(conftest))
    assert defs[0].namespace == "db", (
        f"FixtureDef.namespace should be 'db', got {defs[0].namespace!r}"
    )
    # Ensure the function is NOT stamped with a private attribute
    assert not hasattr(defs[0].func, "_oxitest_namespace"), (
        "fixture function should not have _oxitest_namespace stamped"
    )


def test_load_fixtures_raises_on_reserved_name_oxi_variable(tmp: TempDir) -> None:
    """Using 'oxi' as the Fixtures() variable name raises ValueError (reserved)."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\noxi = oxitest.Fixtures()\n")
    with raises(ValueError, match="reserved"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_raises_on_explicit_name_oxi(tmp: TempDir) -> None:
    """Fixtures(name='oxi') raises ValueError because 'oxi' is a reserved namespace."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='oxi')\n")
    with raises(ValueError, match="reserved"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_keyword_namespace_variable(tmp: TempDir) -> None:
    """Fixtures(name='class') raises ValueError: keywords disallowed as namespaces."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nclass_ = oxitest.Fixtures()\n")
    # variable name "class_" is fine, but "class" would be a syntax error
    # so test explicit name= instead
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='class')\n")
    with raises(ValueError, match="Python keyword"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_builtin_namespace_name(tmp: TempDir) -> None:
    """Using a builtin name (like 'int') as a Fixtures() variable raises ValueError."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nint = oxitest.Fixtures()\n")
    with raises(ValueError, match="Python builtin"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_builtin_explicit_name(tmp: TempDir) -> None:
    """Fixtures(name='list') raises ValueError because builtin names are disallowed."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='list')\n")
    with raises(ValueError, match="Python builtin"):
        load_fixtures_from_conftest(str(conftest))


# ── Helpers integration ──────────────────────────────────────────────────────


def test_create_conftest_fixtures_returns_helper_registry(tmp: TempDir) -> None:
    """create_conftest_fixtures returns a HelperRegistry as its third element."""
    f = tmp / "conftest.py"
    f.write_text(
        "from oxitest import Helpers\n"
        "utils = Helpers()\n"
        "@utils.helper\n"
        "def make_thing():\n"
        "    return 'thing'\n"
    )
    _defs, _violations, helper_registry = create_conftest_fixtures([str(f)])
    assert isinstance(helper_registry, HelperRegistry), (
        "third return value should be a HelperRegistry, got "
        f"{type(helper_registry).__name__}"
    )
    assert "make_thing" in helper_registry, (
        "helper_registry should contain 'make_thing'"
    )


def test_create_session_stores_helper_registry(tmp: TempDir) -> None:
    """create_session should store HelperRegistry on the session."""
    f = tmp / "conftest.py"
    f.write_text(
        "from oxitest import Helpers\n"
        "utils = Helpers()\n"
        "@utils.helper\n"
        "def make_thing():\n"
        "    return 'thing'\n"
    )
    session, _, _diags = create_session([str(f)])
    assert hasattr(session, "helper_registry"), (
        "session should have a helper_registry property after create_session"
    )
    assert isinstance(session.helper_registry, HelperRegistry), (
        "helper_registry should be a HelperRegistry, got "
        f"{type(session.helper_registry).__name__}"
    )


def test_create_session_helpers_only_conftest_no_fixtures_no_diagnostic(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """A conftest with only Helpers() (no Fixtures) should not emit a diagnostic."""
    f = tmp / "conftest.py"
    f.write_text(
        "from oxitest import Helpers\n"
        "utils = Helpers()\n"
        "@utils.helper\n"
        "def make_thing():\n"
        "    return 'thing'\n"
    )
    create_session([str(f)])
    conftest_diags = [d for d in diag_collector if d.context == "conftest loading"]
    assert conftest_diags == [], (
        "conftest with only Helpers() should not emit conftest loading diagnostics,"
        f" got {conftest_diags}"
    )


def test_create_session_empty_conftest_still_warns(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """A conftest with NO fixtures AND NO helpers should still emit a diagnostic."""
    f = tmp / "conftest.py"
    f.write_text("")
    create_session([str(f)])
    assert any(
        d.context == "conftest loading" and "no Fixtures instance" in d.message
        for d in diag_collector
    ), (
        "empty conftest must emit a 'conftest loading' diagnostic about missing"
        f" Fixtures instance: {diag_collector}"
    )


def test_create_session_helper_registry_empty_when_no_helpers(tmp: TempDir) -> None:
    """helper_registry is empty when conftest has only fixtures."""
    f = tmp / "conftest.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def db():\n"
        "    return 42\n"
    )
    session, _, _diags = create_session([str(f)])
    assert hasattr(session, "helper_registry"), (
        "session should have helper_registry even with no Helpers() instances"
    )
    registry = session.helper_registry
    assert isinstance(registry, HelperRegistry), (
        "session.helper_registry should be a HelperRegistry"
    )
    assert list(registry) == [], (
        "helper_registry should be empty when conftest has no Helpers() instances"
    )


def test_create_session_pre_session_diagnostics_in_returned_tuple(
    tmp: TempDir,
) -> None:
    """Diagnostics emitted during conftest loading land in the returned tuple.

    This tests the production path where no ContextVar collector is active
    before create_session — a temporary collector captures conftest-loading
    diagnostics and transfers them into session.diagnostics.
    """
    # Clear any active collector to simulate the production path
    token = _diagnostic_collector_var.set(None)
    try:
        f = tmp / "conftest.py"
        f.write_text("")  # empty conftest → emits a NOTICE diagnostic
        _session, _violations, diagnostics = create_session([str(f)])
    finally:
        _diagnostic_collector_var.reset(token)

    assert any(
        d.context == "conftest loading" and "no Fixtures instance" in d.message
        for d in diagnostics
    ), (
        "pre-session diagnostics must be captured by the temporary collector"
        " and returned in the SessionResult tuple so the Rust bridge can"
        f" drain them: {diagnostics}"
    )


# ── _extract_fixture_type and _extract_depends_on ─────────────────────────────


class Connection:
    """Dummy connection type for fixture type-extraction tests."""


class DBSession:
    """Dummy session type for fixture type-extraction tests."""


def test_extract_fixture_type_from_return() -> None:
    """Return annotation directly becomes the binding type."""

    def my_fixture() -> DBSession:
        raise NotImplementedError

    assert _extract_fixture_type(my_fixture) is DBSession, (
        "should extract DBSession from return annotation"
    )


def test_extract_fixture_type_from_yields() -> None:
    """Yields[T] unwraps to T."""

    def my_fixture() -> Yields[DBSession]:
        raise NotImplementedError

    assert _extract_fixture_type(my_fixture) is DBSession, (
        "should unwrap Yields[DBSession] to DBSession"
    )


def test_extract_fixture_type_missing_raises() -> None:
    """No return annotation raises an error."""

    def my_fixture():  # noqa: ANN202 — intentionally unannotated to test error path
        pass

    with raises(Exception, match="return"):
        _extract_fixture_type(my_fixture)


def test_extract_depends_on() -> None:
    """Fixture[T] params produce (qualifier, binding_type) tuples."""

    def my_fixture(conn: Fixture[Connection], x: int) -> DBSession:
        raise NotImplementedError

    deps = _extract_depends_on(my_fixture)
    assert deps == (("conn", Connection),), (
        "should extract conn: Fixture[Connection] as a dependency, ignoring plain int"
    )


def test_extract_depends_on_skips_unannotated() -> None:
    """Unannotated parameters are not included in depends_on."""

    def my_fixture(db, x: int) -> DBSession:  # noqa: ANN001 — intentionally unannotated to test skip behavior
        raise NotImplementedError

    deps = _extract_depends_on(my_fixture)
    assert deps == (), (
        "unannotated and non-Fixture params should not appear in depends_on"
    )


# ── _extract_helpers ──────────────────────────────────────────────────────────


def test_extract_helpers_from_conftest(tmp: TempDir) -> None:
    """_extract_helpers should find Helpers() instances and extract defs."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "from oxitest import Helpers\n"
        "utils = Helpers()\n"
        "@utils.helper\n"
        "def greet(name: str) -> str:\n"
        "    return f'hi {name}'\n"
    )
    module = _load_conftest_module(str(conftest))
    assert module is not None, "_load_conftest_module should return a module"
    defs = _extract_helpers(module, str(conftest))
    assert len(defs) == 1, "should extract one helper"
    assert defs[0].name == "greet", "helper name should match function name"
    assert defs[0].namespace == "utils", "namespace should come from variable name"
    assert isinstance(defs[0].source, ConftestSource), (
        "source should be a ConftestSource after stamping"
    )
    assert defs[0].source.conftest_path == str(conftest), (
        "conftest_path should be stamped"
    )


# ── _load_conftest_module: sys.modules cleanup on failure ─────────────────────


@oxitest.arrange("_clean_sys_modules")
@oxitest.mark.inprocess
def test_load_conftest_module_cleans_sys_modules_on_exec_failure(
    tmp: TempDir,
) -> None:
    """exec_module failure cleans unique key but preserves previous conftest."""
    # Arrange: conftest that raises at module level
    conftest = tmp / "conftest.py"
    conftest.write_text("raise RuntimeError('boom')\n")
    unique_name = f"_oxitest_conftest_{abs(hash(str(conftest)))}"
    # Simulate a previously loaded conftest still aliased in sys.modules
    previous_conftest = types.ModuleType("_oxitest_conftest_previous")
    sys.modules.pop(unique_name, None)
    sys.modules["conftest"] = previous_conftest

    # Act + Assert: exception propagates
    with raises(RuntimeError, match="boom"):
        _load_conftest_module(str(conftest))

    # Assert: unique key cleaned, previous conftest alias preserved
    assert unique_name not in sys.modules, (
        "unique module name should be removed from sys.modules after exec failure; "
        "a poisoned entry would cause confusing import errors on retry"
    )
    assert sys.modules.get("conftest") is previous_conftest, (
        "a failing conftest must not remove a previously loaded conftest's alias; "
        "sys.modules['conftest'] was never set to the failing module"
    )
