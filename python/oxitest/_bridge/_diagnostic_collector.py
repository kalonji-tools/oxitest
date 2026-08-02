"""ContextVar-based diagnostic collector for the Python bridge.

Follows the same pattern as ``_fixtures_registry_var`` — FixtureSession
sets the collector on entry, clears on exit. Any call site can emit
diagnostics without threading the session through.
"""

from __future__ import annotations

__all__ = ["DiagnosticSink", "_diagnostic_collector_var", "emit_diagnostic"]

from contextvars import ContextVar
from typing import Protocol

from oxitest._bridge.result import Diagnostic, DiagnosticSeverity


class DiagnosticSink(Protocol):
    """Anything a diagnostic can be handed to.

    ``list[Diagnostic]`` satisfies this structurally, so the serial path and
    every existing caller are unchanged. Worker subprocesses install a sink
    that writes straight to the LDJSON pipe instead of accumulating, because a
    worker has no Rust-side drain and an accumulated list is one nobody reads
    (#1840).
    """

    def append(self, diagnostic: Diagnostic, /) -> None: ...


_diagnostic_collector_var: ContextVar[DiagnosticSink | None] = ContextVar(
    "_diagnostic_collector_var", default=None
)


def emit_diagnostic(
    severity: DiagnosticSeverity,
    context: str,
    message: str,
    *,
    file: str = "",
    lineno: int = 0,
) -> None:
    """Emit a diagnostic to the active collector.

    No-op if no collector is active (e.g., during module import before
    session start).
    """
    collector = _diagnostic_collector_var.get()
    if collector is None:
        return
    collector.append(
        Diagnostic(
            severity=severity,
            context=context,
            message=message,
            file=file,
            lineno=lineno,
        )
    )
