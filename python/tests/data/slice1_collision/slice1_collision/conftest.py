from __future__ import annotations

import oxitest

fixtures = oxitest.Fixtures(name="slice1_collision")


@fixtures.fixture
def conn() -> object:
    return object()
