"""Body, plain-helper and function-teardown positions under wide-async promotion."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture, TestContext
from testcontext_current import _util


def _record(event: str) -> None:
    """Append one event line. Sync, because a bare open in an async body lints."""
    with Path(os.environ["TCC_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


async def test_alpha(aengine: Fixture[str]) -> None:
    """Read identity from the body, from a plain helper, and register a finalizer."""
    # Arrange / Act
    _record(f"BODY {TestContext.current().name}")
    _record(f"HELPER {_util.whoami().rsplit('::', 1)[-1]}")

    def _in_teardown() -> None:
        # Bare acquisition. current() must return here rather than raise — the
        # teardown loop swallows exceptions — and must NOT warn: reading
        # identity during teardown is legitimate and loses nothing. This
        # position is the pin for that (#1952).
        TestContext.current()
        _record("TEARDOWN reached")

    def _registers_ambiently() -> None:
        # Registration from inside a teardown, ambient route. The callback is
        # accepted and never run, so it must warn.
        oxi.current_test().on_teardown(lambda: _record("NEVER RUNS ambient"))
        _record("AMBIENT REGISTRAR reached")

    def _registers_via_captured() -> None:
        # Same, through a context captured before teardown began. This route
        # never passes through current(), so a guard living there cannot see
        # it — which is the case #1952 was filed for.
        captured.on_teardown(lambda: _record("NEVER RUNS captured"))
        _record("CAPTURED REGISTRAR reached")

    captured = TestContext.current()
    captured.on_teardown(_in_teardown)
    captured.on_teardown(_registers_ambiently)
    captured.on_teardown(_registers_via_captured)

    # Registered through the module-level alias instead. current_test() builds
    # a fresh TestContext per call, so this only runs if the alias hands back
    # the run's shared teardown list rather than a private one.
    oxi.current_test().on_teardown(lambda: _record("ALIAS TEARDOWN reached"))

    # Assert
    assert aengine, "the wide async fixture must be injected"
