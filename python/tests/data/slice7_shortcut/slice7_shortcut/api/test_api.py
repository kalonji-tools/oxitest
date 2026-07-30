"""Shortcut access at the declaring package, plus the naming-clash rule."""

from __future__ import annotations

from oxitest import Fixtures

from slice7_shortcut._kinds import Shadowed, Tx


def test_shortcut_resolves_without_a_package_prefix(fx: Fixtures) -> None:
    # Act
    value = fx.tx

    # Assert
    assert value.label == "api", (
        "fx.tx must resolve the fixture anchored at this test's own package; "
        "if it resolved anything else the shortcut is reading a catalog that "
        "is not B1-filtered"
    )


def test_qualified_access_still_works_alongside_the_shortcut(fx: Fixtures) -> None:
    # Act
    value = fx.api.tx

    # Assert
    assert value.label == "api", (
        "slice 1's qualified route must be untouched by the new top-level "
        "fixture branch — the branch sits below the namespace branch and must "
        "not intercept a segment lookup"
    )


def test_a_package_segment_shadows_a_same_named_fixture(fx: Fixtures) -> None:
    """ADR-0009 Rule 5's naming-clash rule, live for the first time."""
    # Act
    segment = fx.api

    # Assert
    assert not isinstance(segment, Shadowed), (
        "fx.api must return the sub-proxy for package 'api', not the fixture "
        "named 'api' — the segment branch has to be checked before the fixture "
        "branch or the package becomes unreachable by its own name"
    )
    assert segment.tx.label == "api", (
        "the object fx.api returns must behave as a namespace proxy; asserting "
        "only 'not a Shadowed' would also pass if it returned None"
    )


def test_the_shadowed_fixture_is_still_reachable_when_qualified(fx: Fixtures) -> None:
    """Shadowing hides the shortcut spelling, not the fixture."""
    # Act
    value = fx.api.api

    # Assert
    assert isinstance(value, Shadowed), (
        "a fixture whose name collides with its package segment must stay "
        "reachable via the qualified path; if it did not, the naming clash "
        "would silently delete a fixture rather than rename it"
    )
    assert value.label == "shadowed-by-the-segment", (
        "the qualified path must reach the shadowed fixture itself, not some "
        "other fixture that happens to be visible under the same name"
    )


async def test_shortcut_awaits_an_async_fixture(fx: Fixtures) -> None:
    # Act
    value: Tx = await fx.async_tx

    # Assert
    assert value.label == "async-api", (
        "the shortcut route must return the same memoising awaitable the "
        "qualified route does; without it an async fixture reached by shortcut "
        "hands the test an un-awaited coroutine"
    )
