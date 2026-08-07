from __future__ import annotations

from oxitest import Fixtures


def test_one(fx: Fixtures) -> None:
    assert fx.trw.wide == "wide-value", (
        "the process-lifetime fixture must be injected, so that its teardown "
        "runs at the process boundary — the position under test"
    )
