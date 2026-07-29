"""Async tests awaiting async fixtures through the ``fx.`` proxy.

The log helper is duplicated here rather than imported from ``__fixtures__``:
oxitest is invoked with this project as a positional path, so the package is
not importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    """Log the test name only, never a fixture value.

    Module-lifetime values arrive ``FrozenProxy``-wrapped, and ``FrozenProxy``
    does not forward ``__format__`` (kalonji-tools/oxitest#1735), so
    interpolating one would record the proxy repr instead of the value. The
    fixtures log their own ids; the tests only need to say they ran.
    """
    with Path(os.environ["ASYNC_LIFETIMES_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


async def test_alpha_one(fx: Fixtures) -> None:
    per_test = await fx.async_lifetimes.per_test
    per_module = await fx.async_lifetimes.per_module
    assert per_test.startswith("per_test-"), (
        f"function-lifetime async fixture must arrive awaited, got {per_test!r} "
        "— a raw coroutine here means the await hook never ran"
    )
    assert per_module.startswith("per_module-"), (
        f"module-lifetime async fixture must arrive awaited, got {per_module!r}"
    )
    _record("USE alpha_one")


async def test_alpha_two(fx: Fixtures) -> None:
    """Second test in this module — its per_module instance must be shared."""
    per_test = await fx.async_lifetimes.per_test
    per_module = await fx.async_lifetimes.per_module
    assert per_test.startswith("per_test-"), (
        f"function-lifetime async fixture must arrive awaited, got {per_test!r}"
    )
    assert per_module.startswith("per_module-"), (
        f"module-lifetime async fixture must arrive awaited, got {per_module!r}"
    )
    _record("USE alpha_two")


async def test_alpha_double_await(fx: Fixtures) -> None:
    """Awaiting the same fixture twice must return the memoised value.

    A bare coroutine cached by ``_CachingProxy._get_cached`` would raise
    ``RuntimeError: cannot reuse already awaited coroutine`` on the second
    await, which is why the proxy has to memoise its own awaited result.
    """
    first = await fx.async_lifetimes.per_test
    second = await fx.async_lifetimes.per_test
    assert first == second, (
        f"double await must yield the memoised value, got {first!r} then "
        f"{second!r} — the fixture was rebuilt instead of cached per test"
    )
    _record("USE alpha_double")


async def test_alpha_generators(fx: Fixtures) -> None:
    """Async-generator fixtures at both tiers, awaited through the proxy."""
    gen_each = await fx.async_lifetimes.per_test_gen
    gen_module = await fx.async_lifetimes.per_module_gen
    assert gen_each.startswith("per_test_gen-"), (
        f"async-generator fixture must yield its value, got {gen_each!r}"
    )
    assert gen_module.startswith("per_module_gen-"), (
        f"module-lifetime async generator must yield its value, got {gen_module!r}"
    )
    _record("USE alpha_generators")
