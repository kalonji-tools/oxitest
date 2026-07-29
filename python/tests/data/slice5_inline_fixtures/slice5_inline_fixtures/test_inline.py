"""Inline @oxi.fixture declarations — 3 tests, 2 fixtures (ADR-0009 slice 5).

The counters are the point. A module-lifetime fixture that is silently rebuilt
per test still injects a perfectly good value, so any assertion that only
checked the value would pass while the tier was wrong.
"""

from __future__ import annotations

import itertools

import oxitest as oxi
from oxitest import Fixtures

_PER_TEST = itertools.count(1)
_PER_MODULE = itertools.count(1)


@oxi.fixture(lifetime="function")
def per_test() -> int:
    return next(_PER_TEST)


@oxi.fixture(lifetime="module")
def per_module() -> int:
    return next(_PER_MODULE)


def test_one(fx: Fixtures) -> None:
    per_test = fx.test_inline.per_test
    per_module = fx.test_inline.per_module
    assert per_test == 1, (
        f"a function-lifetime inline fixture is built fresh per test, so the "
        f"first test must see 1; got {per_test}"
    )
    assert per_module == 1, (
        f"the module-lifetime fixture is built once for this file; got {per_module}"
    )


def test_two(fx: Fixtures) -> None:
    per_test = fx.test_inline.per_test
    per_module = fx.test_inline.per_module
    assert per_test == 2, (
        f"the function-lifetime fixture must be rebuilt for the second test; got "
        f"{per_test} — a cached value here means the tier is wrong"
    )
    assert per_module == 1, (
        f"the module-lifetime fixture must NOT be rebuilt; got {per_module}"
    )


def test_three_sees_the_package_fixture_too(fx: Fixtures) -> None:
    label = fx.slice5_inline_fixtures.shared_label
    assert label == "package-level", (
        f"a package-level fixture stays visible from a file that also declares "
        f"inline fixtures — the module filter must narrow inline visibility "
        f"only; got {label!r}"
    )
