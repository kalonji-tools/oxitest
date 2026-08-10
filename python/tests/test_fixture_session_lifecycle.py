"""Tests for FixtureSession — resolve, teardown, DAG, autouse, and error wrapping."""

from __future__ import annotations

from collections.abc import Generator
from typing import Never

from oxitest import Fixture, raises
from oxitest._bridge._builtin_context import TestContext as OxiTestContext
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._lifetime import Lifetime
from tests import helpers

# ── FixtureSession: function scope ────────────────────────────────────────────


def test_function_scope_new_instance_per_resolve() -> None:
    """Function-scoped fixtures are re-created on every resolve_for_test call."""
    calls = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    session = helpers.make_session(
        helpers.make_fixture_def("val", factory, declaration_path="/c.py")
    )

    def fn(val: Fixture[int]) -> None:
        pass

    k1, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
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

    session = helpers.make_session(
        helpers.make_fixture_def("val", factory, declaration_path="/c.py")
    )

    def fn(val: Fixture[str]) -> None:
        pass

    meta = helpers.make_meta("t.py")
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

    session = helpers.make_session(
        helpers.make_fixture_def("thing", factory, declaration_path="/c.py")
    )

    def fn(thing: Fixture[str]) -> None:
        pass

    meta = helpers.make_meta("t.py")
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

    session = helpers.make_session(
        helpers.make_fixture_def("base", lambda: 10, declaration_path="/c.py"),
        helpers.make_fixture_def("derived", derived, declaration_path="/c.py"),
    )

    def fn(derived: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
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

    session = helpers.make_session(
        helpers.make_fixture_def("setup", setup, autouse=True, declaration_path="/c.py")
    )

    def fn() -> None:
        pass  # does NOT request 'setup'

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
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

    session = helpers.make_session(
        helpers.make_fixture_def("setup", setup, autouse=True, declaration_path="/c.py")
    )

    def fn() -> None:
        pass

    _, fn_teardowns = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    for td in reversed(fn_teardowns):
        td()
    assert torn_down == [True], (
        f"autouse yield fixture teardown should run when fn_teardowns are called, got "
        f"{torn_down!r}"
    )


def _anchored_autouse_def(calls: list[str]) -> FixtureDef[None]:
    """An autouse def anchored at /t/api that records each firing (#1774)."""

    def setup() -> None:
        calls.append("fired")

    return FixtureDef(
        name="api_setup",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=setup,
            defining_module_path="/t/api/__fixtures__.py",
            anchor_package_path="/t/api",
            lifetime=Lifetime.FUNCTION,
        ),
        autouse=True,
    )


def test_anchored_autouse_out_of_boundary_neither_fires_nor_raises() -> None:
    """The autouse pass must not resolve an anchored fixture past its boundary."""
    # Arrange
    calls: list[str] = []
    session = helpers.make_session(_anchored_autouse_def(calls))

    def outside_test() -> None:
        pass

    # Act — must not raise: unfiltered enumeration feeds the name into the
    # B1-filtered lookup, which raises FixtureNotFoundError (#1774)
    kwargs, _ = session.resolve_for_test(
        outside_test, helpers.make_meta("/t/admin/test_other.py")
    )

    # Assert
    assert kwargs == {} and calls == [], (
        "an autouse fixture anchored at /t/api firing (or erroring) for a test "
        "in /t/admin would break every out-of-boundary test on a fixture it "
        "never requested the moment #1716 ships anchored autouse"
    )


def test_anchored_autouse_fires_inside_boundary() -> None:
    """Inside its boundary an anchored autouse fixture still runs per test."""
    # Arrange
    calls: list[str] = []
    session = helpers.make_session(_anchored_autouse_def(calls))

    def inside_test() -> None:
        pass

    # Act
    kwargs, _ = session.resolve_for_test(
        inside_test, helpers.make_meta("/t/api/test_inside.py")
    )

    # Assert
    assert kwargs == {} and calls == ["fired"], (
        "B1-filtering the candidate set must only drop out-of-boundary defs; "
        "eating the in-boundary firing would make anchored autouse a no-op"
    )


# ── Error cases ───────────────────────────────────────────────────────────────


def test_setup_error_raises_fixture_setup_error() -> None:
    """An exception raised inside a fixture factory is wrapped in FixtureSetupError."""

    def bad() -> Never:
        msg = "oops"
        raise ValueError(msg)

    session = helpers.make_session(
        helpers.make_fixture_def("bad", bad, declaration_path="/c.py")
    )

    def fn(bad: Fixture[None]) -> None:
        pass

    with raises(FixtureSetupError) as exc_info:
        session.resolve_for_test(fn, helpers.make_meta("t.py"))
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

    session = helpers.make_session(
        helpers.make_fixture_def("val", factory, declaration_path="/c.py")
    )

    def fn(val: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
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
    session = helpers.make_session()

    def fn(x: int) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert "x" not in kwargs, (
        f"plain-typed param 'x: int' should not be resolved as a fixture, got "
        f"kwargs={list(kwargs)}"
    )


def test_fixture_test_context_injected_directly() -> None:
    """Fixture[OxiTestContext] injects the built-in context with no registry entry."""
    session = helpers.make_session()

    def fn(ctx: Fixture[OxiTestContext]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert isinstance(kwargs["ctx"], OxiTestContext), (
        f"Fixture[OxiTestContext] should inject an OxiTestContext instance, got "
        f"{type(kwargs['ctx']).__name__}"
    )


def test_fixture_dep_resolved_via_annotation() -> None:
    """Dependencies declared via Fixture[T] annotation are resolved transitively."""

    def derived(base: Fixture[int]) -> int:
        return base * 3

    session = helpers.make_session(
        helpers.make_fixture_def("base", lambda: 10, declaration_path="/c.py"),
        helpers.make_fixture_def("derived", derived, declaration_path="/c.py"),
    )

    def fn(derived: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
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

    session = helpers.make_session(
        helpers.make_fixture_def("setup", setup, autouse=True, declaration_path="/c.py")
    )

    def fn(setup: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert "setup" in kwargs, (
        f"explicitly requested autouse fixture should appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert len(calls) == 1, (
        f"autouse fixture explicitly requested should only be called once (not twice), "
        f"got {len(calls)} calls"
    )
