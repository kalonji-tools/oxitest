"""The namespace predicate: can `fx.<namespace>.<name>` be written at all (#1782).

The predicate answers exactly one question, and these tests are built from the
measurement that established it: parse `fx.<name>.x` and check the result is a
nested `ast.Attribute`. Anything that parses that way is reachable and must be
accepted, however unwise the name looks.

That is why builtins and soft keywords appear in the accepted table. The
previous validator refused them and accepted `integration-tests`, so it was
wrong in both directions at once.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._namespace_validation import namespace_defect

# Reachable: `fx.<name>.x` parses as attribute access.
REACHABLE = ["api", "db", "unit", "int", "list", "print", "match", "case", "type", "_"]

# Unreachable, and the reason the predicate must give.
NOT_IDENTIFIERS = ["integration-tests", "2fast", "", "with space", "dotted.name"]
KEYWORDS = ["class", "def", "for", "return", "None"]


@dataclass(frozen=True)
class Case:
    """One namespace under test.

    A frozen dataclass rather than a dict because `strict = "abort"` refuses
    dict-parametrize.
    """

    name: str


def _cases(names: list[str]) -> dict[str, Case]:
    """One parametrize case per name, keyed by a spelling Python can take.

    A case name is a keyword argument, so it must be an identifier — and half
    the inputs here are deliberately not identifiers, which is the whole point
    of the table. The key is therefore a sanitised label and the case carries
    the real string.
    """
    cases: dict[str, Case] = {}
    for name in names:
        label = "".join(char if char.isalnum() else "_" for char in name)
        cases[f"case_{label or 'empty'}"] = Case(name=name)
    return cases


@oxi.parametrize(**_cases(REACHABLE))
def test_reachable_namespaces_have_no_defect(name: str) -> None:
    """A name that can be written as `fx.<name>` must be accepted."""
    # Act
    defect = namespace_defect(name)

    # Assert
    assert defect is None, (
        f"'{name}' parses as attribute access in fx.{name}.x, so refusing it "
        f"would make a working namespace unusable — which is the half of the "
        f"old validator that rejected int, list, match and type"
    )


@oxi.parametrize(**_cases(NOT_IDENTIFIERS))
def test_non_identifiers_are_rejected_as_such(name: str) -> None:
    """The reason must distinguish a non-identifier from a keyword."""
    # Act
    defect = namespace_defect(name)

    # Assert
    assert defect == "not a Python identifier", (
        f"'{name}' cannot be written as fx.{name}.x, and the reason must say "
        f"so — the caller builds its remedy from this string, and 'rename to a "
        f"valid Python identifier' is only correct for this arm"
    )


@oxi.parametrize(**_cases(KEYWORDS))
def test_keywords_are_rejected_as_such(name: str) -> None:
    """A keyword fails at parse time, which is a different failure entirely."""
    # Act
    defect = namespace_defect(name)

    # Assert
    assert defect == "a Python keyword", (
        f"fx.{name}.x is a SyntaxError, so the test module never collects — a "
        f"distinct failure from a non-identifier, which parses into something "
        f"else entirely"
    )


@oxi.parametrize(**_cases([*REACHABLE, *NOT_IDENTIFIERS, *KEYWORDS]))
def test_the_predicate_agrees_with_the_parser(name: str) -> None:
    """The predicate is a claim about Python's grammar — hold it to the grammar.

    Without this, the two tables above are only as good as the judgement that
    built them, and a future edit could drift the predicate away from what the
    parser actually accepts with every hand-written case still green.
    """
    # Arrange — what the grammar says about fx.<name>.
    #
    # The test is that `fx.<name>` is ONE attribute access whose attribute is
    # exactly *name*, not merely that the source parses. `fx.dotted.name`
    # parses cleanly and is still unreachable: it reads as two segments, so it
    # looks up a namespace called `dotted` and never one called `dotted.name`.
    # That is the case a plugin declared as `plugins = ["mypkg.plugin"]` hits.
    try:
        parsed = ast.parse(f"fx.{name}", mode="eval").body
        parses_as_attribute = (
            isinstance(parsed, ast.Attribute)
            and parsed.attr == name
            and isinstance(parsed.value, ast.Name)
        )
    except SyntaxError:
        parses_as_attribute = False

    # Act
    defect = namespace_defect(name)

    # Assert
    assert (defect is None) == parses_as_attribute, (
        f"the predicate and the Python parser disagree about '{name}': "
        f"predicate says {'reachable' if defect is None else defect}, parser "
        f"says {'reachable' if parses_as_attribute else 'unreachable'}. The "
        f"predicate exists to mirror the grammar, so any disagreement is a bug "
        f"in the predicate"
    )
