"""Decision 11: a fixture's dependencies are judged by *its* anchor, not the test's.

``leaky`` is anchored at ``api/`` and asks for ``thing``, which is anchored one
level below at ``api/v1/``. No declaration in this file could legally see that,
so the dependency must be refused however the calling test is placed.

``sane`` is the positive control. Declaration homes are registered per directory
that holds a test file, so without a fixture that resolves cleanly from
``api/test_api.py`` this whole namespace could silently fail to register and the
acceptance test would see a failure that is about absence, not about the anchor.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture


@oxi.fixture(lifetime="function")
def sane() -> str:
    return "sane"


@oxi.fixture(lifetime="function")
def leaky(thing: Fixture[str]) -> str:
    return f"leaky-{thing}"
