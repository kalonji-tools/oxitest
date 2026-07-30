"""Positive control: the anchor package's own `Fixture[T]` injection works."""

from __future__ import annotations

from oxitest import Fixture

from slice6_injection_boundary._kinds import ApiConnection


def test_the_anchor_package_injects_its_own_fixture(
    api_conn: Fixture[ApiConnection],
) -> None:
    assert api_conn.label == "api", (
        "without this passing, a project where api_conn never registered would "
        "produce the same not-found error from admin/ and the boundary "
        "assertion would hold for entirely the wrong reason"
    )
