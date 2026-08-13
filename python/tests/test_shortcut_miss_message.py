"""The tail of the shortcut-miss message, per segment shape (#1759).

``shortcut_miss_message`` states the inline rule and then advises a remedy.
It used to advise the qualified form for every input, including a test module
stem — which is the form the user already wrote when the access was
``fx.<stem>.<name>``. The head of the message states the true rule one sentence
earlier, so the two contradicted each other.

Both tails are pinned here because no test read the tail at all before this
file: the only assertion against this message checked the ``cannot resolve
fixture`` prefix (``test_b1_collection_gate.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._fixture_session import shortcut_miss_message


@dataclass(frozen=True)
class Segment:
    """One segment spelling, and why the default ``python_files`` reaches it."""

    name: str
    why: str


#: The two shapes the *default* ``python_files`` matches
#: (``src/config/mod.rs``: ``vec!["test_*.py", "*_test.py"]``).
_PREFIXED = Segment(name="test_decl", why="the default python_files matches test_*.py")
_SUFFIXED = Segment(name="decl_test", why="the default python_files matches *_test.py")


@oxi.parametrize(prefixed=_PREFIXED, suffixed=_SUFFIXED)
def test_a_test_module_stem_is_not_advised_the_qualified_form(
    case: Segment,
) -> None:
    """The defect this issue is named after.

    ``fx.test_decl.per_module`` *is* the qualified form. Advising it sends the
    user back to the thing they wrote, and contradicts the sentence before it.
    """
    # Act
    message = shortcut_miss_message(case.name)

    # Assert
    assert "qualified form" not in message, (
        f"{case.name} is a test module stem ({case.why}), so the user reaching "
        f"fx.{case.name}.<fixture> already wrote the qualified form; advising "
        f"it again contradicts the inline rule stated one sentence earlier; "
        f"got:\n{message}"
    )
    assert "__fixtures__.py" in message, (
        f"the message must name the remedy that actually works for an inline "
        f"declaration — raising it to a declaration home — rather than only "
        f"refusing the access; got:\n{message}"
    )


def test_a_segment_that_is_not_a_test_module_keeps_the_spelling_advice() -> None:
    """The other half, unchanged.

    A segment that names no test module is a probable typo, and the qualified
    form is the right suggestion for it. This is what stops the fix for the
    case above from degrading the common case.
    """
    # Act
    message = shortcut_miss_message("totally_not_a_module")

    # Assert
    assert "use the qualified form fx.<package>.totally_not_a_module." in message, (
        f"a segment matching no test-module shape is a probable typo, and the "
        f"qualified form is the correct remedy for it; narrowing the fix to "
        f"test module stems is what keeps this input's advice intact; "
        f"got:\n{message}"
    )


def test_the_remedy_holds_for_a_bare_shortcut_to_a_declaration_home() -> None:
    """The second route into this message, which no measurement varied.

    ``get_fixture_shortcut`` reports a bare ``fx.<name>`` miss, where the name
    is a fixture name and not a segment. ``_module_source_registrar`` refuses a
    ``test_``-named fixture only when it is inline, so a ``__fixtures__.py``
    may legally declare ``test_helper`` — and reaching it from outside its
    anchor arrives here with ``name="test_helper"``.

    The predicate cannot separate the two routes, because both arrive as one
    ``str``. So the remedy is worded to be true on both: raising the
    declaration to a common ancestor fixes an inline declaration and an
    out-of-reach declaration home alike.
    """
    # Act
    message = shortcut_miss_message("test_helper")

    # Assert
    assert "ancestor of both modules" in message, (
        "a bare fx.test_helper miss reaches the same branch as a segment, so "
        "the remedy must name the fix that works for a declaration home too; "
        "wording it as 'move it out of the test module' would be false for "
        f"this route; got:\n{message}"
    )
