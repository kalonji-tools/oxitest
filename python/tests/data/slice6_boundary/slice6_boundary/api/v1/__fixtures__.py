"""Namespace ``v1`` — one of two packages in this project with that basename.

Its twin lives at ``admin/v1``. Neither subtree contains the other, so both
declarations are legal and each must resolve only within its own subtree.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def thing() -> str:
    return "api-v1"
