"""Approximate floating-point comparison for assertions."""

from __future__ import annotations

__all__ = ["ApproxBase", "approx"]

import builtins as _builtins
import math
import numbers
from collections.abc import Iterable, Mapping, Sized
from typing import Any

_ORDERING_MSG = "approx() does not support ordering comparisons"
_APPROX_VS_APPROX_MSG = "approx() cannot be compared to another approx()"

# Use builtins.abs to avoid shadowing from the `abs` parameter name.
_builtins_abs = _builtins.abs


class ApproxBase:
    """Abstract base for approximate comparisons."""

    def __init__(
        self,
        expected: Any,
        *,
        rel: float = 1e-6,
        abs: float = 1e-12,
        nan_ok: bool = False,
    ) -> None:
        self._expected = expected
        self._rel = rel
        self._abs = abs
        self._nan_ok = nan_ok

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ApproxBase):
            raise TypeError(_APPROX_VS_APPROX_MSG)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        eq = self.__eq__(other)
        if eq is NotImplemented:
            return NotImplemented
        return not eq

    def __lt__(self, other: object) -> bool:
        raise TypeError(_ORDERING_MSG)

    def __le__(self, other: object) -> bool:
        raise TypeError(_ORDERING_MSG)

    def __gt__(self, other: object) -> bool:
        raise TypeError(_ORDERING_MSG)

    def __ge__(self, other: object) -> bool:
        raise TypeError(_ORDERING_MSG)

    def __repr__(self) -> str:
        raise NotImplementedError


class ApproxScalar(ApproxBase):
    """Approximate comparison for a single numeric value."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ApproxBase):
            raise TypeError(_APPROX_VS_APPROX_MSG)
        if not isinstance(other, numbers.Number):
            return NotImplemented
        actual = float(other)  # ty: ignore[invalid-argument-type]
        expected = float(self._expected)
        if self._nan_ok and math.isnan(expected) and math.isnan(actual):
            return True
        if math.isnan(expected) or math.isnan(actual):
            return False
        return math.isclose(actual, expected, rel_tol=self._rel, abs_tol=self._abs)

    def __repr__(self) -> str:
        expected = float(self._expected)
        if math.isnan(expected):
            return "nan"
        if math.isinf(expected):
            return repr(expected)
        tol = max(self._rel * _builtins_abs(expected), self._abs)
        return f"{self._expected} ± {tol:.1e}"


class ApproxSequence(ApproxBase):
    """Approximate comparison for sequences (list, tuple)."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ApproxBase):
            raise TypeError(_APPROX_VS_APPROX_MSG)
        if not isinstance(other, (list, tuple)):
            return NotImplemented
        if len(other) != len(self._expected):
            return False
        return all(
            approx(e, rel=self._rel, abs=self._abs, nan_ok=self._nan_ok) == a
            for e, a in zip(self._expected, other, strict=False)
        )

    def __repr__(self) -> str:
        items = [
            repr(approx(e, rel=self._rel, abs=self._abs, nan_ok=self._nan_ok))
            for e in self._expected
        ]
        return f"approx([{', '.join(items)}])"


class ApproxMapping(ApproxBase):
    """Approximate comparison for mappings (dict)."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ApproxBase):
            raise TypeError(_APPROX_VS_APPROX_MSG)
        if not isinstance(other, Mapping):
            return NotImplemented
        if set(other.keys()) != set(self._expected.keys()):
            return False
        return all(
            approx(
                self._expected[k],
                rel=self._rel,
                abs=self._abs,
                nan_ok=self._nan_ok,
            )
            == other[k]
            for k in self._expected
        )

    def __repr__(self) -> str:
        items = ", ".join(
            f"{k!r}: {approx(v, rel=self._rel, abs=self._abs, nan_ok=self._nan_ok)!r}"
            for k, v in self._expected.items()
        )
        return f"approx({{{items}}})"


def approx(
    expected: object,
    *,
    rel: float = 1e-6,
    abs: float = 1e-12,
    nan_ok: bool = False,
) -> ApproxBase:
    """Build an approximate-equality wrapper for use in ``assert`` statements.

    Supports scalars (``float``, ``int``, ``Decimal``), sequences (``list``,
    ``tuple``), mappings (``dict``), and arbitrarily nested combinations.

    Args:
        expected: The reference value (or container of values).
        rel: Relative tolerance (default ``1e-6``).
        abs: Absolute tolerance (default ``1e-12``).
        nan_ok: If ``True``, ``NaN == NaN`` compares as equal.

    Example::

        assert 0.1 + 0.2 == approx(0.3)
        assert [0.1, 0.2] == approx([0.1, 0.2], abs=1e-9)
    """
    if isinstance(expected, set):
        raise TypeError("approx() does not support sets")
    if isinstance(expected, Mapping):
        return ApproxMapping(expected, rel=rel, abs=abs, nan_ok=nan_ok)
    if isinstance(expected, (str, bytes)):
        raise TypeError("approx() expects a number, sequence, or mapping")
    if isinstance(expected, Iterable) and isinstance(expected, Sized):
        return ApproxSequence(expected, rel=rel, abs=abs, nan_ok=nan_ok)
    if isinstance(expected, numbers.Number):
        return ApproxScalar(expected, rel=rel, abs=abs, nan_ok=nan_ok)
    raise TypeError("approx() expects a number, sequence, or mapping")
