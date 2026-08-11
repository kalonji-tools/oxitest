"""Site 1: a B1 boundary violation.

The access is **deliberately dynamic**, for the same reason as the sibling
project ``usage_error_mixed``: this project exercises the run-level
usage-error vote (#1761), and site 2 is an async lifetime mismatch that only
surfaces while a test runs. A literal ``fx.api.api_conn`` is refused at
collection by the static B1 gate (#1758), which would abort before site 2 was
ever reached — the wrapper asserting ``"lifetime mismatch"`` would then fail,
and the wrapper asserting two sites would be measuring one.

``getattr`` is the case the static gate cannot see by construction. The
violation and its ``BoundaryError`` are unchanged; only the gate that catches
it is.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_crosses_the_boundary(fx: Fixtures) -> None:
    namespace = "api"
    assert getattr(fx, namespace).api_conn, (
        "reaching a sibling package's fixture must be refused"
    )
