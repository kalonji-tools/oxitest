"""Tries to reach test_owner.py's inline fixture by Fixture[T] injection.

This must fail the run. Parameter injection resolves by **bare name** and never
sees a namespace, so it is a second resolution route: a module filter applied
only to the `fx.<ns>.<name>` proxy leaves this one open. It is also the route a
user reaches for first, which is why it gets its own project rather than an
assertion inside a passing file — an unresolvable parameter fails the test, so
the run has to be the thing under test.
"""

from __future__ import annotations

from oxitest import Fixture


def test_stealing_another_files_inline_fixture(owned: Fixture[int]) -> None:
    assert owned is None, (
        "unreachable — resolving `owned` here must fail, because it is declared "
        "inline in test_owner.py and inline fixtures are visible only to their "
        "own module"
    )
