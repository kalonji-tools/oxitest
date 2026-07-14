"""Tests for _unpack_sync and _FixtureOutcome — sync fixture unpacking helpers."""

from __future__ import annotations

from collections.abc import Generator

from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._fixture_instantiator import _FixtureOutcome, _unpack_sync
from oxitest._bridge.result import Diagnostic


def test_unpack_sync_plain_value() -> None:
    """_unpack_sync on a plain value should set value and leave teardown as None."""
    outcome = _unpack_sync(42, "my_fix")
    assert outcome.value == 42, f"expected 42, got {outcome.value!r}"
    assert outcome.teardown is None, "plain value should have no teardown"


def test_unpack_sync_generator() -> None:
    """_unpack_sync on a generator yields the first value and registers teardown."""

    def gen() -> Generator[str, None, None]:
        yield "setup_val"

    outcome = _unpack_sync(gen(), "my_fix")
    assert outcome.value == "setup_val", f"expected 'setup_val', got {outcome.value!r}"
    assert outcome.teardown is not None, "generator should have teardown"
    outcome.teardown()


def test_unpack_sync_generator_teardown_captures_exception() -> None:
    """Generator teardown exception should be captured as a diagnostic."""

    def gen() -> Generator[str, None, None]:
        yield "val"
        msg = "teardown boom"
        raise RuntimeError(msg)

    outcome = _unpack_sync(gen(), "exploding")
    assert outcome.value == "val", f"expected 'val', got {outcome.value!r}"
    assert outcome.teardown is not None, "generator should have teardown"
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


def test_fixture_outcome_dataclass() -> None:
    """_FixtureOutcome should default teardown to None when only value is provided."""
    o = _FixtureOutcome(value="x")
    assert o.value == "x", f"expected 'x', got {o.value!r}"
    assert o.teardown is None, "default teardown should be None"
