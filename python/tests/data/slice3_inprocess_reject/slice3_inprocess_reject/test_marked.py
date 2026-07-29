"""One inprocess-marked test inside the declaring package."""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.mark.inprocess
def test_marked(fx: Fixtures) -> None:
    assert fx.slice3_inprocess_reject.engine is not None, (
        "unreachable — collection rejects this"
    )


def test_unmarked(fx: Fixtures) -> None:
    assert fx.slice3_inprocess_reject.engine is not None, (
        "unreachable — collection rejects this"
    )
