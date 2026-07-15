"""Decoration-time behavior of @oxi.arrange."""

from __future__ import annotations

from typing import Any, cast

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


def test_arrange_bare_raises_typeerror() -> None:
    """@oxi.arrange (no parens) — decorator receives function as arg; must error.

    Same failure signal as @oxi.arrange() empty: both mean 'no fixtures were given'.
    Aligned wording so users get one clear message about what went wrong.
    """

    def target() -> None: ...

    with oxitest.raises(
        TypeError, match=r"@oxi\.arrange requires at least one fixture"
    ):
        arrange(cast("Any", target))


def test_arrange_empty_raises_typeerror() -> None:
    """@oxi.arrange() with no args — same error as bare form.

    A call with zero fixtures is always a mistake: the decorator has no effect
    and the intent is unclear. Raising at decoration time (import time) gives
    faster feedback than waiting for collection.
    """
    with oxitest.raises(
        TypeError, match=r"@oxi\.arrange requires at least one fixture"
    ):
        arrange()


def test_arrange_duplicate_within_one_call_raises() -> None:
    """@oxi.arrange(TempDir, TempDir) — unambiguous mistake, error at decoration.

    Allowing duplicates would silently run the same fixture setup twice, which
    is never intentional. Catching this at decoration time prevents hard-to-debug
    double-execution at test runtime.
    """
    with oxitest.raises(
        TypeError, match=r"duplicate fixture in @oxi\.arrange: TempDir"
    ):
        arrange(TempDir, TempDir)


def test_arrange_rejects_non_type_non_str_arg() -> None:
    """Anything other than an @injectable class or string is a type error.

    Passing a raw value like 42 cannot be resolved to a fixture — the error
    at decoration time is clearer than a cryptic failure during collection or
    fixture injection.
    """
    with oxitest.raises(
        TypeError,
        match=r"@oxi\.arrange: expected an @injectable class or a fixture name",
    ):
        arrange(cast("Any", 42))
