"""One B1 violation, in a sibling package that cannot see api/.

The access is **deliberately dynamic**. This project exists to exercise the
run-level usage-error vote (#1761), which only fires for a wiring error found
*while a test runs*. A literal ``fx.api.api_conn`` is refused at collection by
the static B1 gate (#1758), which would abort the run before any vote could be
cast and leave the passing test and the failing assertion in this project
unreachable — so every assertion in the wrapper would be measuring the static
gate instead of the vote it was written for.

``getattr`` is the case the static gate cannot see by construction, and the
access-time gate exists for exactly it. The violation and its ``BoundaryError``
are unchanged; only the gate that catches it is.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_crosses_the_boundary(fx: Fixtures) -> None:
    namespace = "api"
    assert getattr(fx, namespace).api_conn, (
        "reaching a sibling package's fixture must be refused"
    )
