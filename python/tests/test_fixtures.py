"""Tests for the fixture system: registry, session, DAG resolution, and Fixtures."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Generator
from typing import Never

import oxitest
from oxitest import Fixture, Fixtures, WarnCapture, helpers, raises
from oxitest._bridge._builtin_context import TestContext as OxiTestContext
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
    UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_context import _fixture_context
from oxitest._bridge._fixture_registry import (
    FixtureRegistry,
    FixtureShadowWarning,
)
from oxitest._bridge._fixtures import FixtureAccessor
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge.plugin_loader import load_plugins
from oxitest._bridge.proxy import FrozenProxy, SharedFixtureMutationError
from oxitest._bridge.proxy_ns import FixturesProxy
from oxitest._bridge.result import ViolationKind
from oxitest.plugin import Plugin

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


# ── FixtureRegistry ───────────────────────────────────────────────────────────


def test_registry_get_returns_none_for_unknown() -> None:
    """FixtureRegistry.get() returns None when the name has never been registered."""
    reg = FixtureRegistry()
    assert reg.get("missing") is None, (
        "FixtureRegistry.get() for an unregistered name should return None"
    )


def test_registry_register_and_get() -> None:
    """Registering a FixtureDef and calling get() returns the same object."""
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def("db", conftest_path="/c.py")
    reg.register(defn)
    assert reg.get("db") is defn, (
        "FixtureRegistry.get('db') should return the exact FixtureDef that was "
        "registered"
    )


def test_registry_most_local_wins() -> None:
    """When the same fixture name is in two conftests, the leaf-most wins."""
    reg = FixtureRegistry()
    root = helpers.common.make_fixture_def(
        "db", lambda: 1, conftest_path="/root/conftest.py"
    )
    leaf = helpers.common.make_fixture_def(
        "db", lambda: 2, conftest_path="/root/tests/conftest.py"
    )
    reg.register(root)
    reg.register(leaf)
    assert reg.get("db") is leaf, (
        "FixtureRegistry should prefer the more-local (leaf) fixture over the root "
        "fixture"
    )


def test_registry_get_autouse_returns_only_autouse() -> None:
    """get_autouse() yields only fixtures registered with autouse=True."""
    reg = FixtureRegistry()
    auto = helpers.common.make_fixture_def("setup", autouse=True, conftest_path="/c.py")
    manual = helpers.common.make_fixture_def("db", conftest_path="/c.py")
    reg.register(auto)
    reg.register(manual)
    result = list(reg.get_autouse())
    assert len(result) == 1, (
        f"get_autouse() should return only 1 autouse fixture, got {len(result)}: "
        f"{[d.name for d in result]}"
    )
    assert result[0].name == "setup", (
        f"the autouse fixture should be named 'setup', got {result[0].name!r}"
    )


def test_registry_get_autouse_empty() -> None:
    """get_autouse() on an empty registry returns an empty sequence."""
    reg = FixtureRegistry()
    assert list(reg.get_autouse()) == [], (
        "get_autouse() on an empty registry should return an empty list"
    )


# ── FixtureRegistry: strict violations ─────────────────────────────────────────


def test_register_returns_violation_for_untyped_fixture() -> None:
    """No return annotation on a fixture yields MISSING_RETURN_ANNOTATION violation."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def(
        "db", lambda: None, conftest_path="/project/conftest.py"
    )

    # Act
    violations = reg.register(defn)

    # Assert
    assert len(violations) == 1, (
        f"registering an untyped fixture should produce 1 violation, "
        f"got {len(violations)}"
    )
    assert violations[0].kind == ViolationKind.MISSING_RETURN_ANNOTATION, (
        f"violation kind should be MISSING_RETURN_ANNOTATION, "
        f"got {violations[0].kind!r}"
    )
    assert violations[0].detail == "db", (
        f"violation detail should be fixture name 'db', got {violations[0].detail!r}"
    )
    assert violations[0].node_id == "/project/conftest.py", (
        f"violation node_id should be conftest path, got {violations[0].node_id!r}"
    )


def test_register_returns_empty_for_typed_fixture() -> None:
    """Registering a fully-annotated fixture produces no violations."""
    # Arrange
    reg = FixtureRegistry()

    def typed_fixture() -> int:
        return 42

    defn = helpers.common.make_fixture_def(
        "val", typed_fixture, conftest_path="/project/conftest.py"
    )

    # Act
    violations = reg.register(defn)

    # Assert
    assert violations == [], (
        "registering a typed fixture should produce no violations, "
        f"got {len(violations)}"
    )


# ── FixtureRegistry: shadow warnings ──────────────────────────────────────────


def test_register_duplicate_name_warns(warn: WarnCapture) -> None:
    """Registering the same name from two conftests emits a FixtureShadowWarning."""
    # Arrange
    reg = FixtureRegistry()
    parent_def = helpers.common.make_fixture_def("db", conftest_path="conftest.py")
    child_def = helpers.common.make_fixture_def("db", conftest_path="tests/conftest.py")

    # Act
    reg.register(parent_def)
    reg.register(child_def)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert len(shadow_warnings) == 1, (
        f"registering 'db' from a different conftest should emit 1 shadow warning, "
        f"got {len(shadow_warnings)}"
    )
    msg = str(shadow_warnings[0].message)
    assert "db" in msg, f"warning should mention fixture name 'db', got {msg!r}"
    assert "conftest.py" in msg, f"warning should mention parent path, got {msg!r}"
    assert "tests/conftest.py" in msg, f"warning should mention child path, got {msg!r}"


def test_register_first_fixture_no_shadow_warning(warn: WarnCapture) -> None:
    """First registration of a fixture name never emits a shadow warning."""
    # Arrange
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(defn)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert shadow_warnings == [], (
        "first registration of a fixture should not emit a shadow warning, "
        f"got {len(shadow_warnings)}"
    )


def test_register_same_conftest_no_shadow_warning(warn: WarnCapture) -> None:
    """Re-registering a fixture from the same conftest emits no shadow warning."""
    # Arrange
    reg = FixtureRegistry()
    first = helpers.common.make_fixture_def("db", conftest_path="conftest.py")
    second = helpers.common.make_fixture_def("db", conftest_path="conftest.py")

    # Act
    reg.register(first)
    reg.register(second)

    # Assert
    shadow_warnings = [
        w for w in warn.list if issubclass(w.category, FixtureShadowWarning)
    ]
    assert shadow_warnings == [], (
        "re-registering 'db' from the same conftest should not emit a shadow warning, "
        f"got {len(shadow_warnings)}"
    )


# ── FixtureSession: function scope ────────────────────────────────────────────


def test_function_scope_new_instance_per_resolve() -> None:
    """Function-scoped fixtures are re-created on every resolve_for_test call."""
    calls = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("val", factory, conftest_path="/c.py")
    )

    def fn(val: Fixture[int]) -> None:
        pass

    k1, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert k1["val"] == 1, (
        f"first resolve of function-scope fixture should return 1, got {k1['val']!r}"
    )
    assert k2["val"] == 2, (
        f"second resolve of function-scope fixture should return 2 (new instance), got "
        f"{k2['val']!r}"
    )


# ── Yield teardown ────────────────────────────────────────────────────────────


def test_yield_fixture_function_scope_teardown() -> None:
    """Yield fixture teardown code runs after fn_teardowns are called, not before."""
    torn_down = []

    def factory() -> Generator[str]:
        yield "value"
        torn_down.append(True)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("val", factory, conftest_path="/c.py")
    )

    def fn(val: Fixture[str]) -> None:
        pass

    meta = helpers.common.make_meta("t.py")
    kwargs, fn_teardowns = session.resolve_for_test(fn, meta)
    assert kwargs["val"] == "value", (
        f"yield fixture should provide 'value' before teardown, got {kwargs['val']!r}"
    )
    assert not torn_down, (
        "yield fixture teardown must not have run before fn_teardowns are called"
    )

    for td in reversed(fn_teardowns):
        td()
    assert torn_down == [True], (
        f"yield fixture teardown should run after fn_teardowns, got {torn_down!r}"
    )


# ── request fixture ───────────────────────────────────────────────────────────


def test_addfinalizer_runs_in_teardown() -> None:
    """ctx.addfinalizer() callbacks execute when fn_teardowns are invoked."""
    calls = []

    def factory(ctx: Fixture[OxiTestContext]) -> str:
        ctx.addfinalizer(lambda: calls.append("done"))
        return "val"

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("thing", factory, conftest_path="/c.py")
    )

    def fn(thing: Fixture[str]) -> None:
        pass

    meta = helpers.common.make_meta("t.py")
    kwargs, fn_teardowns = session.resolve_for_test(fn, meta)
    assert kwargs["thing"] == "val", (
        f"fixture using ctx.addfinalizer should still return 'val', got "
        f"{kwargs['thing']!r}"
    )
    assert not calls, (
        "addfinalizer callback must not run before fn_teardowns are executed"
    )

    for td in reversed(fn_teardowns):
        td()
    assert calls == ["done"], (
        f"addfinalizer callback should run during teardown, got {calls!r}"
    )


# ── DAG resolution ────────────────────────────────────────────────────────────


def test_dag_fixture_depending_on_fixture() -> None:
    """A fixture that depends on another fixture is resolved transitively via DAG."""

    def derived(base: Fixture[int]) -> int:
        return base * 2

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("base", lambda: 10, conftest_path="/c.py"),
        helpers.common.make_fixture_def("derived", derived, conftest_path="/c.py"),
    )

    def fn(derived: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["derived"] == 20, (
        f"derived fixture (base*2=20) should be resolved via DAG, got "
        f"{kwargs['derived']!r}"
    )


# ── Autouse ───────────────────────────────────────────────────────────────────


def test_autouse_runs_side_effects_without_being_in_kwargs() -> None:
    """Autouse fixtures run their factory but do not appear in test kwargs."""
    calls = []

    def setup() -> None:
        calls.append(1)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "setup", setup, autouse=True, conftest_path="/c.py"
        )
    )

    def fn() -> None:
        pass  # does NOT request 'setup'

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "setup" not in kwargs, (
        f"autouse fixture should not appear in test kwargs (not requested), got keys: "
        f"{list(kwargs)}"
    )
    assert calls == [1], (
        f"autouse fixture factory should be called even when not explicitly requested, "
        f"got calls={calls!r}"
    )


def test_autouse_teardown_still_runs() -> None:
    """Autouse yield fixture teardown runs even when the test doesn't request it."""
    torn_down = []

    def setup() -> Generator[None]:
        yield
        torn_down.append(True)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "setup", setup, autouse=True, conftest_path="/c.py"
        )
    )

    def fn() -> None:
        pass

    _, fn_teardowns = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    for td in reversed(fn_teardowns):
        td()
    assert torn_down == [True], (
        f"autouse yield fixture teardown should run when fn_teardowns are called, got "
        f"{torn_down!r}"
    )


# ── Error cases ───────────────────────────────────────────────────────────────


def test_missing_fixture_raises_not_found() -> None:
    """Requesting a fixture that was never registered raises FixtureNotFoundError."""
    session = helpers.common.make_session()

    def fn(nonexistent: Fixture[int]) -> None:
        pass

    with raises(FixtureNotFoundError) as exc_info:
        session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "nonexistent" in str(exc_info.value), (
        f"FixtureNotFoundError message should mention 'nonexistent', got "
        f"{str(exc_info.value)!r}"
    )


def test_cycle_raises_fixture_cycle_error() -> None:
    """A circular fixture dependency raises FixtureCycleError at resolve time."""

    def a(b: Fixture[int]) -> int:
        return b

    def b(a: Fixture[int]) -> int:
        return a

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("a", a, conftest_path="/c.py"),
        helpers.common.make_fixture_def("b", b, conftest_path="/c.py"),
    )

    def fn(a: Fixture[int]) -> None:
        pass

    with raises(FixtureCycleError):
        session.resolve_for_test(fn, helpers.common.make_meta("t.py"))


def test_setup_error_raises_fixture_setup_error() -> None:
    """An exception raised inside a fixture factory is wrapped in FixtureSetupError."""

    def bad() -> Never:
        msg = "oops"
        raise ValueError(msg)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("bad", bad, conftest_path="/c.py")
    )

    def fn(bad: Fixture[None]) -> None:
        pass

    with raises(FixtureSetupError) as exc_info:
        session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "bad" in str(exc_info.value), (
        f"FixtureSetupError should mention fixture name 'bad', got "
        f"{str(exc_info.value)!r}"
    )
    assert "oops" in str(exc_info.value), (
        f"FixtureSetupError should include the original error message 'oops', got "
        f"{str(exc_info.value)!r}"
    )


# ── Annotation-based resolution ───────────────────────────────────────────────


def test_fixture_marker_param_resolved_by_name() -> None:
    """Fixture[T]-annotated params resolve by matching their name in the registry."""
    calls = []

    def factory() -> int:
        calls.append(1)
        return 42

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("val", factory, conftest_path="/c.py")
    )

    def fn(val: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["val"] == 42, (
        f"Fixture[int]-annotated param 'val' should be resolved to 42, got "
        f"{kwargs['val']!r}"
    )
    assert len(calls) == 1, (
        f"fixture factory should be called exactly once per resolve, got {len(calls)} "
        "calls"
    )


def test_non_fixture_param_ignored_by_resolver() -> None:
    """Params with plain type annotations (not Fixture[T]) skip fixture resolution."""
    session = helpers.common.make_session()

    def fn(x: int) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "x" not in kwargs, (
        f"plain-typed param 'x: int' should not be resolved as a fixture, got "
        f"kwargs={list(kwargs)}"
    )


def test_fixture_test_context_injected_directly() -> None:
    """Fixture[OxiTestContext] injects the built-in context with no registry entry."""
    session = helpers.common.make_session()

    def fn(ctx: Fixture[OxiTestContext]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert isinstance(kwargs["ctx"], OxiTestContext), (
        f"Fixture[OxiTestContext] should inject an OxiTestContext instance, got "
        f"{type(kwargs['ctx']).__name__}"
    )


def test_fixture_dep_resolved_via_annotation() -> None:
    """Dependencies declared via Fixture[T] annotation are resolved transitively."""

    def derived(base: Fixture[int]) -> int:
        return base * 3

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("base", lambda: 10, conftest_path="/c.py"),
        helpers.common.make_fixture_def("derived", derived, conftest_path="/c.py"),
    )

    def fn(derived: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["derived"] == 30, (
        f"derived fixture (base*3=30) should resolve via annotation-based DAG, got "
        f"{kwargs['derived']!r}"
    )


def test_autouse_not_double_invoked_when_explicitly_requested() -> None:
    """An autouse fixture explicitly requested by a test is called once, not twice."""
    calls = []

    def setup() -> int:
        calls.append(1)
        return len(calls)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "setup", setup, autouse=True, conftest_path="/c.py"
        )
    )

    def fn(setup: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "setup" in kwargs, (
        f"explicitly requested autouse fixture should appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert len(calls) == 1, (
        f"autouse fixture explicitly requested should only be called once (not twice), "
        f"got {len(calls)} calls"
    )


# ── Fixtures class ────────────────────────────────────────────────────────────


def test_fixtures_bare_decorator_registers_def() -> None:
    """@fx.fixture with no arguments registers a FixtureDef with default settings."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture() -> int:
        return 42

    assert len(fx._defs) == 1, (
        f"one fixture registered with @fx.fixture should produce 1 def, got "
        f"{len(fx._defs)}"
    )
    defn = fx._defs[0]
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

    assert fx._defs[0].autouse is True, (
        f"@fx.fixture(autouse=True) should set autouse=True, got "
        f"{fx._defs[0].autouse!r}"
    )


def test_fixtures_name_override() -> None:
    """@fx.fixture(name='renamed') registers the fixture under the overridden name."""
    fx = oxitest.Fixtures()

    @fx.fixture(name="renamed")
    def original() -> None:
        pass

    assert fx._defs[0].name == "renamed", (
        f"@fx.fixture(name='renamed') should override the fixture name, got "
        f"{fx._defs[0].name!r}"
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

    assert len(fx._defs) == 2, (
        f"two @fx.fixture decorations should produce 2 defs, got {len(fx._defs)}"
    )
    assert {d.name for d in fx._defs} == {"a", "b"}, (
        f"registered fixture names should be {{'a', 'b'}}, got "
        f"{{{', '.join(d.name for d in fx._defs)}}}"
    )


def test_resolve_for_test_skip_names_prevents_resolution() -> None:
    """skip_names prevents resolving Fixture[T] params by those names."""
    called: list[str] = []

    def my_db() -> int:
        called.append("db")
        return 42

    session = helpers.common.make_session(helpers.common.make_fixture_def("db", my_db))

    def test_fn(db: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.common.make_meta("/fake/test.py"), skip_names=frozenset({"db"})
    )
    assert "db" not in kwargs, (
        f"'db' should be skipped (in skip_names) and not appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert called == [], "fixture must not be called when name is in skip_names"


def test_unannotated_param_matching_fixture_raises_helpful_error() -> None:
    """An unannotated param matching a fixture raises UnannotatedFixtureParamError."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "numbers", lambda: [1, 2, 3], conftest_path="/c.py"
        )
    )

    # param 'numbers' has no annotation — should raise a helpful error
    def test_fn(numbers) -> None:  # noqa: ANN001 — intentionally unannotated to test UnannotatedFixtureParamError
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.common.make_meta("t.py"))

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
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "numbers", lambda: [1, 2, 3], conftest_path="/c.py"
        )
    )

    # param 'numbers' has wrong annotation (list[int] instead of Fixture[list[int]])
    def test_fn(numbers: list[int]) -> None:
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.common.make_meta("t.py"))

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


# ── oxitest.fixture sentinel ──────────────────────────────────────────────────


def test_oxitest_fixture_sentinel_raises_with_instructions() -> None:
    """oxitest.fixture sentinel raises with instructions to use Fixtures() instead."""
    with raises((AttributeError, RuntimeError)) as exc_info:
        oxitest.fixture(lambda: None)
    msg = str(exc_info.value)
    assert "Fixtures()" in msg, (
        f"oxitest.fixture sentinel error should mention 'Fixtures()', got {msg!r}"
    )
    assert "@fixtures.fixture" in msg, (
        f"oxitest.fixture sentinel error should mention '@fixtures.fixture', got "
        f"{msg!r}"
    )


def test_oxitest_fixture_sentinel_exists_as_attribute() -> None:
    """oxitest.fixture is a top-level sentinel; raises on use, exists for imports."""
    assert hasattr(oxitest, "fixture"), (
        "'fixture' should be exported as an attribute of the oxitest module"
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

    session = helpers.common.make_session(
        helpers.common.make_fixture_def("thing", factory, conftest_path="/c.py")
    )

    def fn(thing: Fixture[str]) -> None:
        pass

    meta = helpers.common.make_meta("t.py")
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


# ── shared= fixture tier ───────────────────────────────────────────────────────


def test_fixture_decorator_accepts_shared_kwarg() -> None:
    """@fixture(shared=True) stores shared=True on the resulting FixtureDef."""
    reg_obj = Fixtures()

    @reg_obj.fixture(shared=True)
    def my_val() -> int:
        return 42

    defn = reg_obj._defs[0]
    assert defn.shared is True, (
        f"@fixture(shared=True) should set defn.shared=True, got {defn.shared!r}"
    )
    assert defn.name == "my_val", f"fixture name should be 'my_val', got {defn.name!r}"


def test_fixture_decorator_default_shared_is_false() -> None:
    """@fixture without shared= defaults to shared=False on the FixtureDef."""
    reg_obj = Fixtures()

    @reg_obj.fixture
    def my_val() -> int:
        return 42

    defn = reg_obj._defs[0]
    assert defn.shared is False, (
        f"default @fixture (no shared=) should have defn.shared=False, got "
        f"{defn.shared!r}"
    )


def test_shared_fixture_is_called_once_across_tests() -> None:
    """A shared fixture factory is called once; subsequent resolutions use the cache."""
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", factory, shared=True, conftest_path="/c.py"
        )
    )

    def fn(db: Fixture[int]) -> None:
        pass

    k1, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert len(calls) == 1, f"factory called {len(calls)} times, expected 1"
    # Both resolutions return the same proxy instance (cache hit)
    assert k1["db"] is k2["db"], "same FrozenProxy instance expected on cache hit"


def test_shared_fixture_value_is_wrapped_in_frozen_proxy() -> None:
    """Shared fixture values are wrapped in a FrozenProxy to prevent mutation."""

    def factory() -> dict[str, int]:
        return {"x": 1}

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "cfg", factory, shared=True, conftest_path="/c.py"
        )
    )

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert isinstance(k["cfg"], FrozenProxy), (
        f"shared fixture should be wrapped in a FrozenProxy, got "
        f"{type(k['cfg']).__name__}"
    )


def test_shared_fixture_proxy_raises_on_item_mutation() -> None:
    """__setitem__ on a shared fixture value raises SharedFixtureMutationError."""

    def factory() -> dict[str, int]:
        return {"x": 1}

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "cfg", factory, shared=True, conftest_path="/c.py"
        )
    )

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    with raises(SharedFixtureMutationError):
        k["cfg"]["x"] = 2


def test_shared_fixture_teardown_runs_on_end_session() -> None:
    """Shared yield fixture teardown is deferred to end_session, not end_module."""
    torn_down: list[bool] = []

    def factory() -> Generator[str]:
        yield "v"
        torn_down.append(True)

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "res", factory, shared=True, conftest_path="/c.py"
        )
    )

    def fn(res: Fixture[str]) -> None:
        pass

    session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    session.end_module("t.py")
    assert not torn_down, "teardown must not run at end_module for shared fixtures"
    session.end_session()
    assert torn_down == [True], "teardown must run at end_session"


# ── FixtureNotFoundError namespace field ──────────────────────────────────────


def test_fixture_not_found_error_with_namespace() -> None:
    """FixtureNotFoundError includes both fixture name and namespace in the message."""
    exc = FixtureNotFoundError("conn", namespace="db")
    assert "conn" in str(exc), (
        f"FixtureNotFoundError with namespace should mention fixture name 'conn', got "
        f"{str(exc)!r}"
    )
    assert "db" in str(exc), (
        f"FixtureNotFoundError with namespace should mention namespace 'db', got "
        f"{str(exc)!r}"
    )
    assert exc.fixture_name == "conn", (
        f"exc.fixture_name should be 'conn', got {exc.fixture_name!r}"
    )
    assert exc.namespace == "db", f"exc.namespace should be 'db', got {exc.namespace!r}"


def test_fixture_not_found_error_without_namespace() -> None:
    """FixtureNotFoundError without a namespace formats as 'fixture not found'."""
    exc = FixtureNotFoundError("conn")
    assert str(exc) == "fixture 'conn' not found", (
        f"FixtureNotFoundError without namespace should format as \"fixture 'conn' not "
        f'found", got {str(exc)!r}'
    )
    assert exc.namespace == "", (
        f"FixtureNotFoundError without namespace should have exc.namespace='', got "
        f"{exc.namespace!r}"
    )


# ── FixtureDef.namespace field ────────────────────────────────────────────────


def test_fixture_def_has_namespace_field() -> None:
    """FixtureDef stores the namespace name passed at construction."""
    defn = helpers.common.make_fixture_def(
        "conn", namespace="db", conftest_path="/path/conftest.py"
    )
    assert defn.namespace == "db", (
        f"FixtureDef(namespace='db') should store namespace='db', got "
        f"{defn.namespace!r}"
    )


def test_fixture_def_namespace_defaults_to_empty() -> None:
    """FixtureDef.namespace defaults to empty string when no namespace is given."""
    defn = helpers.common.make_fixture_def("conn")
    assert defn.namespace == "", (
        f"FixtureDef without namespace should default to '', got {defn.namespace!r}"
    )


# ── FixtureRegistry.get_in_namespace + has_namespace ─────────────────────────


def test_registry_get_in_namespace_returns_matching_def() -> None:
    """get_in_namespace() returns the FixtureDef registered under a given namespace."""
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    reg.register(defn)
    result = reg.get_in_namespace("conn", "db")
    assert result is defn, (
        f"get_in_namespace('conn', 'db') should return the registered def, got "
        f"{result!r}"
    )


def test_registry_get_in_namespace_ignores_other_namespace() -> None:
    """get_in_namespace() distinguishes same-named fixtures in different namespaces."""
    reg = FixtureRegistry()
    db_def = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    http_def = helpers.common.make_fixture_def("conn", lambda: 2, namespace="http")
    reg.register(db_def)
    reg.register(http_def)
    assert reg.get_in_namespace("conn", "db") is db_def, (
        "get_in_namespace('conn', 'db') should return the db namespace fixture, not "
        "http"
    )
    assert reg.get_in_namespace("conn", "http") is http_def, (
        "get_in_namespace('conn', 'http') should return the http namespace fixture, "
        "not db"
    )


def test_registry_get_in_namespace_returns_none_when_missing() -> None:
    """get_in_namespace() returns None when the fixture or namespace is absent."""
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def("conn", lambda: 1, namespace="db")
    reg.register(defn)
    assert reg.get_in_namespace("conn", "http") is None, (
        "get_in_namespace('conn', 'http') should return None (wrong namespace)"
    )
    assert reg.get_in_namespace("missing", "db") is None, (
        "get_in_namespace('missing', 'db') should return None (fixture not found)"
    )


def test_registry_has_namespace_true() -> None:
    """has_namespace() returns True when at least one fixture with it is registered."""
    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("conn", lambda: 1, namespace="db"))
    assert reg.has_namespace("db") is True, (
        "has_namespace('db') should return True when a fixture with that namespace is "
        "registered"
    )


def test_registry_has_namespace_false() -> None:
    """has_namespace() returns False when no fixture is registered under a namespace."""
    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("conn", lambda: 1, namespace="db"))
    assert reg.has_namespace("http") is False, (
        "has_namespace('http') should return False when no fixture with that namespace "
        "exists"
    )


def test_registry_has_namespace_empty_registry() -> None:
    """has_namespace() returns False on an empty registry."""
    reg = FixtureRegistry()
    assert reg.has_namespace("db") is False, (
        "has_namespace('db') should return False on an empty registry"
    )


# ── FixtureRegistry: __contains__, __iter__, all_defs ─────────────────────────


def test_registry_contains_registered_name() -> None:
    """__contains__ returns True for a fixture name that has been registered."""
    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("db", conftest_path="conftest.py"))
    assert "db" in reg, "__contains__ should return True for a registered fixture name"


def test_registry_contains_returns_false_for_unknown() -> None:
    """__contains__ returns False for a name that has never been registered."""
    reg = FixtureRegistry()
    assert "missing" not in reg, (
        "__contains__ should return False for an unregistered fixture name"
    )


def test_registry_iter_yields_registered_names() -> None:
    """__iter__ yields all registered fixture names."""
    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("a", conftest_path="conftest.py"))
    reg.register(helpers.common.make_fixture_def("b", conftest_path="conftest.py"))
    assert set(reg) == {"a", "b"}, "__iter__ should yield all registered fixture names"


def test_registry_iter_empty() -> None:
    """__iter__ on an empty registry yields nothing."""
    reg = FixtureRegistry()
    assert list(reg) == [], "__iter__ on an empty registry should yield nothing"


def test_registry_all_defs_returns_all_entries() -> None:
    """all_defs() returns all FixtureDefs for a name in registration order."""
    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "db", lambda: "root", conftest_path="root/conftest.py"
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "db", lambda: "leaf", conftest_path="root/sub/conftest.py"
        )
    )
    defs = reg.all_defs("db")
    assert len(defs) == 2, (
        "all_defs should return all registered FixtureDefs for a name"
    )
    assert defs[0].conftest_path == "root/conftest.py", (
        "first entry should be the root conftest definition"
    )
    assert defs[1].conftest_path == "root/sub/conftest.py", (
        "second entry should be the leaf conftest definition"
    )


def test_registry_all_defs_returns_empty_for_unknown() -> None:
    """all_defs() returns an empty list for a name that was never registered."""
    reg = FixtureRegistry()
    assert reg.all_defs("missing") == [], (
        "all_defs for an unregistered name should return an empty list"
    )


# ── FixtureSession.get_fixture_in_namespace ──────────────────────────────────


def test_get_fixture_in_namespace_resolves_correct_fixture() -> None:
    """get_fixture_in_namespace() resolves a fixture value by name and namespace."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("conn", lambda: "db-conn", namespace="db"),
        helpers.common.make_fixture_def("conn", lambda: "http-conn", namespace="http"),
    )

    result = session.get_fixture_in_namespace("conn", "db", "/fake/test.py", [])
    assert result == "db-conn", (
        f"get_fixture_in_namespace('conn', 'db') should return 'db-conn', got "
        f"{result!r}"
    )

    result = session.get_fixture_in_namespace("conn", "http", "/fake/test.py", [])
    assert result == "http-conn", (
        f"get_fixture_in_namespace('conn', 'http') should return 'http-conn', got "
        f"{result!r}"
    )


def test_get_fixture_in_namespace_raises_not_found_with_namespace() -> None:
    """get_fixture_in_namespace() raises FixtureNotFoundError for missing fixtures."""
    session = helpers.common.make_session()

    with raises(FixtureNotFoundError) as exc_info:
        session.get_fixture_in_namespace("conn", "db", "/fake/test.py", [])

    assert "conn" in str(exc_info.value), (
        f"FixtureNotFoundError should mention fixture name 'conn', got "
        f"{str(exc_info.value)!r}"
    )
    assert "db" in str(exc_info.value), (
        f"FixtureNotFoundError should mention namespace 'db', got "
        f"{str(exc_info.value)!r}"
    )


# ── Fixtures name parameter ───────────────────────────────────────────────────


def test_fixtures_default_namespace_name_is_empty() -> None:
    """Fixtures() with no name argument stores an empty string as _namespace_name."""
    fx = oxitest.Fixtures()
    assert fx._namespace_name == "", (
        f"Fixtures() with no name should have _namespace_name='', got "
        f"{fx._namespace_name!r}"
    )


def test_fixtures_explicit_name_is_stored() -> None:
    """Fixtures(name='db') stores 'db' as _namespace_name."""
    fx = oxitest.Fixtures(name="db")
    assert fx._namespace_name == "db", (
        f"Fixtures(name='db') should store _namespace_name='db', got "
        f"{fx._namespace_name!r}"
    )


# ── resolve_for_test bare Fixtures annotation ─────────────────────────────────


def test_resolve_for_test_injects_fixtures_proxy_for_bare_fixtures_annotation() -> None:
    """Test that resolve_for_test injects FixturesProxy for bare Fixtures annotation."""
    session = helpers.common.make_session()

    # Create the test function with Fixtures annotation
    # Use the actual Fixtures class directly (not string annotation)
    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.common.make_meta("/fake/test.py")
    )
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
    session = helpers.common.make_session()

    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.common.make_meta("/fake/module.py")
    )
    proxy = kwargs["fx"]
    assert proxy._session is session, (
        f"FixturesProxy._session should be the same session used during resolve, got "
        f"{proxy._session!r}"
    )


# ── Shared fixtures introspection ──────────────────────────────────────────────


def test_has_shared_fixtures_empty_registry() -> None:
    """has_shared_fixtures() returns False when no fixtures are registered at all."""
    session = helpers.common.make_session()
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() on an empty registry should return False"
    )


def test_has_shared_fixtures_false_when_no_fixture_is_shared() -> None:
    """has_shared_fixtures() returns False when all registered fixtures are unshared."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("db", conftest_path="/c.py")
    )
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() should return False when no fixture has shared=True"
    )


def test_has_shared_fixtures_true_when_any_fixture_is_shared() -> None:
    """has_shared_fixtures() returns True when at least one fixture has shared=True."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("db", shared=True, conftest_path="/c.py")
    )
    assert session.has_shared_fixtures() is True, (
        "has_shared_fixtures() should return True when any fixture has shared=True"
    )


def test_has_shared_fixtures_uses_most_local_definition() -> None:
    """has_shared_fixtures() uses the most-local definition; leaf shared=False wins."""
    # Root conftest defines db as shared; leaf conftest overrides it as non-shared.
    # The effective definition is the last-registered one (leaf), so
    # has_shared_fixtures() should return False.
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", shared=True, conftest_path="/root/conftest.py"
        ),
        helpers.common.make_fixture_def("db", conftest_path="/root/sub/conftest.py"),
    )
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() should use only the most-local definition; "
        "root shared=True overridden by leaf shared=False should return False"
    )


def test_shared_fixture_names_uses_most_local_definition() -> None:
    """shared_fixture_names() omits fixtures whose most-local definition is unshared."""
    # Root conftest defines db as shared; leaf conftest overrides it as non-shared.
    # shared_fixture_names() should NOT include "db" because the effective definition
    # (defs[-1]) has shared=False.
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", shared=True, conftest_path="/root/conftest.py"
        ),
        helpers.common.make_fixture_def("db", conftest_path="/root/sub/conftest.py"),
    )
    assert session.shared_fixture_names() == [], (
        "shared_fixture_names() should use only the most-local definition; "
        "a root shared=True overridden by leaf shared=False should not appear"
    )


def test_shared_fixture_names_returns_empty_when_no_shared() -> None:
    """shared_fixture_names() returns an empty list when no fixture has shared=True."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("client", conftest_path="/c.py")
    )
    assert session.shared_fixture_names() == [], (
        "shared_fixture_names() should return [] when no fixture has shared=True"
    )


def test_shared_fixture_names_returns_only_shared_names() -> None:
    """shared_fixture_names() returns a sorted list of only the shared fixture names."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("db", shared=True, conftest_path="/c.py"),
        helpers.common.make_fixture_def("cache", shared=True, conftest_path="/c.py"),
        helpers.common.make_fixture_def("client", conftest_path="/c.py"),
    )
    assert session.shared_fixture_names() == ["cache", "db"], (
        "shared_fixture_names() should return only names where shared=True, got "
        f"{session.shared_fixture_names()!r}"
    )


# ── Shared fixture groups (connected components) ──────────────────────────────


def test_shared_fixture_groups_empty_registry() -> None:
    """shared_fixture_groups() returns an empty list when no fixtures are registered."""
    session = helpers.common.make_session()
    assert session.shared_fixture_groups() == [], (
        "empty registry should return no fixture groups"
    )


def test_shared_fixture_groups_no_shared_fixtures() -> None:
    """shared_fixture_groups() returns an empty list when no fixture has shared=True."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("store", conftest_path="/conftest.py")
    )
    assert session.shared_fixture_groups() == [], (
        "registry with no shared fixtures should return no groups"
    )


def test_shared_fixture_groups_single_shared() -> None:
    """A single shared fixture forms its own group of one."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def("db", shared=True, conftest_path="/conftest.py")
    )
    groups = session.shared_fixture_groups()
    assert groups == [["db"]], (
        f"single shared fixture should produce one group, got {groups}"
    )


def test_shared_fixture_groups_transitive_dependency() -> None:
    """A non-shared fixture depending on a shared fixture joins that fixture's group."""

    class _DbType:
        pass

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", shared=True, conftest_path="/conftest.py", fixture_type=_DbType
        ),
        helpers.common.make_fixture_def(
            "repo",
            conftest_path="/conftest.py",
            depends_on=(("db", _DbType),),
        ),
    )
    groups = session.shared_fixture_groups()
    assert groups == [["db", "repo"]], (
        f"repo depends on shared db — should form one group, got {groups}"
    )


def test_shared_fixture_groups_two_independent_shared() -> None:
    """Two independent shared fixtures each form their own group."""
    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", shared=True, conftest_path="/conftest.py"
        ),
        helpers.common.make_fixture_def(
            "cache", shared=True, conftest_path="/conftest.py"
        ),
    )
    groups = session.shared_fixture_groups()
    assert len(groups) == 2, (
        f"two independent shared fixtures should produce two groups, got {groups}"
    )
    flat = [name for g in groups for name in g]
    assert sorted(flat) == ["cache", "db"], (
        f"groups should contain db and cache, got {flat}"
    )


def test_shared_fixture_groups_transitive_merge() -> None:
    """A fixture depending on two shared fixtures merges all three into one group."""

    class _DbType:
        pass

    class _CacheType:
        pass

    session = helpers.common.make_session(
        helpers.common.make_fixture_def(
            "db", shared=True, conftest_path="/c.py", fixture_type=_DbType
        ),
        helpers.common.make_fixture_def(
            "cache", shared=True, conftest_path="/c.py", fixture_type=_CacheType
        ),
        helpers.common.make_fixture_def(
            "service",
            conftest_path="/c.py",
            depends_on=(("db", _DbType), ("cache", _CacheType)),
        ),
    )
    groups = session.shared_fixture_groups()
    # service links db+cache into one connected component
    assert len(groups) == 1, (
        f"service links db+cache — should merge into one group, got {groups}"
    )
    assert sorted(groups[0]) == ["cache", "db", "service"], (
        f"merged group should contain all three fixtures, got {groups[0]}"
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
        _ = accessor._private


def test_fixture_accessor_call_delegates_to_func() -> None:
    """FixtureAccessor.__call__ delegates to the wrapped function."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("greet", fx_obj, lambda x: f"hi {x}")
    assert accessor("world") == "hi world", "should delegate to wrapped func"


def test_fixture_accessor_has_oxitest_fixture_name() -> None:
    """FixtureAccessor carries _oxitest_fixture_name for executor resolution."""
    fx_obj = Fixtures()
    accessor = FixtureAccessor("db", fx_obj, lambda: None)
    assert accessor._oxitest_fixture_name == "db", "should carry fixture name"


def test_fixtures_getattr_returns_accessor() -> None:
    """Fixtures.__getattr__ returns a FixtureAccessor for registered fixtures."""
    fx_obj = Fixtures()

    @fx_obj.fixture
    def db() -> str:
        return "conn"

    accessor = fx_obj.db
    assert isinstance(accessor, FixtureAccessor), "should return FixtureAccessor"
    assert accessor._oxitest_fixture_name == "db", "accessor should carry fixture name"


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
        _ = fx_obj._internal


def test_fixtures_fixture_with_options() -> None:
    """@fixtures.fixture(name=..., shared=...) registers with custom options."""
    fx_obj = Fixtures()

    @fx_obj.fixture(name="custom_name", shared=True)
    def my_func() -> str:
        return "val"

    assert len(fx_obj._defs) == 1, "should register one fixture"
    defn = fx_obj._defs[0]
    assert defn.name == "custom_name", "should use custom name"
    assert defn.shared is True, "should be shared"


def test_fixtures_namespace_name() -> None:
    """Fixtures stores the namespace name passed at construction."""
    fx_obj = Fixtures("myns")
    assert fx_obj._namespace_name == "myns", "should store namespace name"

    fx_default = Fixtures()
    assert fx_default._namespace_name == "", "default namespace should be empty"


@oxitest.mark.inprocess
def test_plugin_fixture_provider_injected() -> None:
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

        def create(self, ctx: object) -> FakeDatabase:
            return FakeDatabase(url="sqlite://test")

        def teardown(self, value: object) -> None:
            if isinstance(value, FakeDatabase):
                value.closed = True

        @property
        def scope(self) -> str:
            return "each"

        @property
        def autouse(self) -> bool:
            return False

    provider = FakeDatabaseProvider()
    mod = helpers.common.make_plugin_module(
        "db_plugin",
        lambda **_: Plugin(fixture_providers=(provider,)),
    )
    sys.modules["db_plugin"] = mod

    try:
        registry = load_plugins(["db_plugin"], {})
        assert len(registry.fixture_providers) == 1, (
            f"Expected 1 fixture provider, got {len(registry.fixture_providers)}"
        )
        assert registry.fixture_providers[0].fixture_type is FakeDatabase, (
            "Provider fixture_type should be FakeDatabase"
        )
    finally:
        sys.modules.pop("db_plugin", None)


# ── FixtureRegistry: dual-index type-based resolve ────────────────────────────


class DBSession:
    """Stub type used to test type-based fixture resolution."""


class AuthToken:
    """Stub type used to verify FixtureNotFoundError when no fixture matches a type."""


def test_registry_resolve_by_type_unique() -> None:
    """Single fixture for a type resolves regardless of qualifier."""
    reg = FixtureRegistry()
    defn = helpers.common.make_fixture_def(
        "db_session", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg.register(defn)

    result = reg.resolve(DBSession)
    assert result.name == "db_session", "should resolve the only match by type"

    result2 = reg.resolve(DBSession, qualifier="anything")
    assert result2.name == "db_session", "qualifier ignored when type is unique"


def test_registry_resolve_by_type_ambiguous_with_qualifier() -> None:
    """Two fixtures of same type -- qualifier disambiguates."""
    reg = FixtureRegistry()
    dev = helpers.common.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.common.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg.register(dev)
    reg.register(prod)

    result = reg.resolve(DBSession, qualifier="dev_db")
    assert result.name == "dev_db", "qualifier should select dev_db"


def test_registry_resolve_ambiguous_no_match() -> None:
    """Two fixtures of same type, unknown qualifier -- AmbiguousFixtureError."""
    reg = FixtureRegistry()
    dev = helpers.common.make_fixture_def(
        "dev_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    prod = helpers.common.make_fixture_def(
        "prod_db", DBSession, conftest_path="/c.py", fixture_type=DBSession
    )
    reg.register(dev)
    reg.register(prod)

    with raises(AmbiguousFixtureError, match="ambiguous"):
        reg.resolve(DBSession, qualifier="unknown")


def test_registry_resolve_no_match() -> None:
    """No fixture for type -- FixtureNotFoundError."""
    reg = FixtureRegistry()

    with raises(FixtureNotFoundError):
        reg.resolve(AuthToken)


def test_registry_override_precedence() -> None:
    """Last registered fixture of same type wins (leaf conftest overrides root)."""
    reg = FixtureRegistry()
    root = helpers.common.make_fixture_def(
        "db", lambda: "root", conftest_path="/conftest.py", fixture_type=DBSession
    )
    leaf = helpers.common.make_fixture_def(
        "db", lambda: "leaf", conftest_path="/tests/conftest.py", fixture_type=DBSession
    )
    reg.register(root)
    reg.register(leaf)

    result = reg.resolve(DBSession)
    assert result.conftest_path == "/tests/conftest.py", (
        "leaf conftest should override root"
    )


def test_fixtures_captures_source_line() -> None:
    """Fixtures() captures the source line number of its own instantiation call."""
    fx = oxitest.Fixtures()
    assert hasattr(fx, "_source_line"), "Fixtures should capture source line"
    assert isinstance(fx._source_line, int), "source line should be an int"
    assert fx._source_line > 0, "source line should be positive"
