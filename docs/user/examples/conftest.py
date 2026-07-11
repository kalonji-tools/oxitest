"""Shared fixtures and stubs for doc example tests.

Provides common fixtures referenced across multiple doc pages.
Populated as migration batches add pages that need external symbols.
"""

from __future__ import annotations

# --8<-- [start:quickstart-setup]
from oxitest import Fixtures

fx = Fixtures()


@fx.fixture
def greeting() -> str:
    return "hello"


# --8<-- [end:quickstart-setup]
