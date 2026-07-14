"""ContextVar-based diagnostic collector for the Python bridge.

Follows the same pattern as ``_fixtures_registry_var`` and
``_helpers_registry_var`` — FixtureSession sets the collector on entry,
clears on exit. Any call site can emit diagnostics without threading
the session through.
"""

from __future__ import annotations

__all__ = ["_diagnostic_collector_var", "emit_diagnostic"]

from contextvars import ContextVar

from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

_diagnostic_collector_var: ContextVar[list[Diagnostic] | None] = ContextVar(
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
