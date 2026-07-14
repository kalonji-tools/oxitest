"""Decoration-time behavior of @oxi.arrange."""

from __future__ import annotations

from oxitest._bridge._fn_metadata import get_or_create


def test_function_metadata_defaults_arranged_to_empty() -> None:
    """FunctionMetadata.arranged must default to () so undecorated tests need no init.

    Ensures tests without @oxi.arrange don't require explicit initialization.
    """

    def target() -> None: ...

    meta = get_or_create(target)
    assert meta.arranged == (), (
        "arranged must default to () so undecorated tests don't need explicit init"
    )
