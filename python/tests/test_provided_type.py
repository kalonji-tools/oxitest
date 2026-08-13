"""A yield fixture resolves by the type it provides, not the type it wraps.

The binding type stays the raw annotation, so ``_by_type`` gains no member.
Unwrapping happens in ``FixtureDef.provides``, which only the qualifier
comparison reads. See ADR-0002's amendment for why the index is left alone.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator, Iterator
from pathlib import Path
from typing import Any

from oxitest import Yields
from oxitest._bridge._errors import FixtureTypeMismatchError
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureScope,
    FrameworkSource,
)
from oxitest._bridge._module_source_registrar import _infer_return_type
from tests import helpers

_DATA = Path(__file__).parent / "data" / "provided_type"
_MISMATCH = Path(__file__).parent / "data" / "provided_type_mismatch"


def _defn(fixture_type: Any, *, is_generator: bool) -> FixtureDef[Any]:
    """Build a FixtureDef the way the module registrar builds one."""
    return FixtureDef(
        name="target",
        fixture_type=fixture_type,
        scope=FixtureScope.EACH,
        source=FrameworkSource(func=lambda: None, origin="<test>"),
        is_generator=is_generator,
    )


def test_the_binding_type_of_a_yield_fixture_is_not_unwrapped() -> None:
    """The index key must stay the raw annotation (ADR-0002 amendment)."""

    # Arrange
    def yields_str() -> Yields[str]:
        yield "x"

    # Act
    binding = _infer_return_type(yields_str)

    # Assert
    assert binding == Generator[str, None, None], (
        "unwrapping the binding type enlarges _by_type, which makes a yield "
        "fixture collide with a plugin fixture of the same type and breaks "
        "test_plugin_fixture_survives_the_first_test (#2094)"
    )


def test_a_generator_fixture_provides_its_yield_type() -> None:
    """The qualifier must compare against the yielded type."""
    # Arrange
    defn = _defn(Yields[str], is_generator=True)

    # Act
    provided = defn.provides

    # Assert
    assert provided is str, (
        "a yield fixture provides the type it yields; comparing the generator "
        "alias instead rejects the fixture the parameter name selects (#2094)"
    )


def test_every_yielding_spelling_provides_its_element_type() -> None:
    """Every legal yield spelling unwraps, not only Generator."""
    # Arrange
    spellings = [
        Generator[str, None, None],
        AsyncGenerator[str, None],
        Iterator[str],
    ]

    # Act
    provided = [_defn(s, is_generator=True).provides for s in spellings]

    # Assert
    assert provided == [str, str, str], (
        "each of these spellings is a legal yield fixture, so each must provide "
        "its element type; matching only Generator misses Iterator (#2094)"
    )


def test_a_fixture_that_does_not_yield_provides_its_binding_type() -> None:
    """A plain function returning an iterator must not be unwrapped."""
    # Arrange
    defn = _defn(Iterator[str], is_generator=False)

    # Act
    provided = defn.provides

    # Assert
    assert provided == Iterator[str], (
        "this function provides an iterator, it does not yield one; unwrapping "
        "it would hand consumers a str where an Iterator[str] was declared"
    )


def test_two_competitors_do_not_hide_the_named_yield_fixture() -> None:
    """The name wins even when the type index holds two other candidates."""
    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", "--serial", cwd=str(_DATA))

    # Assert
    assert rc == 0, (
        f"two same-typed competitors must not stop a fixture the parameter "
        f"names; got rc={rc}\n{out}\n{err}"
    )


def test_a_named_fixture_with_the_wrong_type_is_named_in_the_error() -> None:
    """The diagnosis must name the disagreement, not list strangers."""
    # Act
    out, err, rc = helpers.run_oxitest(
        None, "--warnings", "--serial", cwd=str(_MISMATCH)
    )

    # Assert
    combined = out + err
    assert rc != 0, (
        f"a fixture that provides str must not be injected into a parameter "
        f"annotated Fixture[int]; got rc={rc}\n{combined}"
    )
    assert "target" in combined and "str" in combined and "int" in combined, (
        "the error must name the fixture, what it provides, and what the "
        "parameter asked for; listing unrelated competitors is the defect "
        "this issue reports (#2094)"
    )


def test_the_mismatch_error_names_all_three_parts() -> None:
    """The message is the user-facing contract this issue exists to fix."""
    # Arrange
    error = FixtureTypeMismatchError("target", "str", "int")

    # Act
    message = str(error)

    # Assert
    assert "'target'" in message, (
        "the author cannot act on a message that does not name the fixture; "
        "listing unrelated candidates is the defect this issue reports (#2094)"
    )
    assert "'str'" in message and "Fixture[int]" in message, (
        "the message must state both sides of the disagreement — what the "
        "fixture provides and what the parameter asked for"
    )
    assert error.param_name == "target", "callers read param_name off the error"
    assert error.provided == "str", "callers read provided off the error"
    assert error.annotated == "int", "callers read annotated off the error"
