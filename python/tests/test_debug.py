"""Tests for --debug post-mortem debugging support."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


from helpers import RecordingDebugger
from oxitest._bridge._builtins._capture import _StdCapture
from oxitest._bridge._debugger import DebuggerBackend, _PdbBackend
from oxitest._bridge._middleware import _is_debuggable
from oxitest._bridge.executor import (
    _debug_post_mortem,
    _print_debug_banner,
    _print_trace_banner,
    _run_base,
    _suspend_capture,
    _trace_before_test,
)
from oxitest._bridge.result import StatusKind


def test_is_debuggable_assertion_error():
    """AssertionError should trigger the debugger."""
    assert _is_debuggable(AssertionError("fail")), "AssertionError should be debuggable"


def test_is_debuggable_runtime_error():
    """RuntimeError should trigger the debugger."""
    assert _is_debuggable(RuntimeError("boom")), "RuntimeError should be debuggable"


def test_is_debuggable_value_error():
    """ValueError should trigger the debugger."""
    assert _is_debuggable(ValueError("bad")), "ValueError should be debuggable"


def test_is_debuggable_skipped_false():
    """Skipped exceptions (by name) should not trigger pdb."""

    class Skipped(Exception):
        pass

    assert not _is_debuggable(Skipped("reason")), "Skipped should not be debuggable"


def test_is_debuggable_skip_test_false():
    """SkipTest exceptions (by name) should not trigger pdb."""

    class SkipTest(Exception):
        pass

    assert not _is_debuggable(SkipTest("reason")), "SkipTest should not be debuggable"


def test_is_debuggable_keyboard_interrupt_false():
    """KeyboardInterrupt should not trigger the debugger."""
    result = _is_debuggable(KeyboardInterrupt())
    assert not result, "KeyboardInterrupt should not be debuggable"


def test_is_debuggable_system_exit_false():
    """SystemExit should not trigger the debugger."""
    assert not _is_debuggable(SystemExit(1)), "SystemExit should not be debuggable"


def test_suspend_capture_restores_std_capture():
    """_suspend_capture should call _restore on StdCapture instances."""
    cap = _StdCapture()
    old_stdout = cap._old_stdout
    _suspend_capture({"cap": cap, "x": 42})
    assert sys.stdout is old_stdout, "stdout should be restored after _suspend_capture"


def test_suspend_capture_ignores_non_capture_kwargs():
    """_suspend_capture should not fail on kwargs without capture objects."""
    _suspend_capture({"x": 42, "name": "test"})


def test_print_debug_banner_contains_node_id():
    """Banner should include node ID, exception type/message, and help text."""
    exc = AssertionError("expected 3, got 5")
    buf = io.StringIO()
    _print_debug_banner("tests/test_math.py::test_add", exc, file=buf)
    output = buf.getvalue()
    assert "tests/test_math.py::test_add" in output, f"missing node_id: {output!r}"
    assert "AssertionError" in output, f"missing exc type: {output!r}"
    assert "expected 3, got 5" in output, f"missing exc message: {output!r}"
    assert "'h' for help" in output, f"missing help hint: {output!r}"


def test_print_trace_banner_contains_node_id():
    """Trace banner should include node ID and stepping message."""
    buf = io.StringIO()
    _print_trace_banner("tests/test_math.py::test_add", file=buf)
    output = buf.getvalue()
    assert "TRACE" in output, f"missing TRACE keyword: {output!r}"
    assert "tests/test_math.py::test_add" in output, f"missing node_id: {output!r}"
    assert "'c' to run" in output, f"missing help hint: {output!r}"


def test_pdb_backend_satisfies_protocol():
    """_PdbBackend must be a valid DebuggerBackend."""
    assert isinstance(_PdbBackend(), DebuggerBackend), (
        "_PdbBackend should satisfy DebuggerBackend protocol"
    )


def test_recording_debugger_satisfies_protocol():
    """RecordingDebugger test double must be a valid DebuggerBackend."""
    assert isinstance(RecordingDebugger(), DebuggerBackend), (
        "RecordingDebugger should satisfy DebuggerBackend protocol"
    )


def test_recording_debugger_records_trace():
    """RecordingDebugger should count trace() calls."""
    rec = RecordingDebugger()
    rec.trace()
    rec.trace()
    assert rec.trace_count == 2, f"expected 2 trace calls, got {rec.trace_count}"


def test_recording_debugger_records_post_mortem():
    """RecordingDebugger should record traceback objects."""
    rec = RecordingDebugger()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        tb = sys.exc_info()[2]
        assert tb is not None, "traceback must exist inside except block"
        rec.post_mortem(tb)
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 traceback, got {len(rec.post_mortem_tracebacks)}"
    )
    assert rec.post_mortem_tracebacks[0] is tb, (
        "recorded traceback should be the same object"
    )


def test_run_base_always_mode_passing_calls_trace_only():
    """always mode + passing test: trace called, post_mortem not called."""
    rec = RecordingDebugger()
    result = _run_base(
        lambda: None,
        {},
        [],
        debug_mode="always",
        node_id="t.py::test_ok",
        backend=rec,
    )
    assert result.status == StatusKind.PASSED, f"expected passed, got {result.status}"
    assert rec.trace_count == 1, f"expected 1 trace call, got {rec.trace_count}"
    assert len(rec.post_mortem_tracebacks) == 0, (
        f"expected 0 post_mortem calls, got {len(rec.post_mortem_tracebacks)}"
    )


def test_run_base_always_mode_failing_calls_both():
    """always mode + failure: trace before, post_mortem after."""
    rec = RecordingDebugger()

    def failing():
        raise AssertionError("boom")

    result = _run_base(
        failing,
        {},
        [],
        debug_mode="always",
        node_id="t.py::test_fail",
        backend=rec,
    )
    assert result.status == StatusKind.FAILED, f"expected failed, got {result.status}"
    assert rec.trace_count == 1, f"expected 1 trace call, got {rec.trace_count}"
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 post_mortem call, got {len(rec.post_mortem_tracebacks)}"
    )


def test_run_base_post_mortem_mode_failing_calls_post_mortem_only():
    """post-mortem mode + failure: only post_mortem called, no trace."""
    rec = RecordingDebugger()

    def failing():
        raise AssertionError("crash")

    result = _run_base(
        failing,
        {},
        [],
        debug_mode="post-mortem",
        node_id="t.py::test_crash",
        backend=rec,
    )
    assert result.status == StatusKind.FAILED, f"expected failed, got {result.status}"
    assert rec.trace_count == 0, f"expected 0 trace calls, got {rec.trace_count}"
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 post_mortem call, got {len(rec.post_mortem_tracebacks)}"
    )


def test_run_base_post_mortem_mode_passing_calls_neither():
    """post-mortem mode + passing test: neither called."""
    rec = RecordingDebugger()
    result = _run_base(
        lambda: None,
        {},
        [],
        debug_mode="post-mortem",
        node_id="t.py::test_ok",
        backend=rec,
    )
    assert result.status == StatusKind.PASSED, f"expected passed, got {result.status}"
    assert rec.trace_count == 0, f"expected 0 trace calls, got {rec.trace_count}"
    assert len(rec.post_mortem_tracebacks) == 0, (
        f"expected 0 post_mortem calls, got {len(rec.post_mortem_tracebacks)}"
    )


def test_run_base_no_debug_mode_calls_neither():
    """No debug mode: neither trace nor post_mortem called."""
    result = _run_base(
        lambda: None,
        {},
        [],
        debug_mode=None,
        node_id="t.py::test_ok",
        backend=None,
    )
    assert result.status == StatusKind.PASSED, f"expected passed, got {result.status}"


def test_run_base_non_debuggable_exception_skips_post_mortem():
    """Skipped exceptions should not trigger post_mortem."""
    rec = RecordingDebugger()

    class Skipped(Exception):
        pass

    def skip_test():
        raise Skipped("not today")

    _run_base(
        skip_test,
        {},
        [],
        debug_mode="always",
        node_id="t.py::test_skip",
        backend=rec,
    )
    assert rec.trace_count == 1, "trace should still be called before test"
    assert len(rec.post_mortem_tracebacks) == 0, (
        "post_mortem should NOT be called for non-debuggable exception"
    )


def test_trace_before_test_suspends_capture_during_call():
    """Capture should be suspended when backend.trace() is called."""
    cap = _StdCapture()
    suspended_during_trace = False

    class SpyDebugger:
        trace_count = 0
        post_mortem_tracebacks: list = []

        def trace(self):
            nonlocal suspended_during_trace
            suspended_during_trace = sys.stdout is cap._old_stdout
            self.trace_count += 1

        def post_mortem(self, tb):
            pass

    spy = SpyDebugger()
    _trace_before_test({"cap": cap}, "t.py::test_x", spy)

    assert suspended_during_trace, "capture should be suspended during trace()"
    assert sys.stdout is not cap._old_stdout, (
        "capture should be re-enabled after trace returns"
    )
    cap._restore()  # cleanup


def test_trace_before_test_no_capture_kwargs():
    """_trace_before_test should work when no capture fixtures in kwargs."""
    rec = RecordingDebugger()
    _trace_before_test({"x": 42}, "t.py::test_x", rec)
    assert rec.trace_count == 1, "trace should be called once"


def test_debug_post_mortem_permanently_suspends_capture():
    """_debug_post_mortem should permanently restore capture."""
    cap = _StdCapture()
    old_stdout = cap._old_stdout
    rec = RecordingDebugger()

    try:
        raise AssertionError("test failure")
    except AssertionError as exc:
        _debug_post_mortem({"cap": cap}, "t.py::test_fail", exc, rec)

    assert sys.stdout is old_stdout, "capture should be permanently restored"
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 post_mortem call, got {len(rec.post_mortem_tracebacks)}"
    )
    assert rec.post_mortem_tracebacks[0] is not None, "traceback should be present"


def test_debug_post_mortem_no_capture_kwargs():
    """_debug_post_mortem should work when no capture fixtures in kwargs."""
    rec = RecordingDebugger()
    try:
        raise ValueError("oops")
    except ValueError as exc:
        _debug_post_mortem({}, "t.py::test_err", exc, rec)
    assert len(rec.post_mortem_tracebacks) == 1, "post_mortem should be called once"
