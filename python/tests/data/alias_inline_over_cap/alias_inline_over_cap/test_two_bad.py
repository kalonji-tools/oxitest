"""Two inline declarations above the module cap, both aliased.

An inline fixture is anchored to its own module, so a lifetime wider than the
module would outlive the only scope that can see it (ADR-0009 Rule 2's home-kind
cap). Before #1859 the check read the prescan AST, which cannot see `ox`, so
both of these were silently accepted.

Two offenders rather than one: the cap accumulates per module, and a fail-fast
implementation would name only the first.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="package")
def first_bad() -> str:
    return "a"


@ox.fixture(lifetime="process")
def second_bad() -> str:
    return "b"


def test_never_runs() -> None:
    assert True, "collection must fail before this test executes"
