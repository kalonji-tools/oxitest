"""An unused declaration inline in a test module, the third declaration home."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def never_used_inline() -> float:
    return 1.0


def test_touches_no_fixture() -> None:
    assert 1 + 1 == 2, (
        "the run must reach a collected test, because the unused-fixture check "
        "runs over the items collection produced"
    )
