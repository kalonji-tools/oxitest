"""A raising ``ctx.addfinalizer`` callback, beside a raising yield teardown.

Both arms live in one project deliberately. The yield arm is the negative
control for double-wrapping: the outer test asserts that one failure produces
one diagnostic, in the fixture wording, and no callback-worded twin.
"""

from __future__ import annotations

from oxitest import Fixture, TestContext


def _boom() -> None:
    msg = "ADDFINALIZER blew up"
    raise RuntimeError(msg)


def test_addfinalizer_raises(ctx: TestContext) -> None:
    ctx.addfinalizer(_boom)
    assert True, "the test itself passes; the finalizer is what raises"


def test_yield_teardown_raises(loud: Fixture[str]) -> None:
    assert loud == "v", "the fixture must resolve, or its teardown never runs"
