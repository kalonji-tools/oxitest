"""The shortcut must not smuggle a fixture across a B1 boundary.

This test is *expected to error*. The runner-level assertion lives in
``python/tests/test_fixtures_redesign_slice7.py``; what matters here is that
the access is attempted at all.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_a_sibling_package_cannot_shortcut_into_api(fx: Fixtures) -> None:
    # Act — expected to raise FixtureNotFoundError, not to return
    value = fx.api_only

    # Assert — unreachable; if it ever runs, B1 has been bypassed
    assert value is None, (
        "reaching this line means the shortcut resolved a fixture anchored in "
        "a package this test cannot see — the shortcut is meant to save "
        "keystrokes, never scope"
    )
