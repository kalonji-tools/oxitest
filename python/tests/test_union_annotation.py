"""A PEP 604 union renders its own spelling instead of stopping the run.

``str | int`` is a ``types.UnionType``, and it carries no ``__name__``. Four
call sites read that attribute without a guard, and the first of them runs at
collection, so one union-annotated parameter stopped the whole run with a bare
``AttributeError`` (#2098).

The union and its ``typing`` spelling name the same type, so they must reach
the same outcome. ``Fixture[Union[str, int]]`` already passed, because
``Union`` does carry ``__name__``. Only the modern spelling crashed.

**CPython 3.14 removed the defect at the source.** It unified the two
spellings — ``types.UnionType is typing.Union`` — so ``(str | int).__name__``
exists there and returns ``'Union'``. The crash is reachable on 3.11 to 3.13
only, and the display name therefore differs by interpreter. Measured:
3.13.13 has no ``__name__``, 3.14.4 returns ``'Union'``. These tests assert
the display the running interpreter must produce, so they are regression
guards below 3.14 and agreement checks at and above it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Union

import oxitest as oxi
from oxitest._bridge._errors import AmbiguousFixtureError, FixtureNotFoundError
from oxitest._bridge._fixture_type import BindingType, type_display_name
from oxitest._bridge.importer import _get_fixture_deps
from tests import helpers

_DATA = Path(__file__).parent / "data" / "union_annotation"

_UNION: BindingType = str | int

# 3.14 folded types.UnionType into typing.Union, so the union carries the
# alias name from that release on. Below it there is no __name__ and the
# fallback renders the spelling the author wrote.
_UNION_DISPLAY = "Union" if sys.version_info >= (3, 14) else "str | int"


def test_a_union_and_its_typing_spelling_resolve_the_same_way() -> None:
    """Both spellings name one type, so one run must accept both."""
    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", "--serial", cwd=str(_DATA))

    # Assert
    assert rc == 0, (
        f"Fixture[str | int] and Fixture[Union[str, int]] name the same type, "
        f"so a run that accepts one must accept the other; got rc={rc}\n"
        f"{out}\n{err}"
    )


def test_a_union_annotated_parameter_is_collected() -> None:
    """Site 1: ``_get_fixture_deps`` runs at collection for every parameter."""

    # Arrange
    def takes_union(thing: oxi.Fixture[Union[str, int]]) -> None:  # noqa: UP007 — the typing spelling is the subject
        """A parameter whose annotation carries no ``__name__``."""

    def takes_pep604(thing: oxi.Fixture[str | int]) -> None:
        """The same type, in the spelling that used to crash."""

    # Act
    typing_deps = _get_fixture_deps(takes_union)
    pep604_deps = _get_fixture_deps(takes_pep604)

    # Assert
    assert pep604_deps == (("thing", _UNION_DISPLAY),), (
        "collection must name a union parameter by its own spelling; reading "
        "__name__ off a types.UnionType stopped the run before any test ran "
        "(#2098)"
    )
    assert typing_deps == (("thing", "Union"),), (
        "the typing spelling already worked and must keep its name, so this "
        "change is a guard and not a rename"
    )


def test_an_unknown_union_names_itself_in_the_not_found_error() -> None:
    """Site 2: no ``_by_type`` entry, so ``resolve`` raises and must render.

    The refusal must name the annotation the author wrote. Building it used to
    raise ``AttributeError`` before the refusal existed.
    """
    # Arrange
    registry = helpers.make_registry()

    # Act, Assert
    with oxi.raises(FixtureNotFoundError, match=re.escape(_UNION_DISPLAY)):
        registry.resolve(_UNION)


def test_two_union_typed_fixtures_name_the_union_in_the_ambiguity() -> None:
    """Site 3: two candidates and a qualifier that matches neither.

    The ambiguity must name the type both candidates provide, and must not
    raise ``AttributeError`` while the message is built.
    """
    # Arrange
    registry = helpers.make_registry(
        helpers.make_fixture_def("alpha", fixture_type=_UNION),
        helpers.make_fixture_def("beta", fixture_type=_UNION),
    )

    # Act, Assert
    with oxi.raises(AmbiguousFixtureError, match=re.escape(_UNION_DISPLAY)):
        registry.resolve(_UNION, "matches_neither")


def test_a_union_typed_fixture_does_not_break_arranged_type_lookup() -> None:
    """Site 4: the scan reads a name off *every* key in the type index.

    ``resolve_arranged_type`` walks the whole index to find an ``@injectable``
    class by name, so one union-typed fixture anywhere in the run made every
    ``@oxi.arrange`` type entry raise, whatever it was looking for. The
    refusal must name the entry that was asked for, not the union it walked
    past.
    """
    # Arrange
    registry = helpers.make_registry(
        helpers.make_fixture_def("alpha", fixture_type=_UNION),
    )

    # Act, Assert
    with oxi.raises(FixtureNotFoundError, match="SomeInjectable"):
        registry.resolve_arranged_type("SomeInjectable")


def test_the_display_name_accepts_any_annotation_form() -> None:
    """Every spelling a parameter may legally carry must render.

    ``str`` guards the fallback from swallowing the common case: rendering
    every type with ``str()`` would print ``<class 'str'>`` in every message.
    """
    # Arrange
    forms: tuple[tuple[Any, str], ...] = (
        (str, "str"),
        (_UNION, _UNION_DISPLAY),
        (Union[str, int], "Union"),  # noqa: UP007 — the typing spelling is the subject
        (list[str], "list"),
    )

    # Act, Assert
    for annotation, expected in forms:
        assert type_display_name(annotation) == expected, (
            f"{annotation!r} must render as {expected!r}; a form that raises "
            f"here stops collection for every test in the run, and one that "
            f"renders <class '...'> puts that in every fixture message (#2098)"
        )
