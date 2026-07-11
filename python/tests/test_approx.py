"""Tests for oxi.approx() floating-point comparison."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import oxitest as oxi
from oxitest import ApproxBase, approx, raises

# ── Scalar: default tolerance ────────────────────────────────────────────────


def test_scalar_default_tolerance_passes() -> None:
    """Default relative tolerance makes 0.1 + 0.2 compare equal to approx(0.3)."""
    assert approx(0.3) == 0.1 + 0.2, (
        "default rel tolerance must absorb IEEE 754 floating-point representation error"
    )


def test_scalar_default_tolerance_fails() -> None:
    """Values outside the default relative tolerance are not approximately equal."""
    assert approx(0.3) != 0.5, (
        "values outside the tolerance band must compare unequal to prevent"
        " false-positive test passes"
    )


def test_scalar_int_coercion() -> None:
    """Integers are accepted as the comparison value and coerced to float."""
    assert approx(100.0) == 100, (
        "int-to-float coercion must be seamless so callers can mix literal types"
        " without manual casting"
    )


def test_scalar_decimal() -> None:
    """Decimal values are supported and compare equal to themselves via approx."""
    assert approx(Decimal("0.1")) == Decimal("0.1"), (
        "Decimal support lets financial/fixed-point code use approx without converting"
        " to float first"
    )


# ── Scalar: custom tolerances ────────────────────────────────────────────────


def test_scalar_custom_rel() -> None:
    """A custom rel tolerance of 1% allows 1.001 to match 1.0."""
    assert approx(1.001, rel=1e-2) == 1.0, (
        "custom rel tolerance lets users widen the band for domains where 1% drift is"
        " acceptable"
    )


def test_scalar_custom_rel_too_tight() -> None:
    """A very tight rel tolerance rejects 1.0 as not approximately equal to 1.001."""
    assert approx(1.001, rel=1e-05) != 1.0, (
        "tightening rel tolerance must actually reject values outside the narrower band"
    )


def test_scalar_custom_abs() -> None:
    """A custom abs tolerance dominates near zero and allows 1e-13 to match 0.0."""
    assert approx(1e-13, abs=1e-12) == 0.0, (
        "abs tolerance dominates near zero where rel tolerance collapses to a"
        " meaninglessly small band"
    )


def test_scalar_custom_abs_too_tight() -> None:
    """A tight abs tolerance rejects 0.0 as not approximately equal to 1e-10."""
    assert approx(1e-10, abs=1e-12) != 0.0, (
        "tight abs tolerance must still reject values that exceed it, even near zero"
    )


def test_scalar_both_rel_and_abs() -> None:
    """When both rel and abs are set, the rel tolerance dominates for large values."""
    # rel dominates for large values, abs dominates near zero
    assert approx(1000.0005, rel=1e-6, abs=1e-12) == 1000.0, (
        "rel tolerance must dominate for large values since abs=1e-12 is negligible at"
        " this magnitude"
    )


# ── Scalar: special IEEE 754 values ─────────────────────────────────────────


def test_scalar_inf_equal() -> None:
    """Positive infinity compares equal to approx(inf) per IEEE 754 identity."""
    assert float("inf") == approx(float("inf")), (
        "IEEE 754 defines inf == inf, so approx must preserve this identity rather than"
        " applying tolerance"
    )


def test_scalar_neg_inf_not_equal_pos_inf() -> None:
    """Negative infinity does not compare equal to positive infinity."""
    assert float("-inf") != approx(float("inf")), (
        "opposite infinities represent divergence in opposite directions -- no finite"
        " tolerance can bridge them"
    )


def test_scalar_nan_default_false() -> None:
    """By default nan != nan, matching standard IEEE 754 semantics."""
    assert float("nan") != approx(float("nan")), (  # noqa: PLW0177
        "NaN is unordered per IEEE 754 so default behavior must reject NaN==NaN to"
        " catch computation errors"
    )


def test_scalar_nan_ok_true() -> None:
    """nan_ok=True makes nan compare equal to nan, useful for placeholder checks."""
    assert float("nan") == approx(float("nan"), nan_ok=True), (  # noqa: PLW0177
        "nan_ok=True lets callers assert NaN placeholders match without masking real"
        " computation errors"
    )


def test_scalar_nan_vs_number() -> None:
    """NaN on the left-hand side is not approximately equal to any finite number."""
    assert float("nan") != approx(1.0), (  # noqa: PLW0177
        "NaN is unordered relative to all finite numbers per IEEE 754, so no tolerance"
        " can bridge the gap"
    )


def test_scalar_number_vs_nan() -> None:
    """approx(nan) is not approximately equal to any finite number."""
    assert approx(float("nan")) != 1.0, (
        "approx(NaN) on the expected side must also reject finite numbers -- symmetry"
        " with the LHS case"
    )


# ── Scalar: near-zero (abs tolerance dominates) ─────────────────────────────


def test_scalar_near_zero() -> None:
    """Near-zero values match via the abs tolerance when rel tolerance is too tight."""
    # rel tolerance on a near-zero expected is tiny; abs tolerance saves us
    assert approx(0.0, abs=1e-12) == 1e-13, (
        "rel tolerance on a near-zero expected is vanishingly small, so abs tolerance"
        " must rescue the comparison"
    )


# ── Sequence ─────────────────────────────────────────────────────────────────


def test_sequence_list_match() -> None:
    """List elements are compared element-wise using the default tolerance."""
    assert approx([0.3, 0.7]) == [0.1 + 0.2, 0.3 + 0.4], (
        "element-wise comparison lets users assert entire result vectors without"
        " per-element approx wrapping"
    )


def test_sequence_tuple_match() -> None:
    """Tuples are supported as sequence input and compared element-wise."""
    assert approx((0.3, 0.7)) == (0.1 + 0.2, 0.3 + 0.4), (
        "tuples are common return types from numeric functions -- approx must handle"
        " them without list conversion"
    )


def test_sequence_length_mismatch() -> None:
    """Sequences of different lengths are never considered approximately equal."""
    assert approx([1.0]) != [1.0, 2.0], (
        "length mismatch is a structural error, not a tolerance issue -- must fail fast"
        " to catch shape bugs"
    )


def test_sequence_nested() -> None:
    """Nested lists are compared recursively, each element against its approx twin."""
    assert approx([[0.3], [0.7]]) == [[0.1 + 0.2], [0.3 + 0.4]], (
        "recursive descent into nested structures avoids forcing callers to flatten"
        " before comparison"
    )


def test_sequence_mixed_numeric_types() -> None:
    """Mixed int, float, and Decimal elements in a sequence all compare correctly."""
    assert [1, 0.2, Decimal("0.3")] == approx([1.0, 0.2, Decimal("0.3")]), (
        "real-world sequences mix numeric types -- approx must coerce per-element"
        " rather than rejecting the whole sequence"
    )


# ── Mapping ──────────────────────────────────────────────────────────────────


def test_mapping_match() -> None:
    """Dict values are compared approximately, key by key."""
    assert approx({"x": 0.3, "y": 0.7}) == {"x": 0.1 + 0.2, "y": 0.3 + 0.4}, (
        "key-by-key value comparison lets callers assert named result fields without"
        " decomposing the dict"
    )


def test_mapping_key_mismatch() -> None:
    """Dicts with different key sets are never approximately equal."""
    assert approx({"b": 1.0}) != {"a": 1.0}, (
        "key mismatch is a structural error -- silently ignoring missing keys would"
        " mask field-name typos"
    )


def test_mapping_nested() -> None:
    """Nested dicts are compared recursively using approximate equality."""
    assert approx({"outer": {"inner": 0.3}}) == {"outer": {"inner": 0.1 + 0.2}}, (
        "recursive descent into nested dicts mirrors the sequence behavior and avoids"
        " manual flattening"
    )


def test_mapping_with_sequence_values() -> None:
    """Dict values that are lists are compared element-wise via approx."""
    assert approx({"pts": [0.3, 0.7]}) == {"pts": [0.1 + 0.2, 0.3 + 0.4]}, (
        "mixed dict-of-lists structures are common in data pipelines -- approx must"
        " recurse through both layers"
    )


# ── Error guards ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrderingCase:
    """Parametrize case for ordering-operator rejection tests."""

    op: Callable[[Any, Any], Any]


@oxi.parametrize(
    lt=OrderingCase(op=operator.lt),
    le=OrderingCase(op=operator.le),
    gt=OrderingCase(op=operator.gt),
    ge=OrderingCase(op=operator.ge),
)
def test_ordering_raises(case: OrderingCase) -> None:
    """Ordering comparisons (<, <=, >, >=) against approx always raise TypeError."""
    with raises(TypeError, match="ordering"):
        case.op(approx(1.0), 2.0)


def test_approx_vs_approx_raises() -> None:
    """Comparing two approx instances raises TypeError."""
    with raises(TypeError, match="cannot be compared"):
        approx(1.0) == approx(2.0)  # noqa: B015 — triggers __eq__ inside raises()


@dataclass(frozen=True)
class BadInputCase:
    """Parametrize case for unsupported-input TypeError tests."""

    value: object
    match: str


@oxi.parametrize(
    set_input=BadInputCase(value={1.0, 2.0}, match="sets"),
    string=BadInputCase(value="hello", match="number, sequence, or mapping"),
    bytes_input=BadInputCase(value=b"hello", match="number, sequence, or mapping"),
)
def test_bad_input_raises(case: BadInputCase) -> None:
    """Unsupported input types (sets, strings, bytes) raise TypeError immediately."""
    with raises(TypeError, match=case.match):
        approx(case.value)


def test_non_numeric_eq_returns_not_implemented() -> None:
    """Non-numeric operands cause __eq__ to return NotImplemented, yielding False."""
    # When the other operand is not numeric, __eq__ returns NotImplemented,
    # which Python turns into False.
    assert approx(1.0) != "hello", (
        "non-numeric operands must yield NotImplemented so Python falls back to False"
        " rather than crashing"
    )


# ── Repr ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReprCase:
    """Parametrize case for repr output substring checks."""

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
def test_repr(case: ReprCase) -> None:
    """repr(approx(...)) includes expected substrings for each input type."""
    r = repr(approx(case.value))
    for substring in case.expected_substrings:
        assert substring in r, (
            f"repr must include {substring!r} so assertion failure output shows the"
            f" tolerance band and value"
        )


# ── Public API ───────────────────────────────────────────────────────────────


def test_import_from_public_api() -> None:
    """The approx function is accessible via the top-level oxitest package."""
    assert oxi.approx(0.3) == 0.1 + 0.2, (
        "top-level import must work so users write oxi.approx() without hunting"
        " internal module paths"
    )


def test_approx_base_isinstance() -> None:
    """approx() returns an ApproxBase instance, making isinstance checks reliable."""
    assert isinstance(oxi.approx(1.0), ApproxBase), (
        "isinstance checks against ApproxBase let plugins and extensions detect approx"
        " wrappers at runtime"
    )
