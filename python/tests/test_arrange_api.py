"""Decoration-time behavior of @oxi.arrange."""

from __future__ import annotations

import oxitest
from oxitest._bridge._arrange_api import arrange
from oxitest._bridge._builtins import TempDir
from oxitest._bridge._fn_metadata import get_or_create


def test_arrange_stores_single_type() -> None:
    """Decorating with a single type must land in FunctionMetadata.arranged."""

    @arrange(TempDir)
    def target() -> None: ...

    meta = get_or_create(target)
    assert meta.arranged == (TempDir,), (
        "single-type arrange must store the class in metadata for the collector"
    )


def test_arrange_stores_mixed_types_and_names_in_order() -> None:
    """Types and strings mix freely; declaration order is preserved."""

    @arrange(TempDir, "clean_sys_modules")
    def target() -> None: ...

    meta = get_or_create(target)
    assert meta.arranged == (TempDir, "clean_sys_modules"), (
        "arrange must preserve declaration order — collector runs entries in order"
    )


def test_arrange_rejects_non_injectable_class() -> None:
    """A regular class (not @injectable) must be rejected at decoration time.

    @oxi.arrange resolves types via get_fixture_by_type at runtime, which
    would eventually raise FixtureTypeNotFoundError — but catching the mistake
    at decoration gives immediate feedback to the test author.
    """

    class NotInjectable:
        pass

    with oxitest.raises(TypeError, match=r"is not @injectable"):
        arrange(NotInjectable)
