from __future__ import annotations

import unittest

import oxitest
from oxitest import Fixture, raises
from oxitest._bridge.fixtures import (
    FixtureCycleError,
    FixtureDef,
    FixtureNotFoundError,
    FixtureRegistry,
    Fixtures,
    FixtureSession,
    FixtureSetupError,
    _TestContext as OxiTestContext,
)

# ── skip / mark ───────────────────────────────────────────────────────────────


def test_skip_raises_skip_test():
    with raises(unittest.SkipTest) as exc_info:
        oxitest.skip("not ready")
    assert str(exc_info.value) == "not ready", (
        f"oxitest.skip('not ready') should produce message 'not ready', got "
        f"{str(exc_info.value)!r}"
    )


def test_skip_no_reason():
    with raises(unittest.SkipTest):
        oxitest.skip()


def test_mark_attribute_callable_without_error():
    oxitest.mark.skip("reason")
    oxitest.mark.xfail
    oxitest.mark.anything("value")


def test_mark_used_as_decorator_returns_function():
    @oxitest.mark.skip
    def fn():
        pass

    assert callable(fn), (
        "@oxitest.mark.skip used as decorator should return the original callable, got "
        "non-callable"
    )


# ── FixtureRegistry ───────────────────────────────────────────────────────────


def test_registry_get_returns_none_for_unknown():
    reg = FixtureRegistry()
    assert reg.get("missing") is None, (
        "FixtureRegistry.get() for an unregistered name should return None"
    )


def test_registry_register_and_get():
    reg = FixtureRegistry()
    defn = FixtureDef(
        name="db",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/c.py",
    )
    reg.register(defn)
    assert reg.get("db") is defn, (
        "FixtureRegistry.get('db') should return the exact FixtureDef that was "
        "registered"
    )


def test_registry_most_local_wins():
    reg = FixtureRegistry()
    root = FixtureDef("db", lambda: 1, False, None, "/root/conftest.py")
    leaf = FixtureDef("db", lambda: 2, False, None, "/root/tests/conftest.py")
    reg.register(root)
    reg.register(leaf)
    assert reg.get("db") is leaf, (
        "FixtureRegistry should prefer the more-local (leaf) fixture over the root "
        "fixture"
    )


def test_registry_get_autouse_returns_only_autouse():
    reg = FixtureRegistry()
    auto = FixtureDef("setup", lambda: None, True, None, "/c.py")
    manual = FixtureDef("db", lambda: None, False, None, "/c.py")
    reg.register(auto)
    reg.register(manual)
    result = reg.get_autouse()
    assert len(result) == 1, (
        f"get_autouse() should return only 1 autouse fixture, got {len(result)}: "
        f"{[d.name for d in result]}"
    )
    assert result[0].name == "setup", (
        f"the autouse fixture should be named 'setup', got {result[0].name!r}"
    )


def test_registry_get_autouse_empty():
    reg = FixtureRegistry()
    assert reg.get_autouse() == [], (
        "get_autouse() on an empty registry should return an empty list"
    )


# ── FixtureSession: function scope ────────────────────────────────────────────


def test_function_scope_new_instance_per_resolve():
    calls = []

    def factory():
        calls.append(1)
        return len(calls)

    reg = FixtureRegistry()
    reg.register(FixtureDef("val", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(val: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    k1, _ = session.resolve_for_test(fn, "t.py")
    k2, _ = session.resolve_for_test(fn, "t.py")
    assert k1["val"] == 1, (
        f"first resolve of function-scope fixture should return 1, got {k1['val']!r}"
    )
    assert k2["val"] == 2, (
        f"second resolve of function-scope fixture should return 2 (new instance), got "
        f"{k2['val']!r}"
    )


# ── Yield teardown ────────────────────────────────────────────────────────────


def test_yield_fixture_function_scope_teardown():
    torn_down = []

    def factory():
        yield "value"
        torn_down.append(True)

    reg = FixtureRegistry()
    reg.register(FixtureDef("val", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(val: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, fn_teardowns = session.resolve_for_test(fn, "t.py")
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


def test_addfinalizer_runs_in_teardown():
    calls = []

    def factory(ctx: Fixture[OxiTestContext]) -> str:  # type: ignore[type-arg]
        ctx.addfinalizer(lambda: calls.append("done"))
        return "val"

    reg = FixtureRegistry()
    reg.register(FixtureDef("thing", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(thing: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, fn_teardowns = session.resolve_for_test(fn, "t.py")
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


def test_dag_fixture_depending_on_fixture():
    reg = FixtureRegistry()
    reg.register(FixtureDef("base", lambda: 10, False, None, "/c.py"))

    def derived(base: Fixture[int]) -> int:  # type: ignore[type-arg]
        return base * 2

    reg.register(FixtureDef("derived", derived, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(derived: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert kwargs["derived"] == 20, (
        f"derived fixture (base*2=20) should be resolved via DAG, got "
        f"{kwargs['derived']!r}"
    )


# ── Autouse ───────────────────────────────────────────────────────────────────


def test_autouse_runs_side_effects_without_being_in_kwargs():
    calls = []

    def setup():
        calls.append(1)

    reg = FixtureRegistry()
    reg.register(FixtureDef("setup", setup, True, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn():
        pass  # does NOT request 'setup'

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert "setup" not in kwargs, (
        f"autouse fixture should not appear in test kwargs (not requested), got keys: "
        f"{list(kwargs)}"
    )
    assert calls == [1], (
        f"autouse fixture factory should be called even when not explicitly requested, "
        f"got calls={calls!r}"
    )


def test_autouse_teardown_still_runs():
    torn_down = []

    def setup():
        yield
        torn_down.append(True)

    reg = FixtureRegistry()
    reg.register(FixtureDef("setup", setup, True, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn():
        pass

    _, fn_teardowns = session.resolve_for_test(fn, "t.py")
    for td in reversed(fn_teardowns):
        td()
    assert torn_down == [True], (
        f"autouse yield fixture teardown should run when fn_teardowns are called, got "
        f"{torn_down!r}"
    )


# ── Error cases ───────────────────────────────────────────────────────────────


def test_missing_fixture_raises_not_found():
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(nonexistent: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    with raises(FixtureNotFoundError) as exc_info:
        session.resolve_for_test(fn, "t.py")
    assert "nonexistent" in str(exc_info.value), (
        f"FixtureNotFoundError message should mention 'nonexistent', got "
        f"{str(exc_info.value)!r}"
    )


def test_cycle_raises_fixture_cycle_error():
    reg = FixtureRegistry()

    def a(b: Fixture[int]) -> int:  # type: ignore[type-arg]
        return b

    def b(a: Fixture[int]) -> int:  # type: ignore[type-arg]
        return a

    reg.register(FixtureDef("a", a, False, None, "/c.py"))
    reg.register(FixtureDef("b", b, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(a: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    with raises(FixtureCycleError):
        session.resolve_for_test(fn, "t.py")


def test_setup_error_raises_fixture_setup_error():
    def bad():
        raise ValueError("oops")

    reg = FixtureRegistry()
    reg.register(FixtureDef("bad", bad, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(bad: Fixture[None]) -> None:  # type: ignore[type-arg]
        pass

    with raises(FixtureSetupError) as exc_info:
        session.resolve_for_test(fn, "t.py")
    assert "bad" in str(exc_info.value), (
        f"FixtureSetupError should mention fixture name 'bad', got "
        f"{str(exc_info.value)!r}"
    )
    assert "oops" in str(exc_info.value), (
        f"FixtureSetupError should include the original error message 'oops', got "
        f"{str(exc_info.value)!r}"
    )


# ── Annotation-based resolution ───────────────────────────────────────────────


def test_fixture_marker_param_resolved_by_name():

    calls = []

    def factory() -> int:
        calls.append(1)
        return 42

    reg = FixtureRegistry()
    reg.register(FixtureDef("val", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(val: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert kwargs["val"] == 42, (
        f"Fixture[int]-annotated param 'val' should be resolved to 42, got "
        f"{kwargs['val']!r}"
    )
    assert len(calls) == 1, (
        f"fixture factory should be called exactly once per resolve, got {len(calls)} "
        "calls"
    )


def test_non_fixture_param_ignored_by_resolver():
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(x: int) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert "x" not in kwargs, (
        f"plain-typed param 'x: int' should not be resolved as a fixture, got "
        f"kwargs={list(kwargs)}"
    )


def test_fixture_test_context_injected_directly():
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(ctx: Fixture[OxiTestContext]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert isinstance(kwargs["ctx"], OxiTestContext), (
        f"Fixture[OxiTestContext] should inject an OxiTestContext instance, got "
        f"{type(kwargs['ctx']).__name__}"
    )


def test_fixture_dep_resolved_via_annotation():

    reg = FixtureRegistry()
    reg.register(FixtureDef("base", lambda: 10, False, None, "/c.py"))

    def derived(base: Fixture[int]) -> int:  # type: ignore[type-arg]
        return base * 3

    reg.register(FixtureDef("derived", derived, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(derived: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert kwargs["derived"] == 30, (
        f"derived fixture (base*3=30) should resolve via annotation-based DAG, got "
        f"{kwargs['derived']!r}"
    )


def test_autouse_not_double_invoked_when_explicitly_requested():

    calls = []

    def setup() -> int:
        calls.append(1)
        return len(calls)

    reg = FixtureRegistry()
    reg.register(FixtureDef("setup", setup, True, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(setup: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, "t.py")
    assert "setup" in kwargs, (
        f"explicitly requested autouse fixture should appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert len(calls) == 1, (
        f"autouse fixture explicitly requested should only be called once (not twice), "
        f"got {len(calls)} calls"
    )


# ── Fixtures class ────────────────────────────────────────────────────────────


def test_fixtures_bare_decorator_registers_def():
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture():
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
    assert defn.params is None, f"default params should be None, got {defn.params!r}"
    assert defn.conftest_path == "", (
        f"default conftest_path should be '', got {defn.conftest_path!r}"
    )


def test_fixtures_autouse():
    fx = oxitest.Fixtures()

    @fx.fixture(autouse=True)
    def auto():
        pass

    assert fx._defs[0].autouse is True, (
        f"@fx.fixture(autouse=True) should set autouse=True, got "
        f"{fx._defs[0].autouse!r}"
    )


def test_fixtures_name_override():
    fx = oxitest.Fixtures()

    @fx.fixture(name="renamed")
    def original():
        pass

    assert fx._defs[0].name == "renamed", (
        f"@fx.fixture(name='renamed') should override the fixture name, got "
        f"{fx._defs[0].name!r}"
    )


def test_fixtures_stamps_fixture_name_for_inject_compat():
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    assert get_metadata(my_fixture).fixture_name == "my_fixture", (
        f"@fx.fixture should register fixture_name='my_fixture' in metadata, "
        f"got {get_metadata(my_fixture).fixture_name!r}"
    )


def test_fixtures_name_override_stamps_fixture_name():
    fx = oxitest.Fixtures()

    @fx.fixture(name="renamed")
    def original():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    assert get_metadata(original).fixture_name == "renamed", (
        f"@fx.fixture(name='renamed') should register fixture_name='renamed'"
        f" in metadata, got {get_metadata(original).fixture_name!r}"
    )


def test_fixtures_does_not_stamp_oxitest_fixture_attr():
    """Fixtures.fixture does NOT stamp _oxitest_fixture (old attribute-scan marker)."""
    fx = oxitest.Fixtures()

    @fx.fixture
    def my_fixture():
        pass

    assert not hasattr(my_fixture, "_oxitest_fixture"), (
        "Fixtures.fixture should NOT stamp the old '_oxitest_fixture' attribute on the "
        "function"
    )


def test_fixtures_multiple_registrations():
    fx = oxitest.Fixtures()

    @fx.fixture
    def a():
        pass

    @fx.fixture
    def b():
        pass

    assert len(fx._defs) == 2, (
        f"two @fx.fixture decorations should produce 2 defs, got {len(fx._defs)}"
    )
    assert {d.name for d in fx._defs} == {"a", "b"}, (
        f"registered fixture names should be {{'a', 'b'}}, got "
        f"{{{', '.join(d.name for d in fx._defs)}}}"
    )


def test_fixture_ref_inner_type_helper_detects_fixture_ref():
    from oxitest import FixtureRef
    from oxitest._bridge.fixtures import _fixture_ref_inner_type

    is_ref, inner = _fixture_ref_inner_type(FixtureRef[int])
    assert is_ref is True, (
        f"_fixture_ref_inner_type(FixtureRef[int]) should return is_ref=True, got "
        f"{is_ref!r}"
    )


def test_fixture_ref_inner_type_rejects_plain_type():
    from oxitest._bridge.fixtures import _fixture_ref_inner_type

    is_ref, inner = _fixture_ref_inner_type(int)
    assert is_ref is False, (
        f"_fixture_ref_inner_type(int) should return is_ref=False, got {is_ref!r}"
    )
    assert inner is None, (
        f"_fixture_ref_inner_type(int) should return inner=None, got {inner!r}"
    )


def test_fixture_ref_inner_type_rejects_fixture_type():
    from oxitest import Fixture
    from oxitest._bridge.fixtures import _fixture_ref_inner_type

    is_ref, inner = _fixture_ref_inner_type(Fixture[int])
    assert is_ref is False, (
        f"_fixture_ref_inner_type(Fixture[int]) should return is_ref=False (not a "
        f"FixtureRef), got {is_ref!r}"
    )


def test_resolve_for_test_skip_names_prevents_resolution():
    """skip_names prevents resolving Fixture[T] params by those names."""
    registry = FixtureRegistry()
    called: list[str] = []

    def my_db() -> int:
        called.append("db")
        return 42

    registry.register(
        FixtureDef(
            name="db",
            func=my_db,
            autouse=False,
            params=None,
            conftest_path="",
        )
    )
    session = FixtureSession(registry)
    session.begin_module("/fake/test.py")

    def test_fn(db: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, "/fake/test.py", skip_names=frozenset({"db"})
    )
    assert "db" not in kwargs, (
        f"'db' should be skipped (in skip_names) and not appear in kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert called == [], "fixture must not be called when name is in skip_names"


def test_unannotated_param_matching_fixture_raises_helpful_error():
    from oxitest._bridge._errors import UnannotatedFixtureParamError

    reg = FixtureRegistry()
    reg.register(FixtureDef("numbers", lambda: [1, 2, 3], False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    # param 'numbers' has no annotation — should raise a helpful error
    def test_fn(numbers) -> None:  # type: ignore[annotation-unchecked]
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, "t.py")

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


def test_wrong_annotation_matching_fixture_raises_helpful_error():
    from oxitest._bridge._errors import UnannotatedFixtureParamError

    reg = FixtureRegistry()
    reg.register(FixtureDef("numbers", lambda: [1, 2, 3], False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    # param 'numbers' has wrong annotation (list[int] instead of Fixture[list[int]])
    def test_fn(numbers: list[int]) -> None:
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, "t.py")

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


def test_oxitest_fixture_sentinel_raises_with_instructions():
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


def test_oxitest_fixture_sentinel_exists_as_attribute():
    assert hasattr(oxitest, "fixture"), (
        "'fixture' should be exported as an attribute of the oxitest module"
    )


# ── TestContext.on_teardown alias ────────────────────────────────────────────


def test_test_context_has_on_teardown_alias():
    assert hasattr(OxiTestContext, "on_teardown"), (
        "OxiTestContext should have an 'on_teardown' attribute (alias for addfinalizer)"
    )
    assert OxiTestContext.on_teardown is OxiTestContext.addfinalizer, (
        "OxiTestContext.on_teardown should be the same method as "
        "OxiTestContext.addfinalizer"
    )


def test_on_teardown_registers_cleanup():
    calls: list[str] = []

    def factory(ctx: Fixture[OxiTestContext]) -> str:  # type: ignore[type-arg]
        ctx.on_teardown(lambda: calls.append("done"))
        return "val"

    reg = FixtureRegistry()
    reg.register(FixtureDef("thing", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(thing: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, fn_teardowns = session.resolve_for_test(fn, "t.py")
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


def test_fixture_decorator_accepts_shared_kwarg():
    from oxitest._bridge.fixtures import Fixtures

    reg_obj = Fixtures()

    @reg_obj.fixture(shared=True)
    def my_val() -> int:
        return 42

    defn = reg_obj._defs[0]
    assert defn.shared is True, (
        f"@fixture(shared=True) should set defn.shared=True, got {defn.shared!r}"
    )
    assert defn.name == "my_val", f"fixture name should be 'my_val', got {defn.name!r}"


def test_fixture_decorator_default_shared_is_false():
    from oxitest._bridge.fixtures import Fixtures

    reg_obj = Fixtures()

    @reg_obj.fixture
    def my_val() -> int:
        return 42

    defn = reg_obj._defs[0]
    assert defn.shared is False, (
        f"default @fixture (no shared=) should have defn.shared=False, got "
        f"{defn.shared!r}"
    )


def test_shared_fixture_is_called_once_across_tests():
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    reg = FixtureRegistry()
    reg.register(FixtureDef("db", factory, False, None, "/c.py", shared=True))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(db: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    k1, _ = session.resolve_for_test(fn, "t.py")
    k2, _ = session.resolve_for_test(fn, "t.py")
    assert len(calls) == 1, f"factory called {len(calls)} times, expected 1"
    # Both resolutions return the same proxy instance (cache hit)
    assert k1["db"] is k2["db"], "same FrozenProxy instance expected on cache hit"


def test_shared_fixture_value_is_wrapped_in_frozen_proxy():
    from oxitest._bridge.proxy import FrozenProxy

    def factory() -> dict[str, int]:
        return {"x": 1}

    reg = FixtureRegistry()
    reg.register(FixtureDef("cfg", factory, False, None, "/c.py", shared=True))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, "t.py")
    assert isinstance(k["cfg"], FrozenProxy), (
        f"shared fixture should be wrapped in a FrozenProxy, got "
        f"{type(k['cfg']).__name__}"
    )


def test_shared_fixture_proxy_raises_on_item_mutation():
    from oxitest._bridge.proxy import SharedFixtureMutationError

    def factory() -> dict[str, int]:
        return {"x": 1}

    reg = FixtureRegistry()
    reg.register(FixtureDef("cfg", factory, False, None, "/c.py", shared=True))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(cfg: Fixture[dict[str, int]]) -> None:
        pass

    k, _ = session.resolve_for_test(fn, "t.py")
    with raises(SharedFixtureMutationError):
        k["cfg"]["x"] = 2  # type: ignore[index]


def test_shared_fixture_teardown_runs_on_end_session():
    torn_down: list[bool] = []

    def factory():  # type: ignore[return]
        yield "v"
        torn_down.append(True)

    reg = FixtureRegistry()
    reg.register(FixtureDef("res", factory, False, None, "/c.py", shared=True))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(res: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    session.resolve_for_test(fn, "t.py")
    session.end_module("t.py")
    assert not torn_down, "teardown must not run at end_module for shared fixtures"
    session.end_session()
    assert torn_down == [True], "teardown must run at end_session"


# ── FixtureNotFoundError namespace field ──────────────────────────────────────


def test_fixture_not_found_error_with_namespace():
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


def test_fixture_not_found_error_without_namespace():
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


def test_fixture_def_has_namespace_field():
    defn = FixtureDef(
        name="conn",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/path/conftest.py",
        namespace="db",
    )
    assert defn.namespace == "db", (
        f"FixtureDef(namespace='db') should store namespace='db', got "
        f"{defn.namespace!r}"
    )


def test_fixture_def_namespace_defaults_to_empty():
    defn = FixtureDef(
        name="conn",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="",
    )
    assert defn.namespace == "", (
        f"FixtureDef without namespace should default to '', got {defn.namespace!r}"
    )


# ── FixtureRegistry.get_in_namespace + has_namespace ─────────────────────────


def test_registry_get_in_namespace_returns_matching_def():
    reg = FixtureRegistry()
    defn = FixtureDef("conn", lambda: 1, False, None, "", namespace="db")
    reg.register(defn)
    result = reg.get_in_namespace("conn", "db")
    assert result is defn, (
        f"get_in_namespace('conn', 'db') should return the registered def, got "
        f"{result!r}"
    )


def test_registry_get_in_namespace_ignores_other_namespace():
    reg = FixtureRegistry()
    db_def = FixtureDef("conn", lambda: 1, False, None, "", namespace="db")
    http_def = FixtureDef("conn", lambda: 2, False, None, "", namespace="http")
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


def test_registry_get_in_namespace_returns_none_when_missing():
    reg = FixtureRegistry()
    defn = FixtureDef("conn", lambda: 1, False, None, "", namespace="db")
    reg.register(defn)
    assert reg.get_in_namespace("conn", "http") is None, (
        "get_in_namespace('conn', 'http') should return None (wrong namespace)"
    )
    assert reg.get_in_namespace("missing", "db") is None, (
        "get_in_namespace('missing', 'db') should return None (fixture not found)"
    )


def test_registry_has_namespace_true():
    reg = FixtureRegistry()
    reg.register(FixtureDef("conn", lambda: 1, False, None, "", namespace="db"))
    assert reg.has_namespace("db") is True, (
        "has_namespace('db') should return True when a fixture with that namespace is "
        "registered"
    )


def test_registry_has_namespace_false():
    reg = FixtureRegistry()
    reg.register(FixtureDef("conn", lambda: 1, False, None, "", namespace="db"))
    assert reg.has_namespace("http") is False, (
        "has_namespace('http') should return False when no fixture with that namespace "
        "exists"
    )


def test_registry_has_namespace_empty_registry():
    reg = FixtureRegistry()
    assert reg.has_namespace("db") is False, (
        "has_namespace('db') should return False on an empty registry"
    )


# ── FixtureSession.get_fixture_in_namespace ──────────────────────────────────


def test_get_fixture_in_namespace_resolves_correct_fixture():
    reg = FixtureRegistry()
    reg.register(FixtureDef("conn", lambda: "db-conn", False, None, "", namespace="db"))
    reg.register(
        FixtureDef("conn", lambda: "http-conn", False, None, "", namespace="http")
    )
    session = FixtureSession(reg)
    session.begin_module("/fake/test.py")

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


def test_get_fixture_in_namespace_raises_not_found_with_namespace():
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("/fake/test.py")

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


def test_fixtures_default_namespace_name_is_empty():
    fx = oxitest.Fixtures()
    assert fx._namespace_name == "", (
        f"Fixtures() with no name should have _namespace_name='', got "
        f"{fx._namespace_name!r}"
    )


def test_fixtures_explicit_name_is_stored():
    fx = oxitest.Fixtures(name="db")
    assert fx._namespace_name == "db", (
        f"Fixtures(name='db') should store _namespace_name='db', got "
        f"{fx._namespace_name!r}"
    )


# ── resolve_for_test bare Fixtures annotation ─────────────────────────────────


def test_resolve_for_test_injects_fixtures_proxy_for_bare_fixtures_annotation():
    """Test that resolve_for_test injects FixturesProxy for bare Fixtures annotation."""
    from oxitest._bridge.proxy_ns import FixturesProxy

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("/fake/test.py")

    # Create the test function with Fixtures annotation
    # Use the actual Fixtures class directly (not string annotation)
    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(test_fn, "/fake/test.py")
    assert "fx" in kwargs, (
        f"Fixtures-annotated param 'fx' should be injected into kwargs, got keys: "
        f"{list(kwargs)}"
    )
    assert isinstance(kwargs["fx"], FixturesProxy), (
        f"injected value for Fixtures annotation should be a FixturesProxy, got "
        f"{type(kwargs['fx']).__name__}"
    )


def test_resolve_for_test_fixtures_proxy_has_correct_session():
    """Verify that FixturesProxy holds reference to the correct session."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("/fake/module.py")

    def test_fn(fx: Fixtures) -> None:
        pass

    kwargs, _ = session.resolve_for_test(test_fn, "/fake/module.py")
    proxy = kwargs["fx"]
    assert proxy._session is session, (
        f"FixturesProxy._session should be the same session used during resolve, got "
        f"{proxy._session!r}"
    )


# ── Shared fixtures introspection ──────────────────────────────────────────────


def test_has_shared_fixtures_empty_registry():
    session = FixtureSession(FixtureRegistry())
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() on an empty registry should return False"
    )


def test_has_shared_fixtures_false_when_no_fixture_is_shared():
    reg = FixtureRegistry()
    reg.register(
        FixtureDef(
            name="db",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=False,
        )
    )
    session = FixtureSession(reg)
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() should return False when no fixture has shared=True"
    )


def test_has_shared_fixtures_true_when_any_fixture_is_shared():
    reg = FixtureRegistry()
    reg.register(
        FixtureDef(
            name="db",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=True,
        )
    )
    session = FixtureSession(reg)
    assert session.has_shared_fixtures() is True, (
        "has_shared_fixtures() should return True when any fixture has shared=True"
    )


def test_has_shared_fixtures_uses_most_local_definition():
    # Root conftest defines db as shared; leaf conftest overrides it as non-shared.
    # The effective definition is the last-registered one (leaf), so
    # has_shared_fixtures() should return False.
    reg = FixtureRegistry()
    root_def = FixtureDef(
        name="db",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/root/conftest.py",
        shared=True,
    )
    leaf_def = FixtureDef(
        name="db",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/root/sub/conftest.py",
        shared=False,
    )
    reg.register(root_def)
    reg.register(leaf_def)
    session = FixtureSession(reg)
    assert session.has_shared_fixtures() is False, (
        "has_shared_fixtures() should use only the most-local definition; "
        "root shared=True overridden by leaf shared=False should return False"
    )


def test_shared_fixture_names_uses_most_local_definition():
    # Root conftest defines db as shared; leaf conftest overrides it as non-shared.
    # shared_fixture_names() should NOT include "db" because the effective definition
    # (defs[-1]) has shared=False.
    reg = FixtureRegistry()
    root_def = FixtureDef(
        name="db",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/root/conftest.py",
        shared=True,
    )
    leaf_def = FixtureDef(
        name="db",
        func=lambda: None,
        autouse=False,
        params=None,
        conftest_path="/root/sub/conftest.py",
        shared=False,
    )
    reg.register(root_def)
    reg.register(leaf_def)
    session = FixtureSession(reg)
    assert session.shared_fixture_names() == [], (
        "shared_fixture_names() should use only the most-local definition; "
        "a root shared=True overridden by leaf shared=False should not appear"
    )


def test_shared_fixture_names_returns_empty_when_no_shared():
    reg = FixtureRegistry()
    reg.register(
        FixtureDef(
            name="client",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=False,
        )
    )
    session = FixtureSession(reg)
    assert session.shared_fixture_names() == [], (
        "shared_fixture_names() should return [] when no fixture has shared=True"
    )


def test_shared_fixture_names_returns_only_shared_names():
    reg = FixtureRegistry()
    reg.register(
        FixtureDef(
            name="db",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=True,
        )
    )
    reg.register(
        FixtureDef(
            name="cache",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=True,
        )
    )
    reg.register(
        FixtureDef(
            name="client",
            func=lambda: None,
            autouse=False,
            params=None,
            conftest_path="/c.py",
            shared=False,
        )
    )
    session = FixtureSession(reg)
    assert session.shared_fixture_names() == ["cache", "db"], (
        "shared_fixture_names() should return only names where shared=True, got "
        f"{session.shared_fixture_names()!r}"
    )


# ── FixtureAccessor ───────────────────────────────────────────────────────────


def test_fixture_accessor_getattr_raises_runtime_error_without_teardown_context():
    """FixtureAccessor.__getattr__ must raise RuntimeError when
    _instantiation_context is set but _teardown_local.fn_teardowns is absent.
    This guards against attribute access that occurs outside an active
    resolve_for_test call.
    """
    import oxitest._bridge.fixtures as _fx_mod
    from oxitest._bridge.fixtures import (
        FixtureAccessor,
        Fixtures,
        _instantiation_context,
    )

    _teardown_local = _fx_mod._teardown_local

    fx_obj = Fixtures()
    accessor = FixtureAccessor("value", fx_obj, lambda: 42)

    # Ensure fn_teardowns is absent on this thread before the test.
    if hasattr(_teardown_local, "fn_teardowns"):
        del _teardown_local.fn_teardowns

    token = _instantiation_context.set((object(), "t.py"))
    try:
        with raises(RuntimeError) as exc_info:
            _ = accessor.value  # non-underscore attribute access triggers __getattr__
        assert "outside an active resolve_for_test call" in str(exc_info.value), (
            "RuntimeError message should mention 'outside an active resolve_for_test "
            f"call', got {str(exc_info.value)!r}"
        )
    finally:
        _instantiation_context.reset(token)


def test_plugin_fixture_provider_injected():
    """A plugin-provided FixtureProvider is resolved via Fixture[T] annotation."""
    import sys
    import types

    from oxitest._bridge.plugin_loader import load_plugins
    from oxitest.plugin import Plugin

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

    provider = FakeDatabaseProvider()
    mod = types.ModuleType("db_plugin")
    mod.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        fixture_providers=[provider]
    )
    sys.modules["db_plugin"] = mod

    try:
        from oxitest._bridge import plugin_loader

        old_registry = plugin_loader._registry
        plugin_loader._registry = load_plugins(["db_plugin"], {})

        registry = plugin_loader.get_registry()
        assert len(registry.fixture_providers) == 1, (
            f"Expected 1 fixture provider, got {len(registry.fixture_providers)}"
        )
        assert registry.fixture_providers[0].fixture_type is FakeDatabase, (
            "Provider fixture_type should be FakeDatabase"
        )
    finally:
        plugin_loader._registry = old_registry
        sys.modules.pop("db_plugin", None)
