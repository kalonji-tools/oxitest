"""Tests for @oxi.parametrize decorator: stamps, cases internals, validation."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from oxitest import parametrize, raises
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge.parametrize import DataclassCases, DictCases


@dataclass(frozen=True)
class AddCase:
    """Frozen dataclass used as parametrize case type in addition tests."""

    x: int
    y: int
    expected: int


# ── Group A: Decorator stamps + validation ─────────────────────────────────────


def test_parametrize_stamps_function() -> None:
    """@parametrize stamps a DataclassCases tuple on the function's metadata."""

    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"parametrize should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"parametrize should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DataclassCases), (
        "parametrize decorator should stamp DataclassCases (dataclass mode)"
    )
    assert "basic" in param_cases.cases, (
        f"'basic' case should be in param_cases.cases, got {list(param_cases.cases)}"
    )
    assert param_cases.cases["basic"] == AddCase(x=1, y=2, expected=3), (
        f"'basic' case value should be AddCase(x=1, y=2, expected=3), "
        f"got {param_cases.cases['basic']!r}"
    )
    assert param_cases.param_type is AddCase, (
        f"param_type should be AddCase, got {param_cases.param_type!r}"
    )


def test_parametrize_multiple_cases() -> None:
    """@parametrize with multiple keyword cases stores all of them in the metadata."""

    @parametrize(
        basic=AddCase(x=1, y=2, expected=3),
        zero_sum=AddCase(x=0, y=0, expected=0),
    )
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"parametrize should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"parametrize should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DataclassCases), (
        "decorator should stamp DataclassCases (dataclass mode)"
    )
    assert len(param_cases.cases) == 2, (
        f"expected 2 parametrize cases, got {len(param_cases.cases)}"
    )
    assert "zero_sum" in param_cases.cases, (
        f"'zero_sum' case should be in param_cases.cases, got {list(param_cases.cases)}"
    )


def test_parametrize_rejects_non_dataclass() -> None:
    """@parametrize rejects a case value that is not a frozen dataclass or dict."""
    with raises(
        TypeError, match="case values must be dicts, frozen dataclass instances"
    ):

        @parametrize(basic=object())
        def test_foo(x: int) -> None:
            pass


def test_parametrize_rejects_non_frozen_dataclass() -> None:
    """@parametrize rejects a mutable (non-frozen) dataclass instance."""

    @dataclass
    class Mutable:
        x: int

    with raises(TypeError, match="frozen=True"):

        @parametrize(basic=Mutable(x=1))
        def test_foo(x: int) -> None:
            pass


def test_parametrize_rejects_empty_cases() -> None:
    """@parametrize with no keyword arguments raises TypeError."""
    with raises(TypeError, match="at least one case"):

        @parametrize()
        def test_foo(x: int) -> None:
            pass


def test_parametrize_rejects_wrong_instance_type() -> None:
    """@parametrize rejects a case that is an instance of a different dataclass type."""

    @dataclass(frozen=True)
    class OtherCase:
        z: int

    with raises(TypeError, match="instance of 'AddCase'"):

        @parametrize(
            good=AddCase(x=1, y=2, expected=3),
            bad=OtherCase(z=1),
        )
        def test_foo(x: int) -> None:
            pass


# ── Group F: Cases internals ──────────────────────────────────────────────────


def test_dict_cases_items_yields_repr_pairs() -> None:
    """DictCases.items() yields (case_id, [(key, repr(val))...]) tuples."""
    dc = DictCases(cases=MappingProxyType({"basic": {"x": 1, "y": 2}}))
    result = list(dc.items())
    assert result == [("basic", [("x", "1"), ("y", "2")])], (
        "ResolvedCases.items() (dict mode) should yield"
        f" (case_id, [(key, repr(val))...]),"
        f" got {result}"
    )


def test_dict_cases_resolve_returns_kwargs_and_empty_fixrefs() -> None:
    """DictCases.resolve() returns the dict as kwargs with an empty fixrefs set."""
    dc = DictCases(cases=MappingProxyType({"basic": {"x": 1, "y": 2}}))
    kwargs, fixrefs = dc.resolve(lambda _x, _y: None, "basic")
    assert kwargs == {"x": 1, "y": 2}, f"resolve should return case dict, got {kwargs}"
    assert fixrefs == frozenset(), f"dict mode fixrefs should be empty, got {fixrefs}"


def test_dataclass_cases_items_yields_field_repr_pairs() -> None:
    """DataclassCases.items() yields (case_id, [(field, repr(val))...]) tuples."""
    dc = DataclassCases(
        cases=MappingProxyType({"basic": AddCase(x=1, y=2, expected=3)}),
        param_type=AddCase,
        fixref_fields=(),
    )
    result = list(dc.items())
    assert result == [("basic", [("x", "1"), ("y", "2"), ("expected", "3")])], (
        "ResolvedCases.items() (dataclass mode) should"
        f" yield (case_id, [(field, repr(val))...]),"
        f" got {result}"
    )


def test_dataclass_cases_resolve_expanded_mode() -> None:
    """DataclassCases.resolve() expands dataclass fields as individual kwargs."""
    dc = DataclassCases(
        cases=MappingProxyType({"basic": AddCase(x=1, y=2, expected=3)}),
        param_type=AddCase,
        fixref_fields=(),
    )

    def test_fn(x: int, y: int, expected: int) -> None:
        pass

    kwargs, fixrefs = dc.resolve(test_fn, "basic")
    assert kwargs == {"x": 1, "y": 2, "expected": 3}, (
        f"expanded mode should spread fields as kwargs, got {kwargs}"
    )
    assert fixrefs == frozenset(), f"no FixtureRef fields, got {fixrefs}"


def test_dataclass_cases_resolve_compact_mode() -> None:
    """DataclassCases.resolve() passes the whole dataclass as one kwarg (compact)."""
    dc = DataclassCases(
        cases=MappingProxyType({"basic": AddCase(x=1, y=2, expected=3)}),
        param_type=AddCase,
        fixref_fields=(),
    )

    def test_fn(params: AddCase) -> None:
        pass

    kwargs, fixrefs = dc.resolve(test_fn, "basic")
    assert kwargs == {"params": AddCase(x=1, y=2, expected=3)}, (
        f"compact mode should pass whole instance as single param, got {kwargs}"
    )
    assert fixrefs == frozenset(), f"no FixtureRef fields, got {fixrefs}"


# ── Group H: Direct call validation ──────────────────────────────────────────


def test_parametrize_rejects_empty_cases_direct() -> None:
    """Calling parametrize() directly with no args raises TypeError."""
    with raises(TypeError, match="at least one case"):
        parametrize()


def test_parametrize_rejects_non_dataclass_non_dict_direct() -> None:
    """parametrize() with a non-dict, non-dataclass value raises TypeError."""
    with raises(
        TypeError, match="case values must be dicts, frozen dataclass instances"
    ):

        @parametrize(basic=42)
        def test_fn(x: int) -> None:
            pass
