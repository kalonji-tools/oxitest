from __future__ import annotations

__all__ = [
    "CaptureResult",
    "FdCapture",
    "StdCapture",
    "_CaptureBase",
    "_FdCaptureFixture",
    "_StdCaptureFixture",
]

import io
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtins._base import BuiltinFixture

if TYPE_CHECKING:
    from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._fixture_type import injectable


@dataclass(frozen=True, slots=True)
class CaptureResult:
    r"""Captured stdout and stderr returned by ``readouterr()``.

    Attributes:
        out: Everything written to stdout since the last ``readouterr()`` call.
        err: Everything written to stderr since the last ``readouterr()`` call.

    See Also:
        - :class:`StdCapture` — Python-stream-level capture producer.
        - :class:`FdCapture` — fd-level capture producer.

    Examples:
        Frozen dataclass — construct directly for illustration or in
        test doubles:

        >>> from oxitest import CaptureResult
        >>> result = CaptureResult(out="hello\n", err="")
        >>> result.out
        'hello\n'
        >>> result.err
        ''

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

    def close(self) -> None:
        """Permanently restore original streams/fds and release resources."""
        self._suspend()


@injectable
class StdCapture(_CaptureBase):
    r"""Captures ``sys.stdout`` and ``sys.stderr`` at the Python stream level.

    Replaces ``sys.stdout`` and ``sys.stderr`` with in-memory
    :class:`io.StringIO` objects for the duration of the test. Does
    **not** capture output from C extensions or subprocesses that write
    directly to file descriptors — use :class:`FdCapture` for that.

    Line endings: this fixture intercepts *above* Python's text layer, so
    ``print()`` output arrives with ``\n`` on every platform, Windows
    included. :class:`FdCapture` taps below that layer and reports
    ``\r\n`` there — see its docstring.

    See Also:
        - :class:`FdCapture` — fd-level capture for C-extension and
          subprocess output. Reports a different line ending on Windows;
          the two are not interchangeable.
        - :class:`CaptureResult` — return type of ``readouterr()``.

    Examples:
        Injected by parameter type — declare ``cap: StdCapture`` on the
        test::

            def test_prints(cap: StdCapture) -> None:
                print("hello")
                captured = cap.readouterr()
                assert captured.out == "hello\n"

        For illustration, construct + close directly (production code
        should let oxitest manage the lifecycle):

        >>> from oxitest import StdCapture
        >>> cap = StdCapture()
        >>> print("hello")
        >>> result = cap.readouterr()
        >>> cap.close()
        >>> result.out
        'hello\n'
        >>> result.err
        ''

    """

    def __init__(self) -> None:
        self._out_buf = io.StringIO()
        self._err_buf = io.StringIO()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        sys.stdout = self._out_buf
        sys.stderr = self._err_buf

    @property
    def original_stdout(self) -> Any:
        """The original sys.stdout before capture was installed."""
        return self._old_stdout

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
    r"""Captures stdout and stderr at file-descriptor level (fd 1 and fd 2).

    Redirects the underlying OS file descriptors, so output from C
    extensions, subprocesses, and any code that writes directly to fd 1
    or fd 2 is captured. :class:`StdCapture` is cheaper and enough for
    ``print()``-style Python output, but it is not the same measurement
    — see Line endings below before treating one as a substitute for
    the other.

    Line endings: this fixture reports the bytes that reached the file
    descriptor. On Windows, Python's text layer translates ``\n`` to
    ``\r\n`` before ``print()`` output gets there, so ``readouterr()``
    returns ``"text\r\n"`` where :class:`StdCapture` returns
    ``"text\n"``. Bytes written with ``os.write`` bypass that layer and
    arrive unchanged. On POSIX both fixtures return ``\n``, which is why
    the difference only appears in CI.

    See Also:
        - :class:`StdCapture` — Python-stream-level capture (cheaper,
          misses C/subprocess output, and returns ``\n`` on Windows
          where this fixture returns ``\r\n``).
        - :class:`CaptureResult` — return type of ``readouterr()``.

    Examples:
        Injected by parameter type — declare ``cap: FdCapture`` on the
        test::

            import ctypes
            def test_c_output(cap: FdCapture) -> None:
                ctypes.cdll.LoadLibrary("libc.so.6").puts(b"from C")
                captured = cap.readouterr()
                assert "from C" in captured.out

        For illustration, direct construction + close (production code
        should let oxitest manage the lifecycle):

        >>> from oxitest import FdCapture
        >>> import os
        >>> cap = FdCapture()
        >>> _ = os.write(1, b"hello\n")
        >>> result = cap.readouterr()
        >>> cap.close()
        >>> result.out
        'hello\n'

    """

    def __init__(self) -> None:
        self._old_stdout_fd = os.dup(1)
        self._old_stderr_fd = os.dup(2)
        self._stdout_tmp = tempfile.TemporaryFile()  # noqa: SIM115 — lifecycle managed by start()/close()
        self._stderr_tmp = tempfile.TemporaryFile()  # noqa: SIM115 — lifecycle managed by start()/close()
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

    def close(self) -> None:
        self._suspend()
        os.close(self._old_stdout_fd)
        os.close(self._old_stderr_fd)
        self._stdout_tmp.close()
        self._stderr_tmp.close()


class _StdCaptureFixture(BuiltinFixture, fixture_type=StdCapture):
    def create(self, *, ctx: _BuiltinContext) -> StdCapture:
        cap = StdCapture()
        ctx.teardown_stack.append(cap.close)
        return cap


class _FdCaptureFixture(BuiltinFixture, fixture_type=FdCapture):
    def create(self, *, ctx: _BuiltinContext) -> FdCapture:
        cap = FdCapture()
        ctx.teardown_stack.append(cap.close)
        return cap
