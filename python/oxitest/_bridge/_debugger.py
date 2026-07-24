"""Debugger backend abstraction.

Defines the ``DebuggerBackend`` protocol and the default ``_PdbBackend``
implementation that wraps stdlib ``pdb``.
"""

from __future__ import annotations

__all__ = ["_NULL_DEBUGGER", "DebuggerBackend", "_PdbBackend"]

import pdb  # noqa: T100
from typing import TYPE_CHECKING, Never, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@runtime_checkable
class DebuggerBackend(Protocol):
    """Protocol for debugger backends.

    Plugins implement this to provide alternative debuggers (ipdb, pudb, etc.).
    oxitest owns capture management, banners, and *when* to call the debugger.
    The backend only provides the *what* — the actual debugger interaction.

    See Also:
        - ``oxitest.Plugin.debugger_backend`` — how a plugin exposes an
          implementation to oxitest.
        - ``pdb.set_trace`` / ``pdb.post_mortem`` — the stdlib fallback
          used when no plugin registers a backend.

    Examples:
        Any object with matching ``trace`` and ``post_mortem`` methods
        satisfies the protocol:

        >>> from oxitest import DebuggerBackend
        >>> class MyBackend:
        ...     def trace(self) -> None: pass
        ...     def post_mortem(self, tb) -> None: pass
        >>> isinstance(MyBackend(), DebuggerBackend)
        True
        >>> isinstance(object(), DebuggerBackend)
        False

    """

    def trace(self) -> None:
        """Enter the debugger for interactive stepping.

        Called before test execution in ``--debug=always`` mode.  The user
        types ``c`` (continue) to proceed into the test, or ``q`` to quit.
        """
        ...

    def post_mortem(self, tb: TracebackType) -> None:
        """Enter the debugger for post-mortem inspection of a failure.

        Called after a test raises a debuggable exception in any ``--debug``
        mode.  The traceback is the exception's ``__traceback__``.

        Args:
            tb: The traceback from the failed test.

        """
        ...


class _PdbBackend:
    """Default debugger backend wrapping stdlib ``pdb``."""

    def trace(self) -> None:
        pdb.set_trace()  # noqa: T100

    def post_mortem(self, tb: TracebackType) -> None:
        pdb.post_mortem(tb)


class _NullDebugger:
    """Null-object stand-in when no plugin provides a debugger backend.

    Held on ``Plugin.debugger_backend`` and ``PluginRegistry.debugger_backend``
    as the canonical "no debugger registered" value. Discovery filters this
    out by identity; any actual method call is a bug and raises
    ``AssertionError``.
    """

    def trace(self) -> Never:
        msg = "null-object DebuggerBackend method called — discovery filter is broken"
        raise AssertionError(msg)

    def post_mortem(self, tb: TracebackType) -> Never:
        # Reference tb in the message to satisfy ARG002 while keeping the
        # protocol-required parameter name.
        msg = (
            f"null-object DebuggerBackend method called (tb type={type(tb).__name__})"
            " — discovery filter is broken"
        )
        raise AssertionError(msg)


_NULL_DEBUGGER = _NullDebugger()
