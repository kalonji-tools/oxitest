"""Tests for the oxitest.raises() context manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never

import oxitest as oxi
from oxitest import raises


def test_raises_catches_expected_exception() -> Never:
    """Verifies that raises() passes when the expected exception type is raised."""
    with raises(ValueError):
        msg = "boom"
        raise ValueError(msg)


def test_raises_no_exception_raises_assertion_error() -> None:
    """Verifies that raises() itself raises AssertionError when no exception occurs."""
    with raises(AssertionError, match="Expected ValueError"), raises(ValueError):
        pass  # nothing raised


def test_raises_wrong_type_reraises() -> Never:
    """raises() re-raises exceptions that don't match the expected type."""
    with raises(TypeError), raises(ValueError):
        msg = "wrong type"
        raise TypeError(msg)


def test_raises_match_passes_when_pattern_found() -> Never:
    """match= succeeds when the pattern is found in the exception message."""
    with raises(ValueError, match="boom"):
        msg = "oh boom, something broke"
        raise ValueError(msg)


def test_raises_match_fails_when_pattern_not_found() -> Never:
    """match= raises AssertionError when the pattern is absent from the message."""
    with raises(AssertionError, match="not found"), raises(ValueError, match="boom"):
        msg = "nothing matches here"
        raise ValueError(msg)


def test_raises_exc_info_value_holds_exception() -> Never:
    """The as-binding's .value attribute holds the caught exception instance."""
    with raises(ValueError) as exc_info:
        msg = "stored"
        raise ValueError(msg)
    assert isinstance(exc_info.value, ValueError), (
        f"exc_info.value should be ValueError, got {type(exc_info.value).__name__}"
    )
    assert str(exc_info.value) == "stored", (
        f"exc_info.value message should be 'stored', got {str(exc_info.value)!r}"
    )


def test_raises_match_uses_regex_search_not_full_match() -> Never:
    """match= uses re.search; a substring pattern anywhere in the message suffices."""
    # "boom" must match anywhere in the string, not require a full match
    with raises(ValueError, match="boom"):
        msg = "oh boom!"
        raise ValueError(msg)


def test_raises_subclass_caught_by_parent_type() -> Never:
    """Specifying a parent exception type catches subclass exceptions via isinstance."""
    # ValueError is a subclass of Exception — parent type must catch it
    with raises(Exception):
        msg = "subclass"
        raise ValueError(msg)


def test_raises_exported_from_oxitest() -> None:
    """raises() is part of the public oxitest API and listed in __all__."""
    import oxitest

    assert hasattr(oxitest, "raises"), (
        "'raises' should be exported from the oxitest module"
    )
    assert "raises" in oxitest.__all__, "'raises' should be listed in oxitest.__all__"


@dataclass(frozen=True)
class TupleCatchCase:
    """Parameters for a single tuple-catch test case."""

    exc_class: type


@oxi.parametrize(
    first_type=TupleCatchCase(exc_class=ValueError),
    second_type=TupleCatchCase(exc_class=TypeError),
)
def test_raises_tuple_catches_matching_type(exc_class: type) -> Never:
    """raises() with a tuple of types catches exceptions whose type is in the tuple."""
    with raises((ValueError, TypeError)):
        msg = "msg"
        raise exc_class(msg)


def test_raises_tuple_wrong_type_reraises() -> Never:
    """raises() with a tuple re-raises exceptions absent from the tuple."""
    with raises(KeyError), raises((ValueError, TypeError)):
        msg = "neither"
        raise KeyError(msg)


def test_raises_tuple_no_exception_names_all_types() -> None:
    """AssertionError from tuple raises() includes all expected type names."""
    with (
        raises(AssertionError, match=r"\(ValueError \| TypeError\)"),
        raises((ValueError, TypeError)),
    ):
        pass
