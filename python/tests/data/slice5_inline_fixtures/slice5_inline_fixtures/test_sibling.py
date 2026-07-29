"""A sibling file: sees the package-level fixture, not test_inline's fixtures.

Both halves matter. The first proves the module filter blocks inline fixtures
declared in another file; the second proves the filter is not a blanket block on
ModuleSource, which would satisfy the first assertion just as well.
"""

from __future__ import annotations

from oxitest import Fixtures, raises


def test_the_package_fixture_is_visible(fx: Fixtures) -> None:
    label = fx.slice5_inline_fixtures.shared_label
    assert label == "package-level", (
        f"package-level fixtures are visible to every file in the directory; a "
        f"module filter that blocked these would be over-broad; got {label!r}"
    )


def test_another_files_inline_fixture_is_not_visible(fx: Fixtures) -> None:
    # raises(Exception) is deliberately loose. The precise type is #1713's
    # business — it owns B1 enforcement and the two-catalogs BoundaryError
    # diagnostic. Narrowing it here would create work for that slice to undo.
    with raises(Exception):
        _ = fx.test_inline.per_module
