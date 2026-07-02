"""Tests for oxi.approx() floating-point comparison."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import oxitest as oxi
from oxitest._bridge._approx import approx
from oxitest._bridge._raises import raises

# ── Scalar: default tolerance ────────────────────────────────────────────────


def test_scalar_default_tolerance_passes():
    assert approx(0.3) == 0.1 + 0.2, "0.1 + 0.2 should approximately equal 0.3"


def test_scalar_default_tolerance_fails():
    assert approx(0.3) != 0.5, "0.5 should not approximately equal 0.3"


def test_scalar_int_coercion():
    assert approx(100.0) == 100, "int 100 should approximately equal float 100.0"


def test_scalar_decimal():
    assert approx(Decimal("0.1")) == Decimal("0.1"), (
        "Decimal('0.1') should approximately equal itself"
    )


# ── Scalar: custom tolerances ────────────────────────────────────────────────


def test_scalar_custom_rel():
    assert approx(1.001, rel=1e-2) == 1.0, "1.0 should be within 1% of 1.001"


def test_scalar_custom_rel_too_tight():
    assert approx(1.001, rel=1e-05) != 1.0, "1.0 should not be within 0.001% of 1.001"


def test_scalar_custom_abs():
    assert approx(1e-13, abs=1e-12) == 0.0, "0.0 should be within abs=1e-12 of 1e-13"


def test_scalar_custom_abs_too_tight():
    assert approx(1e-10, abs=1e-12) != 0.0, (
        "0.0 should not be within abs=1e-12 of 1e-10"
    )


def test_scalar_both_rel_and_abs():
    # rel dominates for large values, abs dominates near zero
    assert approx(1000.0005, rel=1e-6, abs=1e-12) == 1000.0, (
        "rel tolerance should allow 1000.0 ≈ 1000.0005"
    )


# ── Scalar: special IEEE 754 values ─────────────────────────────────────────


def test_scalar_inf_equal():
    assert float("inf") == approx(float("inf")), "inf should equal inf"


def test_scalar_neg_inf_not_equal_pos_inf():
    assert float("-inf") != approx(float("inf")), "-inf should not equal inf"


def test_scalar_nan_default_false():
    assert float("nan") != approx(float("nan")), "nan should not equal nan by default"


def test_scalar_nan_ok_true():
    assert float("nan") == approx(float("nan"), nan_ok=True), (
        "nan should equal nan when nan_ok=True"
    )


def test_scalar_nan_vs_number():
    assert float("nan") != approx(1.0), "nan should not equal a number"


def test_scalar_number_vs_nan():
    assert approx(float("nan")) != 1.0, "approx(nan) should not equal a number"


# ── Scalar: near-zero (abs tolerance dominates) ─────────────────────────────


def test_scalar_near_zero():
    # rel tolerance on a near-zero expected is tiny; abs tolerance saves us
    assert approx(0.0, abs=1e-12) == 1e-13, (
        "near-zero value should match via abs tolerance"
    )


# ── Sequence ─────────────────────────────────────────────────────────────────


def test_sequence_list_match():
    assert approx([0.3, 0.7]) == [0.1 + 0.2, 0.3 + 0.4], (
        "list elements should match approximately"
    )


def test_sequence_tuple_match():
    assert approx((0.3, 0.7)) == (0.1 + 0.2, 0.3 + 0.4), (
        "tuple elements should match approximately"
    )


def test_sequence_length_mismatch():
    assert approx([1.0]) != [1.0, 2.0], (
        "sequences of different lengths should not match"
    )


def test_sequence_nested():
    assert approx([[0.3], [0.7]]) == [[0.1 + 0.2], [0.3 + 0.4]], (
        "nested sequences should match element-wise"
    )


def test_sequence_mixed_numeric_types():
    assert [1, 0.2, Decimal("0.3")] == approx([1.0, 0.2, Decimal("0.3")]), (
        "mixed int/float/Decimal sequences should match"
    )


# ── Mapping ──────────────────────────────────────────────────────────────────


def test_mapping_match():
    assert approx({"x": 0.3, "y": 0.7}) == {"x": 0.1 + 0.2, "y": 0.3 + 0.4}, (
        "dict values should match approximately"
    )


def test_mapping_key_mismatch():
    assert approx({"b": 1.0}) != {"a": 1.0}, (
        "dicts with different keys should not match"
    )


def test_mapping_nested():
    assert approx({"outer": {"inner": 0.3}}) == {"outer": {"inner": 0.1 + 0.2}}, (
        "nested dicts should match value-wise"
    )


def test_mapping_with_sequence_values():
    assert approx({"pts": [0.3, 0.7]}) == {"pts": [0.1 + 0.2, 0.3 + 0.4]}, (
        "dict with list values should match element-wise"
    )


# ── Error guards ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrderingCase:
    op: Callable[[Any, Any], Any]


@oxi.parametrize(
    lt=OrderingCase(op=operator.lt),
    le=OrderingCase(op=operator.le),
    gt=OrderingCase(op=operator.gt),
    ge=OrderingCase(op=operator.ge),
)
def test_ordering_raises(case: OrderingCase):
    with raises(TypeError, match="ordering"):
        case.op(approx(1.0), 2.0)


def test_approx_vs_approx_raises():
    with raises(TypeError, match="cannot be compared"):
        approx(1.0) == approx(2.0)


@dataclass(frozen=True)
class BadInputCase:
    value: object
    match: str


@oxi.parametrize(
    set_input=BadInputCase(value={1.0, 2.0}, match="sets"),
    string=BadInputCase(value="hello", match="number, sequence, or mapping"),
    bytes_input=BadInputCase(value=b"hello", match="number, sequence, or mapping"),
)
def test_bad_input_raises(case: BadInputCase):
    with raises(TypeError, match=case.match):
        approx(case.value)


def test_non_numeric_eq_returns_not_implemented():
    # When the other operand is not numeric, __eq__ returns NotImplemented,
    # which Python turns into False.
    assert approx(1.0) != "hello", (
        "non-numeric comparison should return NotImplemented (treated as False)"
    )


# ── Repr ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReprCase:
    value: object
    expected_substrings: tuple[str, ...]


@oxi.parametrize(
    scalar=ReprCase(value=0.3, expected_substrings=("0.3", "\u00b1")),
    sequence=ReprCase(
        value=[0.1, 0.2],
        expected_substrings=("approx(", "0.1", "0.2"),
    ),
    mapping=ReprCase(
        value={"x": 0.1},
        expected_substrings=("approx(", "'x'", "0.1"),
    ),
    nan=ReprCase(value=float("nan"), expected_substrings=("nan",)),
    zero=ReprCase(value=0.0, expected_substrings=("0.0", "\u00b1")),
)
def test_repr(case: ReprCase):
    r = repr(approx(case.value))
    for substring in case.expected_substrings:
        assert substring in r, f"repr should contain {substring!r}, got {r!r}"


# ── Public API ───────────────────────────────────────────────────────────────


def test_import_from_public_api():
    import oxitest as oxi

    assert oxi.approx(0.3) == 0.1 + 0.2, "approx should be accessible from public API"


def test_approx_base_isinstance():
    import oxitest as oxi

    assert isinstance(oxi.approx(1.0), oxi.ApproxBase), (
        "approx() should return an ApproxBase instance"
    )
