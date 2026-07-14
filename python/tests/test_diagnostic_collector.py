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
        "StrEnum auto() must produce lowercase — Rust deserializes severity strings"
    )
    assert DiagnosticSeverity.WARNING == "warning", (
        "StrEnum auto() must produce lowercase — Rust deserializes severity strings"
    )
    assert DiagnosticSeverity.NOTICE == "notice", (
        "StrEnum auto() must produce lowercase — Rust deserializes severity strings"
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
        "frozen dataclass must preserve severity — mutation would misroute diagnostics"
    )
    assert diag.context == "fixture teardown", (
        "frozen dataclass must preserve context — Rust reporter groups by this field"
    )
    assert diag.file == "/conftest.py", (
        "frozen dataclass must preserve file — reporter uses it for location rendering"
    )


def test_diagnostic_default_fields() -> None:
    """Diagnostic file and lineno default to empty/zero."""
    diag = Diagnostic(
        severity=DiagnosticSeverity.NOTICE,
        context="tempdir",
        message="KEPT /tmp/foo",
    )
    assert diag.file == "", (
        "omitted file must default to empty — Rust converts empty to None for rendering"
    )
    assert diag.lineno == 0, (
        "omitted lineno must default to 0 — Rust converts 0 to None for rendering"
    )


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
    assert wire["type"] == "diagnostic", (
        "type discriminator is how the Rust drain dispatches diagnostic vs result lines"
    )
    assert wire["severity"] == "error", (
        "severity must serialize as lowercase string — Rust match arms expect it"
    )
    assert wire["context"] == "plugin activation", (
        "context loss would make the Rust reporter group diagnostics incorrectly"
    )
    assert wire["message"] == "invalid JSON", (
        "message loss would produce empty diagnostic entries in the summary block"
    )
    assert wire["file"] == "plugin_loader.py", (
        "file loss would prevent location-aware rendering in the reporter"
    )
    assert wire["lineno"] == 434, (
        "lineno loss would prevent location-aware rendering in the reporter"
    )


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
            "each emit must append exactly one entry — duplicates or drops would"
            " corrupt the Rust reporter's dedup counts"
        )
        assert collector[0].severity == DiagnosticSeverity.WARNING, (
            "severity mismatch would miscolor the diagnostic in the reporter output"
        )
        assert collector[0].context == "fixture teardown", (
            "context mismatch would misgroup the diagnostic in dedup and rendering"
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
            "file must survive emit → Diagnostic — Rust uses it for location rendering"
        )
        assert collector[0].lineno == 10, (
            "lineno must survive emit → Diagnostic — Rust uses it for"
            " location rendering"
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
        "Rust bridge extracts the session via PyO3 — wrong type would crash the run"
    )
    assert isinstance(violations, list), (
        "Rust bridge iterates violations for strict-mode — wrong type would crash"
    )
    assert isinstance(diagnostics, list), (
        "Rust bridge drains diagnostics into RunStats — wrong type would crash"
    )
