from __future__ import annotations

__all__ = ["_WarnCapture", "_WarnCaptureFixture"]

import warnings

from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_type import injectable


@injectable
class _WarnCapture:
    """Captures all `warnings.warn()` calls issued during a test.

    Installed automatically when a test parameter is annotated `warn: WarnCapture`.
    Access captured warnings via `warn.list`; reset between assertions with
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
        self.list: list[warnings.WarningMessage] = []
        self._all_captured_ids: set[int] = set()
        self._orig_showwarnmsg = getattr(warnings, "_showwarnmsg")
        _captured = self.list
        _all_ids = self._all_captured_ids

        def _intercepting_showwarnmsg(msg: warnings.WarningMessage) -> None:
            _captured.append(msg)
            _all_ids.add(id(msg))
            self._orig_showwarnmsg(msg)

        setattr(warnings, "_showwarnmsg", _intercepting_showwarnmsg)

    def clear(self) -> None:
        """Clear all captured warnings, resetting `warn.list` to `[]`."""
        self.list.clear()

    def _teardown(self) -> None:
        setattr(warnings, "_showwarnmsg", self._orig_showwarnmsg)


class _WarnCaptureFixture(BuiltinFixture, fixture_type=_WarnCapture):
    def create(self, ctx: _BuiltinContext) -> _WarnCapture:
        cap = _WarnCapture()
        ctx.teardown_stack.append(cap._teardown)
        return cap
