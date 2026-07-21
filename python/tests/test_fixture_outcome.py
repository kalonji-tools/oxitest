"""Tests for _unpack_sync and FixtureOutcome sum type (HasTeardown | NoTeardown)."""

from __future__ import annotations

from collections.abc import Generator

from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._fixture_instantiator import (
    HasTeardown,
    NoTeardown,
    _unpack_sync,
)
from oxitest._bridge.result import Diagnostic


def test_unpack_sync_plain_value() -> None:
    """_unpack_sync on a plain value returns NoTeardown."""
    outcome = _unpack_sync(42, "my_fix")
    assert isinstance(outcome, NoTeardown), (
        f"plain value should yield NoTeardown, got {type(outcome).__name__}"
    )
    assert outcome.value == 42, f"expected 42, got {outcome.value!r}"


def test_unpack_sync_generator() -> None:
    """_unpack_sync on a generator returns HasTeardown with the first value."""

    def gen() -> Generator[str, None, None]:
        yield "setup_val"

    outcome = _unpack_sync(gen(), "my_fix")
    assert isinstance(outcome, HasTeardown), (
        f"generator should yield HasTeardown, got {type(outcome).__name__}"
    )
    assert outcome.value == "setup_val", f"expected 'setup_val', got {outcome.value!r}"
    outcome.teardown()


def test_unpack_sync_generator_teardown_captures_exception() -> None:
    """Generator teardown exception should be captured as a diagnostic."""

    def gen() -> Generator[str, None, None]:
        yield "val"
        msg = "teardown boom"
        raise RuntimeError(msg)

    outcome = _unpack_sync(gen(), "exploding")
    assert isinstance(outcome, HasTeardown), (
        f"generator should yield HasTeardown, got {type(outcome).__name__}"
    )
    assert outcome.value == "val", f"expected 'val', got {outcome.value!r}"
    diags: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(diags)
    try:
        outcome.teardown()
    finally:
        _diagnostic_collector_var.reset(token)
    assert len(diags) == 1, f"expected 1 teardown diagnostic, got {len(diags)}"
    assert "exploding" in diags[0].message, (
        f"diagnostic should contain fixture name 'exploding', got {diags[0].message!r}"
    )


def test_fixture_outcome_variants() -> None:
    """HasTeardown carries value+teardown; NoTeardown carries value only."""
    no_td = NoTeardown(value="x")
    assert no_td.value == "x", f"expected 'x', got {no_td.value!r}"

    def _noop() -> None:
        return None

    has_td = HasTeardown(value="y", teardown=_noop)
    assert has_td.value == "y", f"expected 'y', got {has_td.value!r}"
    assert has_td.teardown is _noop, "teardown should be the callable passed in"
