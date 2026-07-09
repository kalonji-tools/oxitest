"""Debugger backend abstraction.

Defines the ``DebuggerBackend`` protocol and the default ``_PdbBackend``
implementation that wraps stdlib ``pdb``.
"""

from __future__ import annotations

__all__ = ["DebuggerBackend", "_PdbBackend"]

import pdb  # noqa: T100
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@runtime_checkable
class DebuggerBackend(Protocol):
    """Protocol for debugger backends.

    Plugins implement this to provide alternative debuggers (ipdb, pudb, etc.).
    oxitest owns capture management, banners, and *when* to call the debugger.
    The backend only provides the *what* — the actual debugger interaction.
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
