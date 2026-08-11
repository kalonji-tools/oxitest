"""Three cross-boundary accesses the access-time gate can never see.

Each is the *same* violation — `fx.api.api_conn` from a sibling package — and
differs only in how the test is reached. Before #1758 all three exited 0: the
skip and the dead branch reported nothing, and the `xfail` absorbed the
`BoundaryError` and reported `1 passed - 1 xfailed`.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.mark.skip(reason="the violation must be caught without running the body")
def test_inside_a_skipped_test(fx: Fixtures) -> None:
    _ = fx.api.api_conn


@oxi.mark.xfail(reason="the violation must not be absorbed as the expected failure")
def test_inside_an_xfail(fx: Fixtures) -> None:
    _ = fx.api.api_conn


def test_inside_a_branch_never_taken(fx: Fixtures) -> None:
    if False:
        _ = fx.api.api_conn
