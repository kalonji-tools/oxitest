from __future__ import annotations

import sys
import textwrap

import oxitest
from conftest import helpers
from oxitest import Fixture, TempDir, raises, warns
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._helper_namespace import HelperNamespace
from oxitest._bridge.conftest_loader import (
    create_session,
    find_conftest_paths,
    load_fixtures_from_conftest,
)

# ── find_conftest_paths ───────────────────────────────────────────────────────


def test_find_conftest_no_conftest_files(tmp: TempDir):
    (tmp / "tests").mkdir()
    result = find_conftest_paths(str(tmp / "tests" / "test_foo.py"), str(tmp))
    assert result == [], (
        f"expected no conftest paths when no conftest.py exists, got {result}"
    )


def test_find_conftest_root_only(tmp: TempDir):
    (tmp / "conftest.py").write_text("")
    (tmp / "tests").mkdir()
    result = find_conftest_paths(str(tmp / "tests" / "test_foo.py"), str(tmp))
    assert result == [str(tmp / "conftest.py")], (
        f"expected only root conftest.py, got {result}"
    )


def test_find_conftest_root_and_nested(tmp: TempDir):
    (tmp / "conftest.py").write_text("")
    tests_dir = tmp / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text("")
    result = find_conftest_paths(str(tests_dir / "test_foo.py"), str(tmp))
    assert result == [
        str(tmp / "conftest.py"),
        str(tests_dir / "conftest.py"),
    ], f"expected root and nested conftest paths in order, got {result}"


def test_find_conftest_root_first_order(tmp: TempDir):
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


def test_find_conftest_test_outside_rootdir_returns_empty(tmp: TempDir):
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


def test_load_fixtures_empty_conftest_warns(tmp: TempDir):
    f = tmp / "conftest.py"
    f.write_text("")
    with warns(UserWarning, match="no Fixtures instance"):
        create_session([str(f)])


def test_load_fixtures_extracts_from_fixtures_instance(tmp: TempDir):
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


def test_load_fixtures_name_override(tmp: TempDir):
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


def test_load_fixtures_autouse(tmp: TempDir):
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


def test_load_fixtures_conftest_path_recorded(tmp: TempDir):
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


def test_load_fixtures_multiple_instances(tmp: TempDir):
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


def test_create_session_empty_returns_session():
    session, violations = create_session([])
    assert isinstance(session, FixtureSession), (
        f"create_session([]) should return a FixtureSession, got "
        f"{type(session).__name__}"
    )
    assert violations == [], (
        f"create_session([]) should return no violations, got {len(violations)}"
    )


def test_create_session_populates_registry(tmp: TempDir):
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
    session, _ = create_session([str(f)])

    def fn(db: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["db"] == 42, (
        f"fixture 'db' should resolve to 42 after loading conftest, got "
        f"{kwargs.get('db')!r}"
    )


def test_create_session_later_conftest_overrides_earlier(tmp: TempDir):
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
    session, _ = create_session([str(root_conf), str(sub_conf)])

    def fn(val: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(
        fn, helpers.common.make_meta(str(sub / "test_x.py"))
    )
    assert kwargs["val"] == "local", (
        f"more-local conftest fixture should override root conftest, got "
        f"{kwargs.get('val')!r}"
    )


@oxitest.mark.inprocess
def test_load_fixtures_registers_conftest_in_sys_modules(
    tmp: TempDir, clean_sys_modules: Fixture[None]
):
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


def test_load_fixtures_stamps_namespace_from_variable_name(tmp: TempDir):
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


def test_load_fixtures_explicit_name_overrides_variable_name(tmp: TempDir):
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


def test_load_fixtures_sets_namespace_on_fixture_def(tmp: TempDir):
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


def test_load_fixtures_raises_on_reserved_name_oxi_variable(tmp: TempDir):
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\noxi = oxitest.Fixtures()\n")
    with raises(ValueError, match="reserved"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_raises_on_explicit_name_oxi(tmp: TempDir):
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='oxi')\n")
    with raises(ValueError, match="reserved"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_keyword_namespace_variable(tmp: TempDir):
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nclass_ = oxitest.Fixtures()\n")
    # variable name "class_" is fine, but "class" would be a syntax error
    # so test explicit name= instead
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='class')\n")
    with raises(ValueError, match="Python keyword"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_builtin_namespace_name(tmp: TempDir):
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nint = oxitest.Fixtures()\n")
    with raises(ValueError, match="Python builtin"):
        load_fixtures_from_conftest(str(conftest))


def test_load_fixtures_rejects_builtin_explicit_name(tmp: TempDir):
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfx = oxitest.Fixtures(name='list')\n")
    with raises(ValueError, match="Python builtin"):
        load_fixtures_from_conftest(str(conftest))


# ── Helpers integration ──────────────────────────────────────────────────────


@oxitest.mark.inprocess
def test_create_session_attaches_helpers_to_conftest_module(
    tmp: TempDir, clean_sys_modules: Fixture[None]
):
    f = tmp / "conftest.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def db():\n"
        "    return 42\n"
        "\n"
        "def make_thing():\n"
        "    return 'thing'\n"
    )
    import sys as _sys

    _sys.modules.pop("conftest", None)
    create_session([str(f)])  # side-effect: registers conftest module
    conftest_mod = _sys.modules["conftest"]
    assert hasattr(conftest_mod, "helpers"), (
        "conftest module should have a 'helpers' attribute after create_session"
    )
    assert isinstance(conftest_mod.helpers, HelperNamespace), (
        "helpers should be a HelperNamespace, got "
        f"{type(conftest_mod.helpers).__name__}"
    )


@oxitest.mark.inprocess
def test_create_session_helpers_contain_public_functions(
    tmp: TempDir, clean_sys_modules: Fixture[None]
):
    f = tmp / "conftest.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def db():\n"
        "    return 42\n"
        "\n"
        "def make_thing():\n"
        "    return 'thing'\n"
    )
    import sys as _sys

    _sys.modules.pop("conftest", None)
    create_session([str(f)])  # side-effect: registers conftest module
    conftest_mod = _sys.modules["conftest"]
    ns_name = tmp.path.name  # directory name
    scope = getattr(conftest_mod.helpers, ns_name)
    assert scope.make_thing() == "thing", (
        f"expected make_thing() to return 'thing', got {scope.make_thing()!r}"
    )


def test_create_session_helpers_only_conftest_no_fixtures_no_warning(tmp: TempDir):
    """A conftest with only helpers (no Fixtures) should not warn."""
    f = tmp / "conftest.py"
    f.write_text("def make_thing():\n    return 'thing'\n")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        create_session([str(f)])


def test_create_session_empty_conftest_still_warns(tmp: TempDir):
    """A conftest with NO fixtures AND NO helpers should still warn."""
    f = tmp / "conftest.py"
    f.write_text("")
    with warns(UserWarning, match="no Fixtures instance"):
        create_session([str(f)])


@oxitest.mark.inprocess
def test_create_session_helpers_empty_when_no_callables(
    tmp: TempDir, clean_sys_modules: Fixture[None]
):
    """helpers is present but scope has no attrs when conftest has only fixtures."""
    f = tmp / "conftest.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def db():\n"
        "    return 42\n"
    )
    import sys as _sys

    _sys.modules.pop("conftest", None)
    create_session([str(f)])
    conftest_mod = _sys.modules["conftest"]
    assert hasattr(conftest_mod, "helpers"), (
        "conftest module should have 'helpers' even with no public callables"
    )
