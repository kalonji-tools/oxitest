from __future__ import annotations

from typing import Annotated, get_args, get_origin, get_type_hints

from oxitest._bridge._fixture_type import Fixture, _FixtureMarker, _FixtureType


class _Database:
    pass


class _TestCtx:
    pass


def test_fixture_database_is_annotated():
    result = Fixture[_Database]
    assert get_origin(result) is Annotated, (
        f"Fixture[_Database] should have Annotated origin, got {get_origin(result)!r}"
    )
    inner, *meta = get_args(result)
    assert inner is _Database, (
        f"Fixture[_Database] inner type should be _Database, got {inner!r}"
    )
    assert any(isinstance(m, _FixtureMarker) for m in meta), (
        "Fixture[_Database] metadata should contain a _FixtureMarker"
    )


def test_fixture_none_is_annotated():
    result = Fixture[None]
    assert get_origin(result) is Annotated, (
        f"Fixture[None] should have Annotated origin, got {get_origin(result)!r}"
    )
    inner, *meta = get_args(result)
    assert inner is type(None), (
        f"Fixture[None] inner type should be NoneType, got {inner!r}"
    )
    assert any(isinstance(m, _FixtureMarker) for m in meta), (
        "Fixture[None] metadata should contain a _FixtureMarker"
    )


def test_fixture_test_context_is_annotated():
    result = Fixture[_TestCtx]
    assert get_origin(result) is Annotated, (
        f"Fixture[_TestCtx] should have Annotated origin, got {get_origin(result)!r}"
    )
    inner, *meta = get_args(result)
    assert inner is _TestCtx, (
        f"Fixture[_TestCtx] inner type should be _TestCtx, got {inner!r}"
    )
    assert any(isinstance(m, _FixtureMarker) for m in meta), (
        "Fixture[_TestCtx] metadata should contain a _FixtureMarker"
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
    from oxitest._bridge.fixtures import _fixture_inner_type

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
