"""Unit tests for the std-stream codec declaration (#2004).

The helper runs at both entry points before anything reads or writes, so these
tests drive it against substitute streams rather than the real ones — a test
that reconfigured the actual process streams would leak that change into every
test that ran after it in the same worker.
"""

from __future__ import annotations

import io
import sys

from oxitest import Patcher
from oxitest._bridge._streams import force_utf8_streams


def _cp1252_stream(errors: str) -> io.TextIOWrapper:
    """A stand-in for a Windows std stream: locale codec, given error handler."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors)


def _patch_all_streams(
    patch: Patcher, **overrides: io.TextIOBase
) -> dict[str, io.TextIOBase]:
    """Substitute all three std streams, overriding the named ones.

    All three, always — never a subset. ``force_utf8_streams`` touches every
    stream, and under a parallel worker the real ``sys.stdin`` has already been
    read from by the time any test runs, which ``reconfigure`` refuses with
    ``UnsupportedOperation``. A test that patched only the stream it cared
    about would fail on the two it did not.
    """
    streams: dict[str, io.TextIOBase] = {
        "stdin": _cp1252_stream("strict"),
        "stdout": _cp1252_stream("strict"),
        "stderr": _cp1252_stream("backslashreplace"),
    }
    streams.update(overrides)
    for name, stream in streams.items():
        patch.setattr(sys, name, stream)
    return streams


def test_force_utf8_streams_switches_all_three_streams(patch: Patcher) -> None:
    """All three streams move off the locale codec, not just stdout."""
    # Arrange
    streams = _patch_all_streams(patch)

    # Act
    force_utf8_streams()

    # Assert
    for name, stream in streams.items():
        assert stream.encoding == "utf-8", (
            f"sys.{name} left on the locale codec is what makes a non-ASCII "
            "path unreadable to a worker — both ends of the task wire are UTF-8"
        )


def test_force_utf8_streams_preserves_each_error_handler(patch: Patcher) -> None:
    """Only the codec changes — the stream's error handler survives."""
    # Arrange — Windows stderr defaults to backslashreplace, not strict.
    streams = _patch_all_streams(patch)

    # Act
    force_utf8_streams()

    # Assert
    assert streams["stderr"].errors == "backslashreplace", (
        "a bare reconfigure(encoding=...) resets errors to strict, which would "
        "turn a mangled-but-printed traceback into a raise inside the error path"
    )


def test_force_utf8_streams_skips_a_stream_that_is_not_a_text_wrapper(
    patch: Patcher,
) -> None:
    """A stream without reconfigure() is skipped, not crashed on."""
    # Arrange — StdCapture installs a StringIO, which has no reconfigure().
    buffer = io.StringIO()
    _patch_all_streams(patch, stdout=buffer)

    # Act
    force_utf8_streams()

    # Assert
    assert sys.stdout is buffer, (
        "a replaced stream must be left alone; crashing here would break every "
        "test running under capture, which is all of them"
    )
