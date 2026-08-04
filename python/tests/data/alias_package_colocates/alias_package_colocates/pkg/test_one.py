"""Module one of two — two groups are what make the collapse observable."""

from __future__ import annotations

from oxitest import Fixtures


def test_uses_engine_one(fx: Fixtures) -> None:
    value = fx.pkg.engine
    assert value == "engine", (
        f"the aliased package declaration must resolve and co-locate this "
        f"subtree; got {value!r}"
    )
