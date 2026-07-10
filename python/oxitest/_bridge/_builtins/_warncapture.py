from __future__ import annotations

__all__ = ["WarnCapture", "_WarnCaptureFixture"]

import warnings
from warnings import WarningMessage

from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_type import injectable


@injectable
class WarnCapture:
    """Captures all `warnings.warn()` calls issued during a test.

    Installed automatically when a test parameter is annotated `warn: WarnCapture`.
    Access captured warnings via `warn.warnings`; reset between assertions with
    `warn.clear()`.

    Implementation note: the executor wraps each test in
    `warnings.catch_warnings(record=True)`, which replaces
    `warnings._showwarnmsg_impl` with its own accumulator list.  Patching
    `showwarning` or `_showwarnmsg_impl` would therefore be overwritten.
    Instead this class patches `warnings._showwarnmsg` — the function that
    `warnings.warn()` calls directly and that `catch_warnings` does *not*
    save or restore — so the intercept survives any number of nested
    `catch_warnings` contexts.
    """

    def __init__(self) -> None:
        self._warnings: list[WarningMessage] = []
        self._all_captured_ids: set[int] = set()
        self._orig_showwarnmsg = getattr(warnings, "_showwarnmsg")  # noqa: B009 — private CPython API not in type stubs
        _captured = self._warnings
        _all_ids = self._all_captured_ids

        def _intercepting_showwarnmsg(msg: WarningMessage) -> None:
            _captured.append(msg)
            _all_ids.add(id(msg))
            self._orig_showwarnmsg(msg)

        setattr(warnings, "_showwarnmsg", _intercepting_showwarnmsg)  # noqa: B010 — private CPython API

    @property
    def warnings(self) -> tuple[WarningMessage, ...]:
        """Return all captured warnings as an immutable tuple."""
        return tuple(self._warnings)

    def clear(self) -> None:
        """Clear all captured warnings."""
        self._warnings.clear()

    @property
    def captured_ids(self) -> frozenset[int]:
        """Return IDs of all captured warning messages."""
        return frozenset(self._all_captured_ids)

    def close(self) -> None:
        setattr(warnings, "_showwarnmsg", self._orig_showwarnmsg)  # noqa: B010 — private CPython API


class _WarnCaptureFixture(BuiltinFixture, fixture_type=WarnCapture):
    def create(self, *, ctx: _BuiltinContext) -> WarnCapture:
        cap = WarnCapture()
        ctx.teardown_stack.append(cap.close)
        return cap
