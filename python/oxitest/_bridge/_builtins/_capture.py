from __future__ import annotations

__all__ = [
    "CaptureResult",
    "_CaptureBase",
    "StdCapture",
    "FdCapture",
    "_StdCaptureFixture",
    "_FdCaptureFixture",
]

import io
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_type import injectable


@dataclass
class CaptureResult:
    r"""Captured stdout and stderr returned by `readouterr()`.

    Attributes:
        out: Everything written to stdout since the last `readouterr()` call.
        err: Everything written to stderr since the last `readouterr()` call.

    Example:
        ```python
        def test_output(cap: StdCapture) -> None:
            print("hello")
            result = cap.readouterr()
            assert result.out == "hello\\n"
            assert result.err == ""
        ```
    """

    out: str
    err: str


class _CaptureBase(ABC):
    """Abstract base for stdout/stderr capture implementations."""

    @abstractmethod
    def readouterr(self) -> CaptureResult:
        """Return and clear all captured output since the last call."""

    @abstractmethod
    def _suspend(self) -> None:
        """Restore original streams/fds (pause capturing)."""

    @abstractmethod
    def _resume(self) -> None:
        """Re-apply capture streams/fds (resume capturing)."""

    @contextmanager
    def disabled(self) -> Generator[None, None, None]:
        """Context manager: temporarily restore real output so it passes through."""
        self._suspend()
        try:
            yield
        finally:
            self._resume()

    def _restore(self) -> None:
        """Permanently restore original streams/fds and release resources."""
        self._suspend()


@injectable
class StdCapture(_CaptureBase):
    r"""Captures `sys.stdout` and `sys.stderr` at the Python stream level.

    Replaces `sys.stdout` and `sys.stderr` with in-memory `StringIO`
    objects for the duration of the test. Does **not** capture output from C
    extensions or subprocesses that write directly to file descriptors — use
    `FdCapture` for that.

    Example:
        ```python
        def test_prints(cap: StdCapture) -> None:
            print("hello")
            captured = cap.readouterr()
            assert captured.out == "hello\\n"
        ```
    """

    def __init__(self) -> None:
        self._out_buf = io.StringIO()
        self._err_buf = io.StringIO()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        sys.stdout = self._out_buf
        sys.stderr = self._err_buf

    def readouterr(self) -> CaptureResult:
        out = self._out_buf.getvalue()
        err = self._err_buf.getvalue()
        self._out_buf.truncate(0)
        self._out_buf.seek(0)
        self._err_buf.truncate(0)
        self._err_buf.seek(0)
        return CaptureResult(out=out, err=err)

    def _suspend(self) -> None:
        sys.stdout = self._old_stdout
        sys.stderr = self._old_stderr

    def _resume(self) -> None:
        sys.stdout = self._out_buf
        sys.stderr = self._err_buf


@injectable
class FdCapture(_CaptureBase):
    """Captures stdout and stderr at file-descriptor level (fd 1 and fd 2).

    Redirects the underlying OS file descriptors, so output from C extensions,
    subprocesses, and any code that writes directly to fd 1/2 is captured.

    Example:
        ```python
        import ctypes
        def test_c_output(cap: FdCapture) -> None:
            ctypes.cdll.LoadLibrary("libc.so.6").puts(b"from C")
            captured = cap.readouterr()
            assert "from C" in captured.out
        ```
    """

    def __init__(self) -> None:
        self._old_stdout_fd = os.dup(1)
        self._old_stderr_fd = os.dup(2)
        self._stdout_tmp = tempfile.TemporaryFile()
        self._stderr_tmp = tempfile.TemporaryFile()
        os.dup2(self._stdout_tmp.fileno(), 1)
        os.dup2(self._stderr_tmp.fileno(), 2)

    def readouterr(self) -> CaptureResult:
        sys.stdout.flush()
        sys.stderr.flush()
        self._stdout_tmp.seek(0)
        out = self._stdout_tmp.read().decode(errors="replace")
        self._stdout_tmp.truncate(0)
        self._stdout_tmp.seek(0)
        self._stderr_tmp.seek(0)
        err = self._stderr_tmp.read().decode(errors="replace")
        self._stderr_tmp.truncate(0)
        self._stderr_tmp.seek(0)
        return CaptureResult(out=out, err=err)

    def _suspend(self) -> None:
        os.dup2(self._old_stdout_fd, 1)
        os.dup2(self._old_stderr_fd, 2)

    def _resume(self) -> None:
        os.dup2(self._stdout_tmp.fileno(), 1)
        os.dup2(self._stderr_tmp.fileno(), 2)

    def _restore(self) -> None:
        self._suspend()
        os.close(self._old_stdout_fd)
        os.close(self._old_stderr_fd)
        self._stdout_tmp.close()
        self._stderr_tmp.close()


class _StdCaptureFixture(BuiltinFixture, fixture_type=StdCapture):
    def create(self, ctx: _BuiltinContext) -> StdCapture:
        cap = StdCapture()
        ctx.teardown_stack.append(cap._restore)
        return cap


class _FdCaptureFixture(BuiltinFixture, fixture_type=FdCapture):
    def create(self, ctx: _BuiltinContext) -> FdCapture:
        cap = FdCapture()
        ctx.teardown_stack.append(cap._restore)
        return cap
