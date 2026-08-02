"""Tests for Fixtures() DSL, FixtureAccessor, and related public API."""

from __future__ import annotations

import unittest
from typing import Any

import oxitest
from oxitest import Fixture, Fixtures, TestContext, raises
from oxitest._bridge._builtin_context import TestContext as OxiTestContext
from oxitest._bridge._errors import (
    FixtureNotFoundError,
    UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_context import _fixture_context
from oxitest._bridge._fixtures import FixtureAccessor
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge.plugin_loader import load_plugins
from oxitest._bridge.proxy_ns import FixturesProxy
from oxitest.plugin import Plugin
from tests import helpers

# ── skip / mark ───────────────────────────────────────────────────────────────


def test_skip_raises_skip_test() -> None:
    """oxitest.skip() raises SkipTest with the provided reason message."""
    with raises(unittest.SkipTest) as exc_info:
        oxitest.skip("not ready")
    assert str(exc_info.value) == "not ready", (
        f"oxitest.skip('not ready') should produce message 'not ready', got "
        f"{str(exc_info.value)!r}"
    )


def test_skip_no_reason() -> None:
    """oxitest.skip() with no argument still raises SkipTest."""
    with raises(unittest.SkipTest):
        oxitest.skip()


def test_mark_attribute_callable_without_error() -> None:
    """Mark attributes are callable with arbitrary args and don't raise on access."""
    oxitest.mark.skip(reason="reason")
    _ = oxitest.mark.xfail
    oxitest.mark.anything("value")


def test_mark_used_as_decorator_returns_function() -> None:
    """Mark used as a bare decorator passes through the decorated function unchanged."""

    @oxitest.mark.skip
    def fn() -> None:
        pass

    assert callable(fn), (
        "@oxitest.mark.skip used as decorator should return the original callable, got "
        "non-callable"
    )


# ── Fixtures class ────────────────────────────────────────────────────────────


def test_fixtures_bare_decorator_registers_def() -> None:
    """@fx.fixture with no arguments registers a FixtureDef with default settings."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture() -> int:
        return 42

    assert len(fx.defs) == 1, (
        f"one fixture registered with @fx.fixture should produce 1 def, got "
        f"{len(fx.defs)}"
    )
    defn = fx.defs[0]
    assert defn.name == "my_fixture", (
        f"fixture def name should be 'my_fixture', got {defn.name!r}"
    )
    assert defn.func is my_fixture, (
        "fixture def func should be the decorated function itself"
    )
    assert defn.autouse is False, (
        f"default autouse should be False, got {defn.autouse!r}"
    )
    assert defn.conftest_path == "", (
        f"default conftest_path should be '', got {defn.conftest_path!r}"
    )


def test_fixtures_autouse() -> None:
    """@fx.fixture(autouse=True) sets autouse=True on the registered FixtureDef."""
    fx = oxitest.Fixtures()

    @fx.fixture(autouse=True)
    def auto() -> None:
        pass

    assert fx.defs[0].autouse is True, (
        f"@fx.fixture(autouse=True) should set autouse=True, got {fx.defs[0].autouse!r}"
    )


def test_fixtures_name_override() -> None:
    """@fx.fixture(name='renamed') registers the fixture under the overridden name."""
    fx = oxitest.Fixtures()

    @fx.fixture(name="renamed")
    def original() -> None:
        pass

    assert fx.defs[0].name == "renamed", (
        f"@fx.fixture(name='renamed') should override the fixture name, got "
        f"{fx.defs[0].name!r}"
    )


def test_fixtures_stamps_fixture_name_for_inject_compat() -> None:
    """@fx.fixture stamps the fixture_name metadata on the decorated function."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture() -> None:
        pass

    assert get_metadata(my_fixture).fixture_name == "my_fixture", (
        f"@fx.fixture should register fixture_name='my_fixture' in metadata, "
        f"got {get_metadata(my_fixture).fixture_name!r}"
    )


def test_fixtures_name_override_stamps_fixture_name() -> None:
    """@fx.fixture(name='renamed') stamps the overridden name in function metadata."""
    fx = oxitest.Fixtures()

    @fx.fixture(name="renamed")
    def original() -> None:
        pass

    assert get_metadata(original).fixture_name == "renamed", (
        f"@fx.fixture(name='renamed') should register fixture_name='renamed'"
        f" in metadata, got {get_metadata(original).fixture_name!r}"
    )


def test_fixtures_does_not_stamp_oxitest_fixture_attr() -> None:
    """Fixtures.fixture does NOT stamp _oxitest_fixture (old attribute-scan marker)."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture() -> None:
        pass

    assert not hasattr(my_fixture, "_oxitest_fixture"), (
        "Fixtures.fixture should NOT stamp the old '_oxitest_fixture' attribute on the "
        "function"
    )


def test_fixtures_multiple_registrations() -> None:
    """Multiple @fx.fixture decorations accumulate all FixtureDefs in Fixtures."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def a() -> None:
        pass

    @fx.fixture
    def b() -> None:
        pass

    assert len(fx.defs) == 2, (
        f"two @fx.fixture decorations should produce 2 defs, got {len(fx.defs)}"
    )
    assert {d.name for d in fx.defs} == {"a", "b"}, (
        f"registered fixture names should be {{'a', 'b'}}, got "
        f"{{{', '.join(d.name for d in fx.defs)}}}"
    )


def test_resolve_for_test_skip_names_prevents_resolution() -> None:
    """skip_names prevents resolving Fixture[T] params by those names."""
    called: list[str] = []

    def my_db() -> int:
        called.append("db")
        return 42

    session = helpers.make_session(helpers.make_fixture_def("db", my_db))

    def test_fn(db: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.make_meta("/fake/test.py"), skip_names=frozenset({"db"})
    )
    assert "db" not in kwargs, (
        f"'db' should be skipped (in skip_names) and not appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert called == [], "fixture must not be called when name is in skip_names"


def test_unannotated_param_matching_fixture_raises_helpful_error() -> None:
    """An unannotated param matching a fixture raises UnannotatedFixtureParamError."""
    session = helpers.make_session(
        helpers.make_fixture_def("numbers", lambda: [1, 2, 3], conftest_path="/c.py")
    )

    # param 'numbers' has no annotation — should raise a helpful error
    def test_fn(numbers) -> None:  # noqa: ANN001 — intentionally unannotated to test UnannotatedFixtureParamError
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.make_meta("t.py"))

    msg = str(exc_info.value)
    assert "numbers" in msg, (
        f"UnannotatedFixtureParamError should mention param name 'numbers', got {msg!r}"
    )
    assert "Fixture[" in msg, (
        f"UnannotatedFixtureParamError should suggest 'Fixture[' annotation, got "
        f"{msg!r}"
    )
    assert "test_fn" in msg, (
        f"UnannotatedFixtureParamError should mention function name 'test_fn', got "
        f"{msg!r}"
    )


def test_wrong_annotation_matching_fixture_raises_helpful_error() -> None:
    """Wrong annotation on a fixture-name param raises UnannotatedFixtureParamError."""
    session = helpers.make_session(
        helpers.make_fixture_def("numbers", lambda: [1, 2, 3], conftest_path="/c.py")
    )

    # param 'numbers' has wrong annotation (list[int] instead of Fixture[list[int]])
    def test_fn(numbers: list[int]) -> None:
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.make_meta("t.py"))

    msg = str(exc_info.value)
    assert "numbers" in msg, (
        f"UnannotatedFixtureParamError should mention param name 'numbers', got {msg!r}"
    )
    assert "Fixture[" in msg, (
        f"UnannotatedFixtureParamError should suggest 'Fixture[' annotation, got "
        f"{msg!r}"
    )
    assert "test_fn" in msg, (
        f"UnannotatedFixtureParamError should mention function name 'test_fn', got "
        f"{msg!r}"
    )


# ── TestContext.on_teardown alias ────────────────────────────────────────────


def test_test_context_has_on_teardown_alias() -> None:
    """OxiTestContext.on_teardown is an alias for addfinalizer."""
    assert hasattr(OxiTestContext, "on_teardown"), (
        "OxiTestContext should have an 'on_teardown' attribute (alias for addfinalizer)"
    )
    assert OxiTestContext.on_teardown is OxiTestContext.addfinalizer, (
        "OxiTestContext.on_teardown should be the same method as "
        "OxiTestContext.addfinalizer"
    )


def test_on_teardown_registers_cleanup() -> None:
    """ctx.on_teardown() registers a callback run when fn_teardowns are executed."""
    calls: list[str] = []

    def factory(ctx: Fixture[OxiTestContext]) -> str:
        ctx.on_teardown(lambda: calls.append("done"))
        return "val"

    session = helpers.make_session(
        helpers.make_fixture_def("thing", factory, conftest_path="/c.py")
    )

    def fn(thing: Fixture[str]) -> None:
        pass

    meta = helpers.make_meta("t.py")
    kwargs, fn_teardowns = session.resolve_for_test(fn, meta)
    assert kwargs["thing"] == "val", (
        f"fixture using ctx.on_teardown should still return 'val', got "
        f"{kwargs['thing']!r}"
    )
    assert not calls, (
        "on_teardown callback must not have run before fn_teardowns are executed"
    )

    for td in reversed(fn_teardowns):
        td()
    assert calls == ["done"], (
        f"on_teardown callback should run during teardown, got {calls!r}"
    )


# ── Fixtures name parameter ───────────────────────────────────────────────────


def test_fixtures_default_namespace_name_is_empty() -> None:
    """Fixtures() with no name argument stores an empty string as _namespace_name."""
    fx = oxitest.Fixtures()
    assert fx.namespace_name == "", (
        f"Fixtures() with no name should have namespace_name='', got "
        f"{fx.namespace_name!r}"
    )


def test_fixtures_explicit_name_is_stored() -> None:
    """Fixtures(name='db') stores 'db' as _namespace_name."""
    fx = oxitest.Fixtures(name="db")
    assert fx.namespace_name == "db", (
        f"Fixtures(name='db') should store namespace_name='db', got "
        f"{fx.namespace_name!r}"
    )


# ── resolve_for_test bare Fixtures annotation ─────────────────────────────────


def test_resolve_for_test_injects_fixtures_proxy_for_bare_fixtures_annotation() -> None:
    """Test that resolve_for_test injects FixturesProxy for bare Fixtures annotation."""
    session = helpers.make_session()

    # Create the test function with Fixtures annotation
    # Use the actual Fixtures class directly (not string annotation)
    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(test_fn, helpers.make_meta("/fake/test.py"))
    assert "fx" in kwargs, (
        f"Fixtures-annotated param 'fx' should be injected into kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert isinstance(kwargs["fx"], FixturesProxy), (
        f"injected value for Fixtures annotation should be a FixturesProxy, got "
        f"{type(kwargs['fx']).__name__}"
    )


def test_resolve_for_test_fixtures_proxy_has_correct_session() -> None:
    """Verify that FixturesProxy holds reference to the correct session."""
    session = helpers.make_session()

    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(test_fn, helpers.make_meta("/fake/module.py"))
    proxy = kwargs["fx"]
    assert proxy.session is session, (
        f"FixturesProxy.session should be the same session used during resolve, got "
        f"{proxy.session!r}"
    )


# ── FixtureSession.get_fixture_in_namespace ──────────────────────────────────


def test_get_fixture_in_namespace_resolves_correct_fixture() -> None:
    """get_fixture_in_namespace() resolves a fixture value by name and namespace."""
    session = helpers.make_session(
        helpers.make_fixture_def("conn", lambda: "db-conn", namespace="db"),
        helpers.make_fixture_def("conn", lambda: "http-conn", namespace="http"),
    )

    result = session.get_fixture_in_namespace(
        "conn", "db", "/fake/test.py", [], test_is_async=True
    )
    assert result == "db-conn", (
        f"get_fixture_in_namespace('conn', 'db') should return 'db-conn', got "
        f"{result!r}"
    )

    result = session.get_fixture_in_namespace(
        "conn", "http", "/fake/test.py", [], test_is_async=True
    )
    assert result == "http-conn", (
        f"get_fixture_in_namespace('conn', 'http') should return 'http-conn', got "
        f"{result!r}"
    )


def test_get_fixture_in_namespace_raises_not_found_with_namespace() -> None:
    """get_fixture_in_namespace() raises FixtureNotFoundError for missing fixtures."""
    session = helpers.make_session()

    with raises(FixtureNotFoundError) as exc_info:
        session.get_fixture_in_namespace(
            "conn", "db", "/fake/test.py", [], test_is_async=True
        )

    assert "conn" in str(exc_info.value), (
        f"FixtureNotFoundError should mention fixture name 'conn', got "
        f"{str(exc_info.value)!r}"
    )
    assert "db" in str(exc_info.value), (
        f"FixtureNotFoundError should mention namespace 'db', got "
        f"{str(exc_info.value)!r}"
    )


# ── FixtureAccessor ───────────────────────────────────────────────────────────


def test_fixture_accessor_getattr_raises_attribute_error_without_fixture_context() -> (
    None
):
    """FixtureAccessor.__getattr__ raises AttributeError when _fixture_context unset."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("value", fx_obj, lambda: 42)

    # Ensure no active fixture context.
    token = _fixture_context.set(None)
    try:
        with raises(AttributeError) as exc_info:
            _ = accessor.value  # non-underscore attribute access triggers __getattr__
        assert "no active instantiation context" in str(exc_info.value), (
            "AttributeError message should mention 'no active instantiation context', "
            f"got {str(exc_info.value)!r}"
        )
    finally:
        _fixture_context.reset(token)


def test_fixture_accessor_underscore_attr_raises_attribute_error() -> None:
    """FixtureAccessor.__getattr__ raises AttributeError for _-prefixed attrs."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("value", fx_obj, lambda: 42)
    with raises(AttributeError):
        _ = accessor._private  # noqa: SLF001


def test_fixture_accessor_call_delegates_to_func() -> None:
    """FixtureAccessor.__call__ delegates to the wrapped function."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("greet", fx_obj, lambda x: f"hi {x}")
    assert accessor("world") == "hi world", "should delegate to wrapped func"


def test_fixture_accessor_has_oxitest_fixture_name() -> None:
    """FixtureAccessor carries fixture_name for executor resolution."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("db", fx_obj, lambda: None)
    assert accessor.fixture_name == "db", "should carry fixture name"


def test_fixtures_getattr_returns_accessor() -> None:
    """Fixtures.__getattr__ returns a FixtureAccessor for registered fixtures."""
    fx_obj = Fixtures()

    @fx_obj.fixture
    def db() -> str:
        return "conn"

    accessor = fx_obj.db
    assert isinstance(accessor, FixtureAccessor), "should return FixtureAccessor"
    assert accessor.fixture_name == "db", "accessor should carry fixture name"


def test_fixtures_getattr_raises_for_unknown() -> None:
    """Fixtures.__getattr__ raises AttributeError for unregistered names."""
    fx_obj = Fixtures()
    with raises(AttributeError) as exc_info:
        _ = fx_obj.nonexistent
    assert "nonexistent" in str(exc_info.value), "should mention missing name"
    assert "Available" in str(exc_info.value), "should list available fixtures"


def test_fixtures_getattr_raises_for_underscore() -> None:
    """Fixtures.__getattr__ raises AttributeError for _-prefixed names."""
    fx_obj = Fixtures()
    with raises(AttributeError):
        _ = fx_obj._internal  # noqa: SLF001


def test_fixtures_fixture_with_options() -> None:
    """@fixtures.fixture(name=..., shared=...) registers with custom options."""
    fx_obj = Fixtures()

    @fx_obj.fixture(name="custom_name", shared=True)
    def my_func() -> str:
        return "val"

    assert len(fx_obj.defs) == 1, "should register one fixture"
    defn = fx_obj.defs[0]
    assert defn.name == "custom_name", "should use custom name"
    assert defn.shared is True, "should be shared"


def test_fixtures_namespace_name() -> None:
    """Fixtures stores the namespace name passed at construction."""
    fx_obj = Fixtures("myns")
    assert fx_obj.namespace_name == "myns", "should store namespace name"

    fx_default = Fixtures()
    assert fx_default.namespace_name == "", "default namespace should be empty"


@oxitest.mark.inprocess
def test_plugin_fixture_provider_injected(ctx: TestContext) -> None:
    """A plugin-provided FixtureProvider is resolved via Fixture[T] annotation."""

    class FakeDatabase:
        """The type that the plugin provides."""

        def __init__(self, url: str) -> None:
            self.url = url
            self.closed = False

    class FakeDatabaseProvider:
        @property
        def name(self) -> str:
            return "db"

        @property
        def fixture_type(self) -> type:
            return FakeDatabase

        def create(self, **_: Any) -> FakeDatabase:
            return FakeDatabase(url="sqlite://test")

        def teardown(self, *, value: object) -> None:
            if isinstance(value, FakeDatabase):
                value.closed = True

        @property
        def scope(self) -> str:
            return "each"

        @property
        def autouse(self) -> bool:
            return False

    provider = FakeDatabaseProvider()
    mod = helpers.make_plugin_module(
        "db_plugin",
        lambda **_: Plugin(fixture_providers=(provider,)),
    )
    helpers.install_module(ctx, "db_plugin", mod)

    registry = load_plugins(["db_plugin"], {})
    assert len(registry.fixture_providers) == 1, (
        f"Expected 1 fixture provider, got {len(registry.fixture_providers)}"
    )
    assert registry.fixture_providers[0].fixture_type is FakeDatabase, (
        "Provider fixture_type should be FakeDatabase"
    )


def test_fixtures_captures_source_line() -> None:
    """Fixtures() captures the source line number of its own instantiation call."""
    fx = oxitest.Fixtures()
    assert hasattr(fx, "source_line"), "Fixtures should capture source line"
    assert isinstance(fx.source_line, int), "source line should be an int"
    assert fx.source_line > 0, "source line should be positive"
