"""Tests for Diagnostic data model and collector."""

from __future__ import annotations

from oxitest._bridge._diagnostic_collector import (
    _diagnostic_collector_var,
    emit_diagnostic,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.conftest_loader import create_session
from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest._bridge.result import Diagnostic, DiagnosticSeverity


def test_diagnostic_severity_values() -> None:
    """DiagnosticSeverity auto() produces lowercase string values."""
    assert DiagnosticSeverity.ERROR == "error", (
        "ERROR should produce 'error' via auto()"
    )
    assert DiagnosticSeverity.WARNING == "warning", (
        "WARNING should produce 'warning' via auto()"
    )
    assert DiagnosticSeverity.NOTICE == "notice", (
        "NOTICE should produce 'notice' via auto()"
    )


def test_diagnostic_frozen_dataclass() -> None:
    """Diagnostic is frozen — fields cannot be mutated after construction."""
    diag = Diagnostic(
        severity=DiagnosticSeverity.WARNING,
        context="fixture teardown",
        message="error in teardown of fixture 'db'",
        file="/conftest.py",
        lineno=42,
    )
    assert diag.severity == DiagnosticSeverity.WARNING, (
        "severity field should match constructor value"
    )
    assert diag.context == "fixture teardown", (
        "context field should match constructor value"
    )
    assert diag.file == "/conftest.py", "file field should match constructor value"


def test_diagnostic_default_fields() -> None:
    """Diagnostic file and lineno default to empty/zero."""
    diag = Diagnostic(
        severity=DiagnosticSeverity.NOTICE,
        context="tempdir",
        message="KEPT /tmp/foo",
    )
    assert diag.file == "", "file should default to empty string"
    assert diag.lineno == 0, "lineno should default to 0"


def test_diagnostic_to_wire() -> None:
    """Diagnostic.to_wire() produces the expected JSON-serializable dict."""
    diag = Diagnostic(
        severity=DiagnosticSeverity.ERROR,
        context="plugin activation",
        message="invalid JSON",
        file="plugin_loader.py",
        lineno=434,
    )
    wire = diag.to_wire()
    assert wire["type"] == "diagnostic", "wire format must include type discriminator"
    assert wire["severity"] == "error", (
        "severity should serialize as StrEnum string value"
    )
    assert wire["context"] == "plugin activation", "context should be passed through"
    assert wire["message"] == "invalid JSON", "message should be passed through"
    assert wire["file"] == "plugin_loader.py", "file should be passed through"
    assert wire["lineno"] == 434, "lineno should be passed through"


def test_emit_diagnostic_appends_to_collector() -> None:
    """emit_diagnostic appends a Diagnostic to the active collector list."""
    collector: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(collector)
    try:
        emit_diagnostic(
            DiagnosticSeverity.WARNING,
            "fixture teardown",
            "error in teardown",
        )
        assert len(collector) == 1, (
            "emit_diagnostic should append exactly one Diagnostic"
        )
        assert collector[0].severity == DiagnosticSeverity.WARNING, (
            "emitted diagnostic should have the given severity"
        )
        assert collector[0].context == "fixture teardown", (
            "emitted diagnostic should have the given context"
        )
    finally:
        _diagnostic_collector_var.reset(token)


def test_emit_diagnostic_with_location() -> None:
    """emit_diagnostic passes file and lineno through to Diagnostic."""
    collector: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(collector)
    try:
        emit_diagnostic(
            DiagnosticSeverity.NOTICE,
            "tempdir",
            "KEPT /tmp/foo",
            file="/test.py",
            lineno=10,
        )
        assert collector[0].file == "/test.py", (
            "file should be passed through to Diagnostic"
        )
        assert collector[0].lineno == 10, (
            "lineno should be passed through to Diagnostic"
        )
    finally:
        _diagnostic_collector_var.reset(token)


def test_emit_diagnostic_without_collector_is_noop() -> None:
    """emit_diagnostic does nothing when no collector is active."""
    # Ensure no collector is set
    token = _diagnostic_collector_var.set(None)
    try:
        # Should not raise
        emit_diagnostic(
            DiagnosticSeverity.ERROR,
            "test",
            "should be silently dropped",
        )
    finally:
        _diagnostic_collector_var.reset(token)


def test_session_sets_and_clears_diagnostic_collector() -> None:
    """FixtureSession sets _diagnostic_collector_var on init, clears on teardown."""
    # Reset the ContextVar to simulate being the outermost session (no outer runner).
    token = _diagnostic_collector_var.set(None)
    try:
        session = FixtureSession([], PluginRegistry())

        # Collector should be active after session construction
        collector = _diagnostic_collector_var.get()
        assert collector is not None, (
            "FixtureSession should set the diagnostic collector ContextVar"
        )
        assert collector is session.diagnostics, (
            "ContextVar should point to the session's diagnostics list"
        )

        # emit_diagnostic should work and land in session.diagnostics
        emit_diagnostic(DiagnosticSeverity.NOTICE, "test", "hello")
        assert len(session.diagnostics) == 1, (
            "diagnostics emitted while session is active should accumulate"
        )
    finally:
        _diagnostic_collector_var.reset(token)


def test_session_end_clears_diagnostic_collector() -> None:
    """FixtureSession.end_session() clears _diagnostic_collector_var."""
    token = _diagnostic_collector_var.set(None)
    try:
        session = FixtureSession([], PluginRegistry())

        # Collector should be active
        assert _diagnostic_collector_var.get() is not None, (
            "FixtureSession should set the diagnostic collector ContextVar"
        )

        session.end_session()

        # Collector should be cleared after end_session
        assert _diagnostic_collector_var.get() is None, (
            "end_session should clear the diagnostic collector ContextVar"
        )
    finally:
        _diagnostic_collector_var.reset(token)


def test_create_session_returns_diagnostics_tuple() -> None:
    """create_session returns (session, violations, diagnostics) 3-tuple."""
    result = create_session([])
    assert len(result) == 3, (
        f"create_session should return a 3-tuple, got {len(result)}-tuple"
    )
    session, violations, diagnostics = result
    assert isinstance(session, FixtureSession), (
        "first element should be a FixtureSession"
    )
    assert isinstance(violations, list), "second element should be a list of violations"
    assert isinstance(diagnostics, list), (
        "third element should be a list of diagnostics"
    )
