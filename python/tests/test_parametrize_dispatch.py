"""Unit tests for the ``_dispatch_compact_or_expand`` helper.

The helper is the shared compact-vs-expanded dispatch used by both
``DataclassCases.resolve()`` and ``_resolve_composed()``. These tests exercise
the helper directly to lock in its three observable behaviors: compact
success, compact-plus-fixref failure, and expanded-mode field spread.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import Fixture
from oxitest._bridge.parametrize import (
    ParametrizeError,
    _dispatch_compact_or_expand,
)


@dataclass(frozen=True)
class _Case:
    """Simple dataclass used to drive the dispatch helper in these tests."""

    x: int
    y: int


def test_compact_mode_no_fixrefs_returns_single_kwarg() -> None:
    """Compact mode wraps the instance under the case-typed parameter name.

    A function whose signature has one non-Fixture parameter typed as the
    case dataclass should trigger compact mode. The helper must return the
    instance under that exact parameter name so downstream injection binds
    the whole case as a single argument.
    """

    def target(case: _Case) -> None:  # noqa: ARG001 - signature drives detection
        return None

    instance = _Case(x=1, y=2)

    kwargs, fixref_names = _dispatch_compact_or_expand(target, instance, frozenset())

    assert kwargs == {"case": instance}, (
        "compact mode must wrap the instance under the exact parameter name"
        " matching the case type, otherwise fixture injection cannot bind it"
    )
    assert fixref_names == frozenset(), (
        "no fixref fields were supplied, so the returned set must stay empty"
        " to signal the runtime has no FixtureRef work to do"
    )


def test_compact_mode_with_fixrefs_raises_parametrize_error() -> None:
    """Compact mode is incompatible with FixtureRef fields.

    FixtureRef fields require field-by-field injection, which only expanded
    mode supports. Detecting this conflict at resolve time (rather than
    silently ignoring the fixref) prevents tests from running with unresolved
    fixture references — which would surface as confusing AttributeErrors.
    """

    def target(case: _Case) -> None:  # noqa: ARG001 - signature drives detection
        return None

    instance = _Case(x=1, y=2)

    with oxi.raises(
        ParametrizeError,
        match="compact mode is incompatible with FixtureRef fields",
    ):
        _dispatch_compact_or_expand(target, instance, frozenset({"db"}))


def test_expanded_mode_spreads_fields_as_kwargs() -> None:
    """Expanded mode spreads each dataclass field into its own kwarg.

    When the function's parameters name the fields directly (rather than the
    case type), the helper must break the instance apart so each field can be
    bound by name. This is the mode that supports FixtureRef fields, so the
    returned fixref_names set flows through unchanged for the runtime to act
    on.
    """

    def target(x: int, y: int) -> None:  # noqa: ARG001 - signature drives detection
        return None

    instance = _Case(x=1, y=2)
    fixrefs = frozenset({"db"})

    kwargs, fixref_names = _dispatch_compact_or_expand(target, instance, fixrefs)

    assert kwargs == {"x": 1, "y": 2}, (
        "expanded mode must produce one kwarg per dataclass field so that"
        " each field can be injected independently by name"
    )
    assert fixref_names is fixrefs, (
        "expanded mode must pass fixref_names through unchanged — the caller"
        " uses this set to drive per-field FixtureRef resolution downstream"
    )


def test_expanded_mode_when_case_type_only_annotates_fixture_param() -> None:
    """A Fixture[T]-annotated parameter of the case type does NOT trigger compact.

    Compact detection filters out Fixture-annotated parameters. This test
    guards that filter: if someone types a fixture as ``Fixture[_Case]``,
    the helper must still fall through to expanded mode instead of trying to
    bind the whole case there.
    """

    def target(case: Fixture[_Case], x: int, y: int) -> None:  # noqa: ARG001
        return None

    instance = _Case(x=3, y=4)

    kwargs, fixref_names = _dispatch_compact_or_expand(target, instance, frozenset())

    assert kwargs == {"x": 3, "y": 4}, (
        "Fixture[T] parameters must not count as compact-mode targets;"
        " otherwise fixture params would shadow expanded-mode field injection"
    )
    assert fixref_names == frozenset(), (
        "no fixrefs were supplied, so the empty set must round-trip unchanged"
    )
