"""Tests for --debug post-mortem debugging support."""

import io
import sys
from dataclasses import dataclass
from typing import ClassVar

import oxitest
from oxitest import DebuggerBackend, StdCapture, helpers
from oxitest._bridge._debugger import _PdbBackend
from oxitest._bridge._diagnostics import is_debuggable as _is_debuggable
from oxitest._bridge._runners import DebugContext
from oxitest._bridge.executor import (
    _debug_post_mortem,
    _print_banner,
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
    cap = StdCapture()
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
    _print_banner(
        "DEBUG",
        "tests/test_math.py::test_add",
        f"{type(exc).__name__}: {exc}",
        "Entering debugger (type 'h' for help, 'q' to quit)",
        file=buf,
    )
    output = buf.getvalue()
    assert "tests/test_math.py::test_add" in output, f"missing node_id: {output!r}"
    assert "AssertionError" in output, f"missing exc type: {output!r}"
    assert "expected 3, got 5" in output, f"missing exc message: {output!r}"
    assert "'h' for help" in output, f"missing help hint: {output!r}"


def test_print_trace_banner_contains_node_id():
    """Trace banner should include node ID and stepping message."""
    buf = io.StringIO()
    _print_banner(
        "TRACE",
        "tests/test_math.py::test_add",
        "Stepping into test (type 'c' to run, 'q' to quit)",
        file=buf,
    )
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
    assert isinstance(helpers.common.RecordingDebugger(), DebuggerBackend), (
        "RecordingDebugger should satisfy DebuggerBackend protocol"
    )


def test_recording_debugger_records_trace():
    """RecordingDebugger should count trace() calls."""
    rec = helpers.common.RecordingDebugger()
    rec.trace()
    rec.trace()
    assert rec.trace_count == 2, f"expected 2 trace calls, got {rec.trace_count}"


def test_recording_debugger_records_post_mortem():
    """RecordingDebugger should record traceback objects."""
    rec = helpers.common.RecordingDebugger()
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


def _make_failing_fn():
    def failing():
        raise AssertionError("boom")

    return failing


@dataclass(frozen=True)
class DebugModeCase:
    mode: str | None
    passing: bool
    expect_trace: int
    expect_post_mortem: int


@oxitest.parametrize(
    always_pass=DebugModeCase(
        "always", passing=True, expect_trace=1, expect_post_mortem=0
    ),
    always_fail=DebugModeCase(
        "always", passing=False, expect_trace=1, expect_post_mortem=1
    ),
    post_mortem_fail=DebugModeCase(
        "post-mortem", passing=False, expect_trace=0, expect_post_mortem=1
    ),
    post_mortem_pass=DebugModeCase(
        "post-mortem", passing=True, expect_trace=0, expect_post_mortem=0
    ),
    no_debug=DebugModeCase(None, passing=True, expect_trace=0, expect_post_mortem=0),
)
def test_run_base_debug_mode(
    mode: str | None,
    *,
    passing: bool,
    expect_trace: int,
    expect_post_mortem: int,
) -> None:
    """Debug mode matrix: verify trace/post_mortem calls for each mode x outcome."""
    rec = helpers.common.RecordingDebugger()

    fn = (lambda: None) if passing else _make_failing_fn()
    result = _run_base(
        fn,
        {},
        (),
        debug=DebugContext(
            mode=mode,
            node_id="t.py::test_x",
            backend=rec if mode else None,
            file=io.StringIO(),
        ),
    )

    expected_status = StatusKind.PASSED if passing else StatusKind.FAILED
    assert result.status == expected_status, (
        f"expected {expected_status}, got {result.status}"
    )
    assert rec.trace_count == expect_trace, (
        f"expected {expect_trace} trace calls, got {rec.trace_count}"
    )
    assert len(rec.post_mortem_tracebacks) == expect_post_mortem, (
        f"expected {expect_post_mortem} post_mortem calls, "
        f"got {len(rec.post_mortem_tracebacks)}"
    )


def test_run_base_non_debuggable_exception_skips_post_mortem():
    """Skipped exceptions should not trigger post_mortem."""
    rec = helpers.common.RecordingDebugger()

    class Skipped(Exception):
        pass

    def skip_test():
        raise Skipped("not today")

    _run_base(
        skip_test,
        {},
        (),
        debug=DebugContext(
            mode="always",
            node_id="t.py::test_skip",
            backend=rec,
            file=io.StringIO(),
        ),
    )
    assert rec.trace_count == 1, "trace should still be called before test"
    assert len(rec.post_mortem_tracebacks) == 0, (
        "post_mortem should NOT be called for non-debuggable exception"
    )


def test_trace_before_test_suspends_capture_during_call():
    """Capture should be suspended when backend.trace() is called."""
    cap = StdCapture()
    suspended_during_trace = False

    class SpyDebugger:
        trace_count = 0
        post_mortem_tracebacks: ClassVar[list] = []

        def trace(self):
            nonlocal suspended_during_trace
            suspended_during_trace = sys.stdout is cap._old_stdout
            self.trace_count += 1

        def post_mortem(self, tb):
            pass

    spy = SpyDebugger()
    _trace_before_test({"cap": cap}, "t.py::test_x", spy, file=io.StringIO())

    assert suspended_during_trace, "capture should be suspended during trace()"
    assert sys.stdout is not cap._old_stdout, (
        "capture should be re-enabled after trace returns"
    )
    cap._restore()  # cleanup


def test_trace_before_test_no_capture_kwargs():
    """_trace_before_test should work when no capture fixtures in kwargs."""
    rec = helpers.common.RecordingDebugger()
    _trace_before_test({"x": 42}, "t.py::test_x", rec, file=io.StringIO())
    assert rec.trace_count == 1, "trace should be called once"


def test_debug_post_mortem_permanently_suspends_capture():
    """_debug_post_mortem should permanently restore capture."""
    cap = StdCapture()
    old_stdout = cap._old_stdout
    rec = helpers.common.RecordingDebugger()

    try:
        raise AssertionError("test failure")
    except AssertionError as exc:
        _debug_post_mortem(
            {"cap": cap}, "t.py::test_fail", exc, rec, file=io.StringIO()
        )

    assert sys.stdout is old_stdout, "capture should be permanently restored"
    assert len(rec.post_mortem_tracebacks) == 1, (
        f"expected 1 post_mortem call, got {len(rec.post_mortem_tracebacks)}"
    )
    assert rec.post_mortem_tracebacks[0] is not None, "traceback should be present"


def test_debug_post_mortem_no_capture_kwargs():
    """_debug_post_mortem should work when no capture fixtures in kwargs."""
    rec = helpers.common.RecordingDebugger()
    try:
        raise ValueError("oops")
    except ValueError as exc:
        _debug_post_mortem({}, "t.py::test_err", exc, rec, file=io.StringIO())
    assert len(rec.post_mortem_tracebacks) == 1, "post_mortem should be called once"
