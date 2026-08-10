"""Site 2: a fixture dependency whose lifetime cannot hold."""

from __future__ import annotations

from oxitest import Fixture


async def test_the_lifetime_cap_is_refused(long_lived: Fixture[str]) -> None:
    assert long_lived, (
        "a module-lifetime fixture must not capture a function-lifetime async "
        "value, or later tests receive a value bound to a dead event loop"
    )
