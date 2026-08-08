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
    """_unpack_sync on a generator returns HasTeardown carrying the generator."""

    def gen() -> Generator[str, None, None]:
        yield "setup_val"

    outcome = _unpack_sync(gen(), "my_fix")
    assert isinstance(outcome, HasTeardown), (
        f"generator should yield HasTeardown, got {type(outcome).__name__}"
    )
    registered: list[object] = []
    assert outcome.start(registered.append) == "setup_val", (
        "start() must run the body to its yield"
    )
    outcome.teardown()


def test_unpack_sync_does_not_advance_the_generator() -> None:
    """The body must not run until start() — registration happens in between.

    This is the whole point of the split (#1962): the caller registers the
    teardown against a generator that has not been started, then starts it. If
    _unpack_sync advanced, there would be no point at which a teardown could be
    registered before setup ran.
    """
    ran: list[str] = []

    def gen() -> Generator[str, None, None]:
        ran.append("body")
        yield "v"

    outcome = _unpack_sync(gen(), "my_fix")
    assert isinstance(outcome, HasTeardown), "generator should yield HasTeardown"
    assert ran == [], (
        f"_unpack_sync must not run the fixture body; it ran {ran}. Advancing "
        f"here reopens the window where an interrupt strands a set-up fixture "
        f"with no teardown registered"
    )
    outcome.start(lambda _td: None)
    assert ran == ["body"], f"start() must run the body, ran {ran}"


def test_teardown_of_an_unstarted_generator_is_a_no_op() -> None:
    """A registered-but-never-started fixture must not be resumed.

    ``next()`` on an unstarted generator *runs the setup*, so a teardown that
    did not check would execute the fixture body during teardown — strictly
    worse than the missed teardown it is meant to fix.
    """
    ran: list[str] = []

    def gen() -> Generator[str, None, None]:
        ran.append("setup")
        yield "v"
        ran.append("teardown")

    outcome = _unpack_sync(gen(), "never_started")
    assert isinstance(outcome, HasTeardown), "generator should yield HasTeardown"
    outcome.teardown()
    assert ran == [], (
        f"tearing down an unstarted fixture must run nothing, but ran {ran}"
    )


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
    assert outcome.start(lambda _td: None) == "val", (
        "start() must return the yielded value"
    )
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
    """HasTeardown carries generator+teardown; NoTeardown carries value only."""
    no_td = NoTeardown(value="x")
    assert no_td.value == "x", f"expected 'x', got {no_td.value!r}"

    def gen() -> Generator[str, None, None]:
        yield "y"

    def _noop() -> None:
        return None

    generator = gen()
    has_td = HasTeardown(generator=generator, teardown=_noop)
    assert has_td.generator is generator, (
        "HasTeardown must carry the generator itself — the value does not "
        "exist until start() is called"
    )
    assert has_td.teardown is _noop, "teardown should be the callable passed in"
    generator.close()


def test_start_registers_the_teardown_before_running_the_body() -> None:
    """Registration must precede the advance, structurally rather than by rule.

    This is the property the whole fix turns on, and it is invisible in normal
    operation: the two orders differ only when an interrupt lands between them.
    A previous version left the two statements adjacent at the call site, and a
    mutation that swapped them killed no test. `start()` taking the registration
    callback is what makes the wrong order unreachable (#1962).
    """
    order: list[str] = []

    def gen() -> Generator[str, None, None]:
        order.append("body")
        yield "v"

    outcome = _unpack_sync(gen(), "ordered")
    assert isinstance(outcome, HasTeardown), "generator should yield HasTeardown"
    outcome.start(lambda _td: order.append("registered"))

    assert order == ["registered", "body"], (
        f"the teardown must be registered before the fixture body runs, got "
        f"{order}. Advancing first reopens the window in which an interrupt "
        f"leaves a set-up fixture with nothing to dispose it"
    )
