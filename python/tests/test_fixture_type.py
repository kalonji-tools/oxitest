from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, get_args, get_origin, get_type_hints

import oxitest as oxi
from oxitest import Fixture
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureScope,
    PluginSource,
)
from oxitest._bridge._fixture_type import _FixtureMarker, _FixtureType


class _Database:
    pass


class _TestCtx:
    pass


@dataclass(frozen=True)
class Case:
    inner_type: type
    expected_inner: type


@oxi.parametrize(
    database=Case(inner_type=_Database, expected_inner=_Database),
    none=Case(inner_type=type(None), expected_inner=type(None)),
    test_context=Case(inner_type=_TestCtx, expected_inner=_TestCtx),
)
def test_fixture_type_is_annotated(inner_type, expected_inner) -> None:
    result = Fixture[inner_type]
    assert get_origin(result) is Annotated, (
        f"Fixture[{inner_type.__name__}] should have Annotated origin, "
        f"got {get_origin(result)!r}"
    )
    inner, *meta = get_args(result)
    assert inner is expected_inner, (
        f"Fixture[{inner_type.__name__}] inner type should be {expected_inner!r}, "
        f"got {inner!r}"
    )
    assert any(isinstance(m, _FixtureMarker) for m in meta), (
        f"Fixture[{inner_type.__name__}] metadata should contain a _FixtureMarker"
    )


def test_bare_fixture_is_not_annotated() -> None:
    assert get_origin(Fixture) is None, (
        f"bare Fixture (without type arg) should have no origin, got "
        f"{get_origin(Fixture)!r}"
    )
    assert Fixture is _FixtureType, (
        f"bare Fixture should be _FixtureType, got {Fixture!r}"
    )


def test_get_type_hints_detects_fixture_marker() -> None:
    def fn(db: Fixture[_Database], x: int) -> None:
        pass

    hints = get_type_hints(fn, include_extras=True)
    db_hint = hints["db"]
    assert get_origin(db_hint) is Annotated, (
        f"get_type_hints should resolve Fixture[_Database] to Annotated, got "
        f"{get_origin(db_hint)!r}"
    )
    inner, *meta = get_args(db_hint)
    assert inner is _Database, (
        f"inner type of db hint should be _Database, got {inner!r}"
    )
    assert any(isinstance(m, _FixtureMarker) for m in meta), (
        "db hint metadata should contain a _FixtureMarker"
    )
    assert get_origin(hints["x"]) is not Annotated, (
        "plain 'int' annotation should NOT have Annotated origin"
    )


def test_two_fixture_markers_are_independent() -> None:
    a = Fixture[int]
    b = Fixture[int]
    _, *meta_a = get_args(a)
    _, *meta_b = get_args(b)
    assert meta_a[0] is not meta_b[0], (
        "each Fixture[T] subscript should produce a distinct _FixtureMarker instance"
    )


def test_fixture_ref_database_is_annotated() -> None:
    from oxitest import FixtureRef
    from oxitest._bridge._fixture_type import _FixtureRefMarker

    result = FixtureRef[_Database]
    assert get_origin(result) is Annotated, (
        f"FixtureRef[_Database] should have Annotated origin, got "
        f"{get_origin(result)!r}"
    )
    inner, *meta = get_args(result)
    # inner should be Callable[..., _Database]
    assert get_origin(inner) is not None, (
        f"FixtureRef[_Database] inner should be a generic alias (Callable), got "
        f"{inner!r}"
    )
    assert get_args(inner)[-1] is _Database, (
        f"FixtureRef[_Database] inner return type should be _Database, got "
        f"{get_args(inner)[-1]!r}"
    )
    assert any(isinstance(m, _FixtureRefMarker) for m in meta), (
        "FixtureRef[_Database] metadata should contain a _FixtureRefMarker"
    )


def test_fixture_ref_marker_distinct_from_fixture_marker() -> None:
    from oxitest import Fixture, FixtureRef
    from oxitest._bridge._fixture_type import _FixtureMarker, _FixtureRefMarker

    _, *meta_fix = get_args(Fixture[int])
    _, *meta_ref = get_args(FixtureRef[int])
    assert not any(isinstance(m, _FixtureRefMarker) for m in meta_fix), (
        "Fixture[int] metadata should NOT contain _FixtureRefMarker"
    )
    assert not any(isinstance(m, _FixtureMarker) for m in meta_ref), (
        "FixtureRef[int] metadata should NOT contain _FixtureMarker"
    )


def test_two_fixture_ref_markers_are_independent() -> None:
    from oxitest import FixtureRef

    a = FixtureRef[int]
    b = FixtureRef[int]
    _, *meta_a = get_args(a)
    _, *meta_b = get_args(b)
    assert meta_a[0] is not meta_b[0], (
        "each FixtureRef[T] subscript should produce a distinct _FixtureRefMarker "
        "instance"
    )


def test_yields_produces_generator_annotation() -> None:
    """Yields[T] expands to Generator[T, None, None]."""
    from collections.abc import Generator
    from typing import get_args, get_origin

    from oxitest import Yields

    result = Yields[_Database]
    assert get_origin(result) is Generator, (
        f"Yields[_Database] should have Generator origin, got {get_origin(result)!r}"
    )
    args = get_args(result)
    assert args[0] is _Database, (
        f"Yields[_Database] yield type should be _Database, got {args[0]!r}"
    )
    assert args[1] is None, (
        f"Yields[_Database] SendType should be None, got {args[1]!r}"
    )
    assert args[2] is None, (
        f"Yields[_Database] ReturnType should be None, got {args[2]!r}"
    )


def test_yields_does_not_carry_fixture_marker() -> None:
    """Yields[T] is a return-type annotation, not an injection signal."""
    from oxitest import Yields
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    is_fx, _ = _fixture_inner_type(Yields[_Database])
    assert not is_fx, (
        "Yields[T] should NOT be recognized as a Fixture[T] injection annotation"
    )


def test_fixture_ref_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "FixtureRef"), (
        "'FixtureRef' should be exported from the oxitest module"
    )
    assert "FixtureRef" in oxitest.__all__, (
        "'FixtureRef' should be listed in oxitest.__all__"
    )


def test_injectable_stamps_attribute() -> None:
    from oxitest._bridge._fixture_type import injectable

    @injectable
    class MyType:
        pass

    assert getattr(MyType, "__oxitest_injectable__", False) is True, (
        "@injectable should stamp __oxitest_injectable__ = True on the class"
    )


def test_injectable_returns_the_class_unchanged() -> None:
    from oxitest._bridge._fixture_type import injectable

    @injectable
    class MyType:
        pass

    assert MyType.__name__ == "MyType", (
        "@injectable should return the original class, not a wrapper"
    )


def test_fixture_inner_type_detects_injectable() -> None:
    from oxitest._bridge._fixture_registry import _fixture_inner_type
    from oxitest._bridge._fixture_type import injectable

    @injectable
    class DbSession:
        pass

    is_fx, inner = _fixture_inner_type(DbSession)
    assert is_fx is True, (
        "_fixture_inner_type should return is_fixture=True for @injectable classes"
    )
    assert inner is DbSession, (
        "_fixture_inner_type should return the @injectable class itself as inner type"
    )


def test_fixture_inner_type_ignores_plain_class() -> None:
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    class PlainClass:
        pass

    is_fx, _ = _fixture_inner_type(PlainClass)
    assert is_fx is False, (
        "_fixture_inner_type should return is_fixture=False for plain "
        "(non-injectable) classes"
    )


def test_fixture_wrapper_on_injectable_still_works() -> None:
    from oxitest._bridge._fixture_registry import _fixture_inner_type
    from oxitest._bridge._fixture_type import Fixture, injectable

    @injectable
    class DbSession:
        pass

    is_fx, inner = _fixture_inner_type(Fixture[DbSession])
    assert is_fx is True, (
        "Fixture[T] wrapping an @injectable type should still be detected as a fixture"
    )
    assert inner is DbSession, (
        "Fixture[T] wrapping an @injectable type should unwrap to the inner class"
    )


# ── FixtureScope ─────────────────────────────────────────────────────────────


def test_fixture_scope_values() -> None:
    """FixtureScope has three tiers: each, shared, session."""
    assert FixtureScope.EACH == "each", "EACH should be 'each'"
    assert FixtureScope.SHARED == "shared", "SHARED should be 'shared'"
    assert FixtureScope.SESSION == "session", "SESSION should be 'session'"
    assert len(FixtureScope) == 3, "FixtureScope should have exactly 3 members"


# ── Source variants ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceCase:
    label: str
    source: object
    field: str
    expected: object


@oxi.parametrize(
    conftest=SourceCase(
        label="conftest",
        source=ConftestSource(func=lambda: None, conftest_path="/conftest.py"),
        field="conftest_path",
        expected="/conftest.py",
    ),
    plugin=SourceCase(
        label="plugin",
        source=PluginSource(provider=object(), plugin_module="my_plugin"),
        field="plugin_module",
        expected="my_plugin",
    ),
    builtin=SourceCase(
        label="builtin",
        source=BuiltinSource(impl_cls=int),
        field="impl_cls",
        expected=int,
    ),
)
def test_source_variant_stores_fields(case: SourceCase) -> None:
    """Source variant dataclasses store their fields correctly."""
    actual = getattr(case.source, case.field)
    assert actual == case.expected, f"{case.label} source should store {case.field}"
