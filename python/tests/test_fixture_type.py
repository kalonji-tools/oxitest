from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, get_args, get_origin, get_type_hints

import oxitest as oxi
from oxitest._bridge._fixture_type import Fixture, _FixtureMarker, _FixtureType


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
def test_fixture_type_is_annotated(inner_type, expected_inner):
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


def test_bare_fixture_is_not_annotated():
    assert get_origin(Fixture) is None, (
        f"bare Fixture (without type arg) should have no origin, got "
        f"{get_origin(Fixture)!r}"
    )
    assert Fixture is _FixtureType, (
        f"bare Fixture should be _FixtureType, got {Fixture!r}"
    )


def test_get_type_hints_detects_fixture_marker():
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


def test_two_fixture_markers_are_independent():
    a = Fixture[int]
    b = Fixture[int]
    _, *meta_a = get_args(a)
    _, *meta_b = get_args(b)
    assert meta_a[0] is not meta_b[0], (
        "each Fixture[T] subscript should produce a distinct _FixtureMarker instance"
    )


def test_fixture_ref_database_is_annotated():
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


def test_fixture_ref_marker_distinct_from_fixture_marker():
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


def test_two_fixture_ref_markers_are_independent():
    from oxitest import FixtureRef

    a = FixtureRef[int]
    b = FixtureRef[int]
    _, *meta_a = get_args(a)
    _, *meta_b = get_args(b)
    assert meta_a[0] is not meta_b[0], (
        "each FixtureRef[T] subscript should produce a distinct _FixtureRefMarker "
        "instance"
    )


def test_yields_produces_generator_annotation():
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


def test_yields_does_not_carry_fixture_marker():
    """Yields[T] is a return-type annotation, not an injection signal."""
    from oxitest import Yields
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    is_fx, _ = _fixture_inner_type(Yields[_Database])
    assert not is_fx, (
        "Yields[T] should NOT be recognized as a Fixture[T] injection annotation"
    )


def test_fixture_ref_exported_from_oxitest():
    import oxitest

    assert hasattr(oxitest, "FixtureRef"), (
        "'FixtureRef' should be exported from the oxitest module"
    )
    assert "FixtureRef" in oxitest.__all__, (
        "'FixtureRef' should be listed in oxitest.__all__"
    )


# ── @injectable decorator ──────────────────────────────────────────────────


def test_injectable_decorator_sets_attribute():
    """@injectable sets __oxitest_injectable__ = True on the decorated class."""
    from oxitest import injectable

    @injectable
    class _MyType:
        pass

    assert _MyType.__oxitest_injectable__ is True, (
        "@injectable should set __oxitest_injectable__"
    )


def test_injectable_does_not_affect_undecorated():
    """Undecorated classes lack __oxitest_injectable__."""
    class _Plain:
        pass

    assert not hasattr(_Plain, '__oxitest_injectable__'), (
        "Plain class should not have __oxitest_injectable__"
    )


def test_injectable_class_is_detected_by_fixture_inner_type():
    """_fixture_inner_type returns (True, cls) for @injectable classes."""
    from oxitest import injectable
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    @injectable
    class _MyType:
        pass

    is_fx, inner = _fixture_inner_type(_MyType)
    assert is_fx, "_fixture_inner_type should detect @injectable class"
    assert inner is _MyType, (
        f"inner type should be _MyType, got {inner!r}"
    )


def test_injectable_plain_class_not_detected():
    """_fixture_inner_type returns (False, None) for plain classes."""
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    class _Plain:
        pass

    is_fx, inner = _fixture_inner_type(_Plain)
    assert not is_fx, "Plain class should not be detected as fixture"
    assert inner is None, f"inner should be None, got {inner!r}"


def test_injectable_interop_with_fixture_t():
    """Fixture[T] still works on @injectable classes (redundant but harmless)."""
    from oxitest import Fixture, injectable
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    @injectable
    class _MyType:
        pass

    # Via Fixture[T] — same result as before
    is_fx, inner = _fixture_inner_type(Fixture[_MyType])
    assert is_fx, "Fixture[T] on @injectable class should still work"
    assert inner is _MyType, (
        f"inner should be _MyType, got {inner!r}"
    )


def test_injectable_class_as_test_annotation():
    """An @injectable class used as a parameter annotation is injectable."""
    from oxitest import injectable
    from oxitest._bridge._fixture_registry import _fixture_inner_type

    @injectable
    class _DbSession:
        pass

    # Simulate what happens when a test annotates with the @injectable class
    is_fx, inner = _fixture_inner_type(_DbSession)
    assert is_fx, "@injectable class should be detected"
    assert inner is _DbSession
