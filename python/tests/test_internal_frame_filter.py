"""Traceback frames from oxitest's own machinery are recognised on any platform.

``INTERNAL_PREFIXES`` is spelled with forward slashes, but ``co_filename``
carries the platform separator. Matching the raw string meant nothing was ever
internal on Windows, so oxitest's own frames appeared in every traceback
regardless of ``--show-internals`` (#1989).

The integration test that covers this asserted the *absence* of
``"oxitest/_bridge"``, a string Windows output cannot contain, so it passed
there while asserting nothing. These cases pin the predicate itself, in both
spellings, so neither platform can regress silently.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._diagnostics import _is_internal_frame


@dataclass(frozen=True)
class FrameCase:
    """One traceback filename and whether it belongs to oxitest itself."""

    filename: str
    internal: bool


@oxi.parametrize(
    windows_bridge=FrameCase(
        r"C:\Users\dev\.venv\Lib\site-packages\oxitest\_bridge\executor.py",
        internal=True,
    ),
    posix_bridge=FrameCase(
        "/home/dev/.venv/lib/python3.12/site-packages/oxitest/_bridge/executor.py",
        internal=True,
    ),
    windows_builtins=FrameCase(
        r"C:\Users\dev\.venv\Lib\site-packages\oxitest\_builtins\_tempdir.py",
        internal=True,
    ),
    windows_plugin=FrameCase(
        r"C:\Users\dev\.venv\Lib\site-packages\oxitest\plugin_loader.py", internal=True
    ),
    windows_user_test=FrameCase(r"C:\proj\tests\test_thing.py", internal=False),
    posix_user_test=FrameCase("/proj/tests/test_thing.py", internal=False),
)
def test_internal_frame_detection(*, filename: str, internal: bool) -> None:
    """The predicate must not depend on which separator the platform uses."""
    # Act
    verdict = _is_internal_frame(filename)

    # Assert
    assert verdict is internal, (
        "a frame's provenance decides whether the user sees oxitest's own stack "
        "in their failure output; getting it wrong on one platform means every "
        f"traceback there is noise, and {filename!r} was judged internal={verdict}"
    )
