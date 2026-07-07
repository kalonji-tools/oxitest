from __future__ import annotations

from collections.abc import Generator

from oxitest import WarnCapture
from oxitest._bridge._fixture_instantiator import _FixtureOutcome, _unpack_sync


def test_unpack_sync_plain_value() -> None:
    outcome = _unpack_sync(42, "my_fix")
    assert outcome.value == 42, f"expected 42, got {outcome.value!r}"
    assert outcome.teardown is None, "plain value should have no teardown"


def test_unpack_sync_generator() -> None:
    def gen() -> Generator[str, None, None]:
        yield "setup_val"

    outcome = _unpack_sync(gen(), "my_fix")
    assert outcome.value == "setup_val", f"expected 'setup_val', got {outcome.value!r}"
    assert outcome.teardown is not None, "generator should have teardown"
    outcome.teardown()


def test_unpack_sync_generator_teardown_captures_exception(warn: WarnCapture) -> None:
    def gen() -> Generator[str, None, None]:
        yield "val"
        msg = "teardown boom"
        raise RuntimeError(msg)

    outcome = _unpack_sync(gen(), "exploding")
    assert outcome.value == "val", f"expected 'val', got {outcome.value!r}"
    assert outcome.teardown is not None, "generator should have teardown"
    outcome.teardown()
    assert len(warn.list) == 1, f"expected 1 teardown warning, got {len(warn.list)}"
    assert "exploding" in str(warn.list[0].message), (
        f"warning should contain fixture name 'exploding', got "
        f"{str(warn.list[0].message)!r}"
    )


def test_fixture_outcome_dataclass() -> None:
    o = _FixtureOutcome(value="x")
    assert o.value == "x", f"expected 'x', got {o.value!r}"
    assert o.teardown is None, "default teardown should be None"
