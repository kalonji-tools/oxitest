from __future__ import annotations

import warnings

from oxitest._bridge._raises import raises
from oxitest._bridge._warns import warns


def test_warns_catches_expected_warning():
    with warns(UserWarning):
        warnings.warn("something", UserWarning)


def test_warns_no_warning_raises_assertion_error():
    with raises(AssertionError, match="No warning"):
        with warns(UserWarning):
            pass


def test_warns_wrong_category_raises_assertion_error():
    with raises(AssertionError, match="No warning"):
        with warns(UserWarning):
            warnings.warn("something", DeprecationWarning)


def test_warns_match_passes_when_pattern_found():
    with warns(UserWarning, match="specific"):
        warnings.warn("specific message here", UserWarning)


def test_warns_match_fails_when_pattern_not_found():
    with raises(AssertionError, match="not found"):
        with warns(UserWarning, match="specific"):
            warnings.warn("totally different", UserWarning)


def test_warns_subclass_caught_by_parent_category():
    # UserWarning is a subclass of Warning — parent category must catch it
    with warns(Warning):
        warnings.warn("user warn", UserWarning)


def test_warns_exported_from_oxitest():
    import oxitest

    assert hasattr(oxitest, "warns"), (
        "'warns' should be exported from the oxitest module"
    )
    assert "warns" in oxitest.__all__, "'warns' should be listed in oxitest.__all__"
