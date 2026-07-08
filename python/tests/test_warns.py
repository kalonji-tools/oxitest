"""Tests for the oxitest.warns() context manager."""

from __future__ import annotations

import warnings

from oxitest import raises, warns


def test_warns_catches_expected_warning() -> None:
    """warns() should succeed when the expected warning category is emitted."""
    with warns(UserWarning):
        warnings.warn("something", UserWarning, stacklevel=2)


def test_warns_no_warning_raises_assertion_error() -> None:
    """warns() should raise AssertionError when no warning is emitted at all."""
    with raises(AssertionError, match="No warning"), warns(UserWarning):
        pass


def test_warns_wrong_category_raises_assertion_error() -> None:
    """warns() raises AssertionError when a different warning category is emitted."""
    with raises(AssertionError, match="No warning"), warns(UserWarning):
        warnings.warn("something", DeprecationWarning, stacklevel=2)


def test_warns_match_passes_when_pattern_found() -> None:
    """warns(match=...) succeeds when the pattern appears in the warning message."""
    with warns(UserWarning, match="specific"):
        warnings.warn("specific message here", UserWarning, stacklevel=2)


def test_warns_match_fails_when_pattern_not_found() -> None:
    """warns(match=...) should raise AssertionError when the pattern is absent."""
    with (
        raises(AssertionError, match="not found"),
        warns(UserWarning, match="specific"),
    ):
        warnings.warn("totally different", UserWarning, stacklevel=2)


def test_warns_subclass_caught_by_parent_category() -> None:
    """Parent category in warns() catches subclass warnings."""
    # UserWarning is a subclass of Warning — parent category must catch it
    with warns(Warning):
        warnings.warn("user warn", UserWarning, stacklevel=2)


def test_warns_exported_from_oxitest() -> None:
    """The warns function should be publicly exported in oxitest.__all__."""
    import oxitest

    assert hasattr(oxitest, "warns"), (
        "'warns' should be exported from the oxitest module"
    )
    assert "warns" in oxitest.__all__, "'warns' should be listed in oxitest.__all__"
