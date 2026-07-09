"""Tests that exercise composed parametrize code paths for coverage.

These are real test functions using @oxi.parametrize with partial() — not
unit tests about the parametrize internals. When oxitest discovers and runs
this file, it exercises the composition pipeline: partial() construction,
_PartialCases stacking, cartesian expansion in _expand_item(), and
_resolve_composed() at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest
from oxitest import Fixture, FixtureRef, partial

# ── Shared dataclasses ──────────────────────────────────────────────────────


@dataclass
class SignCase:
    """Parameters for a sign+value composition test case."""

    sign: str
    value: int


@dataclass
class CompactCase:
    """Parameters for a compact-mode composition test case."""

    a: int
    b: int


@dataclass
class LabelCase:
    """Parameters for a three-layer composition test case."""

    label: str
    value: int
    multiplier: int


@dataclass
class DbCase:
    """Parameters for a FixtureRef-in-composition test case."""

    db: FixtureRef[str]
    expected: str


# ── Module-level fixture registry (for FixtureRef test) ────────────────────

_fixtures = oxitest.Fixtures()  # oxitest: allow[registrar-in-test-module]


@_fixtures.fixture
def pg_db() -> str:
    """Provide a database connection string for parametrize composition tests."""
    return "postgres"


# ── Expanded mode (2x2) ────────────────────────────────────────────────────
# Each layer provides disjoint fields; assertions depend only on a single
# layer's value to avoid cross-layer math complications.


@oxitest.parametrize(
    pos=partial(SignCase, sign="positive"),
    neg=partial(SignCase, sign="negative"),
)
@oxitest.parametrize(
    small=partial(SignCase, value=1),
    large=partial(SignCase, value=100),
)
def test_expanded_mode(sign: str, value: int) -> None:
    """Stacked @parametrize decorators expand into a cartesian product of fields."""
    assert isinstance(sign, str), f"sign should be a str, got {type(sign)!r}"
    assert isinstance(value, int), f"value should be an int, got {type(value)!r}"
    assert value > 0, f"value should be positive, got {value!r}"
    assert sign in ("positive", "negative"), f"unexpected sign {sign!r}"


# ── Compact mode (2x2) ─────────────────────────────────────────────────────
# Function receives the whole composed dataclass instance as a single param.


@oxitest.parametrize(
    left=partial(CompactCase, a=10),
    right=partial(CompactCase, a=20),
)
@oxitest.parametrize(
    small=partial(CompactCase, b=1),
    big=partial(CompactCase, b=100),
)
def test_compact_mode(case: CompactCase) -> None:
    """Compact mode delivers the fully-composed dataclass as a single parameter."""
    assert isinstance(case, CompactCase), (
        f"compact mode should receive a CompactCase, got {type(case)!r}"
    )
    assert case.a in (10, 20), f"unexpected a={case.a!r}"
    assert case.b in (1, 100), f"unexpected b={case.b!r}"


# ── 3-layer composition ────────────────────────────────────────────────────
# Three stacked decorators produce a 1x1x1 = 1 expanded case.


@oxitest.parametrize(
    fast=partial(LabelCase, label="fast"),
)
@oxitest.parametrize(
    v1=partial(LabelCase, value=1),
)
@oxitest.parametrize(
    x2=partial(LabelCase, multiplier=2),
)
def test_three_layers(label: str, value: int, multiplier: int) -> None:
    """Three stacked @parametrize decorators compose into a single expanded case."""
    assert isinstance(label, str), f"label should be str, got {type(label)!r}"
    assert value == 1, f"expected value=1, got {value!r}"
    assert multiplier == 2, f"expected multiplier=2, got {multiplier!r}"
    assert label == "fast", f"expected label='fast', got {label!r}"


# ── FixtureRef in composition ──────────────────────────────────────────────
# One layer provides a FixtureRef field; the other provides a plain field.
# The module-level Fixtures() instance is registered during collection so
# pg_db is resolvable from the session without a conftest.py.


@oxitest.parametrize(
    pg=partial(DbCase, db=pg_db),
)
@oxitest.parametrize(
    check=partial(DbCase, expected="postgres"),
)
def test_fixture_ref_in_composition(db: Fixture[str], expected: str) -> None:
    """FixtureRef fields in a composed dataclass resolve at runtime."""
    assert db == expected, f"db fixture value {db!r} should match expected {expected!r}"
