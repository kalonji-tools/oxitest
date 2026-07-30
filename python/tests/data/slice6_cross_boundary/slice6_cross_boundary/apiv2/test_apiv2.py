"""The prefix trap: ``apiv2`` starts with ``api`` but is a sibling directory."""

from __future__ import annotations

from oxitest import Fixtures


def test_prefix_sibling_is_not_a_descendant(fx: Fixtures) -> None:
    """Expected to ERROR — a startswith-based predicate would let this through."""
    _ = fx.api.api_conn
