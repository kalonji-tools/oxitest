"""A descendant reaching up one level, and resolving its own ``v1``."""

from __future__ import annotations

from oxitest import Fixtures


def test_reaches_one_level_up(fx: Fixtures) -> None:
    assert fx.api.api_conn == "api", (
        "the parent package's fixture must be visible from a subpackage — the "
        "chain has to walk every ancestor, not only the immediate rootdir"
    )


def test_own_v1_namespace_wins(fx: Fixtures) -> None:
    assert fx.v1.thing == "api-v1", (
        "two packages are named 'v1' in this project; a test must get the one "
        "in its own subtree, not whichever registered first"
    )
