"""Acceptance: cached fixture values render their own value, not the wrapper.

Runs the ``proxy_str`` data-project as a subprocess. The project's assertions
are the acceptance criteria — they fail if ``FrozenProxy`` stops forwarding
string conversion — so this driver only has to prove the project ran and
passed.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import helpers

_PROJECT = Path(__file__).parent / "data" / "proxy_str"

#: Tests declared in the data-project's single module.
_EXPECTED_TESTS = 3


def test_cached_fixture_values_render_their_value() -> None:
    """Every string conversion in the data-project renders the wrapped value."""
    stdout, stderr, rc = helpers.common.run_oxitest(_PROJECT, "--serial")

    assert rc == 0, (
        "a non-zero exit means a fixture value rendered as FrozenProxy(...) "
        f"instead of its own value\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_EXPECTED_TESTS} passed" in stdout, (
        f"expected {_EXPECTED_TESTS} tests to run — a lower count means the "
        f"project did not execute as written and rc == 0 proves nothing"
        f"\nstdout:\n{stdout}"
    )
