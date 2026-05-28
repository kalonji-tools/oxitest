"""Tests for --debug post-mortem debugging support."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oxitest._bridge._builtins._capture import _StdCapture
from oxitest._bridge._middleware import _is_debuggable
from oxitest._bridge.executor import _print_debug_banner, _suspend_capture


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
