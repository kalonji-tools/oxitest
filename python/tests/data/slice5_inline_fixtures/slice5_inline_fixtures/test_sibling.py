"""A sibling file: sees the package-level fixture, not test_inline's fixtures.

The negative half — reaching for another file's inline fixture — moved to the
``slice5_inline_cross`` project when the collection-time B1 gate shipped
(#1758). It used to sit here and assert its own refusal with ``raises`` inside
the test, which a collection-time gate makes unrunnable: the run is refused
before any body executes, so the whole project would fail to collect and this
file's *positive* assertion would stop proving anything.

That positive assertion is why the split was necessary rather than tidy. It
proves the module filter is not a blanket block on ``ModuleSource``, which
would satisfy the negative case exactly as well as a correct filter does.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_the_package_fixture_is_visible(fx: Fixtures) -> None:
    label = fx.slice5_inline_fixtures.shared_label
    assert label == "package-level", (
        f"package-level fixtures are visible to every file in the directory; a "
        f"module filter that blocked these would be over-broad; got {label!r}"
    )
