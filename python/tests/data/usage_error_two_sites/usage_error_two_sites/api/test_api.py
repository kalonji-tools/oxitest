"""The control: a legal access that must keep passing."""

from __future__ import annotations

from oxitest import Fixture


def test_the_legal_access_still_passes(api_conn: Fixture[str]) -> None:
    assert api_conn == "anchored to api/", (
        "without a passing control, two failing sites could equally mean the "
        "whole project failed to collect"
    )
