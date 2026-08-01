"""Tests for debug protocol conformance."""

from oxitest import DebuggerBackend
from oxitest._bridge._debugger import _PdbBackend
from tests import helpers


def test_pdb_backend_satisfies_protocol() -> None:
    """_PdbBackend must be a valid DebuggerBackend."""
    assert isinstance(_PdbBackend(), DebuggerBackend), (
        "_PdbBackend should satisfy DebuggerBackend protocol"
    )


def test_recording_debugger_satisfies_protocol() -> None:
    """RecordingDebugger test double must be a valid DebuggerBackend."""
    assert isinstance(helpers.RecordingDebugger(), DebuggerBackend), (
        "RecordingDebugger should satisfy DebuggerBackend protocol"
    )


def test_recording_debugger_records_trace() -> None:
    """RecordingDebugger should count trace() calls."""
    rec = helpers.RecordingDebugger()
    rec.trace()
    rec.trace()
    assert rec.trace_count == 2, f"expected 2 trace calls, got {rec.trace_count}"


def test_recording_debugger_records_post_mortem() -> None:
    """RecordingDebugger should record traceback objects."""
    rec = helpers.RecordingDebugger()
    exc = helpers.make_exc(ValueError, "boom")
    tb = exc.__traceback__
    assert tb is not None, "make_exc must produce a traceback"
    rec.post_mortem(tb)
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 traceback, got {len(rec.post_mortem_tracebacks)}"
    )
    assert rec.post_mortem_tracebacks[0] is tb, (
        "recorded traceback should be the same object"
    )
