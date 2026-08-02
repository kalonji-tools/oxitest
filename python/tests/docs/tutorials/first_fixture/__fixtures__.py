"""Fixture declarations for the getting-started tutorial and the front page.

Anchored at ``first_fixture/``: a ``__fixtures__.py`` is scoped to its own
directory and everything below it (ADR-0009 Rule 3), so it gets a directory of
its own rather than sitting beside the other tutorial examples, which never
asked to see these declarations.
"""

from __future__ import annotations

# fmt: off
# --8<-- [start:declare-fixture]
import oxitest as oxi


@oxi.fixture(lifetime="function")
def sample_numbers() -> list[int]:
    return [2, 3, 5]
# --8<-- [end:declare-fixture]
# fmt: on
