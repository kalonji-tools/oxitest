"""Unit tests for the WarnCapture builtin fixture."""

from __future__ import annotations

import warnings

import oxitest
from oxitest import WarnCapture


def test_warncapture_captures_warnings_emitted_during_test(warn: WarnCapture) -> None:
    warnings.warn("hello", UserWarning)
    assert len(warn.list) == 1, f"expected 1 warning, got {warn.list!r}"
    assert issubclass(warn.list[0].category, UserWarning), (
        f"expected UserWarning category, got {warn.list[0].category!r}"
    )
    assert "hello" in str(warn.list[0].message), (
        f"expected 'hello' in warning message, got {warn.list[0].message!r}"
    )


def test_warncapture_captures_multiple_warning_categories(warn: WarnCapture) -> None:
    warnings.warn("first", UserWarning)
    warnings.warn("second", DeprecationWarning)
    assert len(warn.list) == 2, f"expected 2 warnings, got {warn.list!r}"
    categories = [w.category for w in warn.list]
    assert UserWarning in categories, (
        f"expected UserWarning in captured categories, got {categories!r}"
    )
    assert DeprecationWarning in categories, (
        f"expected DeprecationWarning in captured categories, got {categories!r}"
    )


def test_warncapture_clear_resets_list(warn: WarnCapture) -> None:
    warnings.warn("before clear", UserWarning)
    assert len(warn.list) == 1, f"expected 1 warning before clear, got {warn.list!r}"
    warn.clear()
    assert warn.list == [], f"expected empty list after clear, got {warn.list!r}"
    warnings.warn("after clear", UserWarning)
    assert len(warn.list) == 1, f"expected 1 warning after clear, got {warn.list!r}"


def test_warncapture_and_oxitest_warns_can_coexist(warn: WarnCapture) -> None:
    """WarnCapture stays active while oxitest.warns() is used in the same test."""
    warnings.warn("before", UserWarning)
    with oxitest.warns(UserWarning, match="asserted"):
        warnings.warn("asserted", UserWarning)
    assert any("before" in str(w.message) for w in warn.list), (
        f"WarnCapture must capture warnings outside oxitest.warns() context, "
        f"got {warn.list!r}"
    )
    assert any("asserted" in str(w.message) for w in warn.list), (
        f"WarnCapture must also capture warnings inside oxitest.warns() context "
        f"(survives nested catch_warnings), got {warn.list!r}"
    )
