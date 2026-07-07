from __future__ import annotations

from dataclasses import dataclass
from typing import Never

import oxitest as oxi
from oxitest import raises


def test_raises_catches_expected_exception() -> Never:
    with raises(ValueError):
        msg = "boom"
        raise ValueError(msg)


def test_raises_no_exception_raises_assertion_error() -> None:
    with raises(AssertionError, match="Expected ValueError"), raises(ValueError):
        pass  # nothing raised


def test_raises_wrong_type_reraises() -> Never:
    with raises(TypeError), raises(ValueError):
        msg = "wrong type"
        raise TypeError(msg)


def test_raises_match_passes_when_pattern_found() -> Never:
    with raises(ValueError, match="boom"):
        msg = "oh boom, something broke"
        raise ValueError(msg)


def test_raises_match_fails_when_pattern_not_found() -> Never:
    with raises(AssertionError, match="not found"), raises(ValueError, match="boom"):
        msg = "nothing matches here"
        raise ValueError(msg)


def test_raises_exc_info_value_holds_exception() -> Never:
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
    # "boom" must match anywhere in the string, not require a full match
    with raises(ValueError, match="boom"):
        msg = "oh boom!"
        raise ValueError(msg)


def test_raises_subclass_caught_by_parent_type() -> Never:
    # ValueError is a subclass of Exception — parent type must catch it
    with raises(Exception):
        msg = "subclass"
        raise ValueError(msg)


def test_raises_exported_from_oxitest() -> None:
    import oxitest

    assert hasattr(oxitest, "raises"), (
        "'raises' should be exported from the oxitest module"
    )
    assert "raises" in oxitest.__all__, "'raises' should be listed in oxitest.__all__"


@dataclass(frozen=True)
class TupleCatchCase:
    exc_class: type


@oxi.parametrize(
    first_type=TupleCatchCase(exc_class=ValueError),
    second_type=TupleCatchCase(exc_class=TypeError),
)
def test_raises_tuple_catches_matching_type(exc_class: type) -> Never:
    with raises((ValueError, TypeError)):
        msg = "msg"
        raise exc_class(msg)


def test_raises_tuple_wrong_type_reraises() -> Never:
    with raises(KeyError), raises((ValueError, TypeError)):
        msg = "neither"
        raise KeyError(msg)


def test_raises_tuple_no_exception_names_all_types() -> None:
    with (
        raises(AssertionError, match=r"\(ValueError \| TypeError\)"),
        raises((ValueError, TypeError)),
    ):
        pass
