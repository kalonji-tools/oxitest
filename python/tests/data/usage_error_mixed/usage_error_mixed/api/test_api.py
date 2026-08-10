"""Two ordinary outcomes: one pass, one assertion failure."""

from __future__ import annotations

from oxitest import Fixture


def test_passes(api_conn: Fixture[str]) -> None:
    assert api_conn == "anchored to api/", (
        "the legal access must work, or the run proves only that the fixture "
        "never registered"
    )


def test_fails_an_assertion() -> None:
    assert 2 + 2 == 5, (
        "a deliberate assertion failure — it must not change the run's exit "
        "code away from 4, because a usage error outranks it"
    )
