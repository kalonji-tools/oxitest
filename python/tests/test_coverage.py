"""Unit tests for coverage integration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import oxitest
import oxitest._bridge._coverage as _cov_mod
import oxitest._bridge.worker as worker_mod
from oxitest import CovReportFormat
from oxitest._bridge._coverage import CoveragePyProvider
from oxitest._bridge._errors import ConflictingCoverageError
from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
from oxitest._bridge.worker import _maybe_start_coverage
from oxitest.plugin import CoverageProvider, Plugin


def test_cov_report_format_values() -> None:
    """All enum variants have the expected string values."""
    assert CovReportFormat.TERM.value == "term", "TERM should be 'term'"
    assert CovReportFormat.HTML.value == "html", "HTML should be 'html'"
    assert CovReportFormat.XML.value == "xml", "XML should be 'xml'"
    assert CovReportFormat.JSON.value == "json", "JSON should be 'json'"
    assert CovReportFormat.NONE.value == "none", "NONE should be 'none'"


def test_cov_report_format_from_string() -> None:
    """Enum can be constructed from string values."""
    assert CovReportFormat("term") is CovReportFormat.TERM, "should round-trip 'term'"
    assert CovReportFormat("html") is CovReportFormat.HTML, "should round-trip 'html'"
    assert CovReportFormat("none") is CovReportFormat.NONE, "should round-trip 'none'"


class _FakeProvider:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def report(self, **_: Any) -> int:
        return 0


def test_fake_provider_satisfies_protocol() -> None:
    """A minimal implementation satisfies the CoverageProvider protocol."""
    provider = _FakeProvider()
    assert isinstance(provider, CoverageProvider), "should satisfy CoverageProvider"


# ─── CoveragePyProvider tests ────────────────────────────────────────────────


@dataclass
class _FakeCovConfig:
    config_files_read: list[str] = field(
        default_factory=lambda: ["/project/pyproject.toml"]
    )


@dataclass
class _FakeCovInstance:
    """Dataclass-based test double for a coverage.Coverage() object."""

    config: _FakeCovConfig = field(default_factory=_FakeCovConfig)
    started: bool = False
    stopped: bool = False
    saved: bool = False
    combined: bool = False
    report_called: bool = False
    html_called: bool = False
    xml_called: bool = False
    json_called: bool = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def save(self) -> None:
        self.saved = True

    def combine(self) -> None:
        self.combined = True

    def report(self) -> int:
        self.report_called = True
        return 85

    def html_report(self) -> int:
        self.html_called = True
        return 0

    def xml_report(self) -> int:
        self.xml_called = True
        return 0

    def json_report(self) -> int:
        self.json_called = True
        return 0


def _make_fake_coverage_module(instance: _FakeCovInstance) -> ModuleType:
    """Build a fake ``coverage`` module whose ``Coverage()`` returns *instance*."""
    mod = ModuleType("coverage")
    setattr(mod, "Coverage", lambda: instance)  # noqa: B010 — dynamic module attr
    return mod


def test_provider_start_sets_env_var(patcher: oxitest.Patcher) -> None:
    """start() sets COVERAGE_PROCESS_START in os.environ."""
    fake_cov = _FakeCovInstance()
    fake_mod = _make_fake_coverage_module(fake_cov)

    provider = CoveragePyProvider()
    patcher.setenv("COVERAGE_PROCESS_START", "")
    patcher.setattr(_cov_mod, "_coverage", fake_mod)
    provider.start()

    assert fake_cov.started, "coverage.start() should have been called"
    assert os.environ.get("COVERAGE_PROCESS_START") is not None, (
        "COVERAGE_PROCESS_START should be set"
    )


def test_provider_stop_saves_and_combines() -> None:
    """stop() calls stop(), save(), combine() in order."""
    fake_cov = _FakeCovInstance()

    provider = CoveragePyProvider()
    provider.cov = fake_cov
    provider.stop()

    assert fake_cov.stopped, "stop() should have been called"
    assert fake_cov.saved, "save() should have been called"
    assert fake_cov.combined, "combine() should have been called"


def test_provider_report_term() -> None:
    """report(TERM) calls coverage.report()."""
    fake_cov = _FakeCovInstance()

    provider = CoveragePyProvider()
    provider.cov = fake_cov
    result = provider.report(fmt=CovReportFormat.TERM)

    assert fake_cov.report_called, "report() should have been called"
    assert result == 85, f"expected 85, got {result}"


def test_provider_report_html() -> None:
    """report(HTML) calls coverage.html_report()."""
    fake_cov = _FakeCovInstance()

    provider = CoveragePyProvider()
    provider.cov = fake_cov
    provider.report(fmt=CovReportFormat.HTML)

    assert fake_cov.html_called, "html_report() should have been called"


def test_provider_report_none_skips() -> None:
    """report(NONE) does not call any coverage method."""
    fake_cov = _FakeCovInstance()

    provider = CoveragePyProvider()
    provider.cov = fake_cov
    result = provider.report(fmt=CovReportFormat.NONE)

    assert result == 0, f"expected 0, got {result}"
    assert not fake_cov.report_called, "report() should not have been called"


def test_provider_import_error_without_coverage(
    patcher: oxitest.Patcher,
) -> None:
    """start() raises ImportError when coverage.py is not installed."""
    provider = CoveragePyProvider()
    patcher.setattr(_cov_mod, "_coverage", None)
    with oxitest.raises(ImportError, match="pip install coverage"):
        provider.start()


# ─── Worker coverage auto-detection tests ────────────────────────────────────


@dataclass
class _FakeCoverageModule:
    """Dataclass-based test double for the ``coverage`` module."""

    startup_called: bool = False

    def process_startup(self) -> None:
        self.startup_called = True


def test_worker_coverage_startup_when_env_set(
    patcher: oxitest.Patcher,
) -> None:
    """_maybe_start_coverage calls coverage.process_startup() when env var set."""
    fake_mod = _FakeCoverageModule()
    patcher.setenv("COVERAGE_PROCESS_START", "/path/to/config")
    patcher.setattr(worker_mod, "_coverage", fake_mod)
    _maybe_start_coverage()

    assert fake_mod.startup_called, "process_startup() should have been called"


def test_worker_coverage_skipped_when_env_absent(
    patcher: oxitest.Patcher,
) -> None:
    """_maybe_start_coverage does nothing when COVERAGE_PROCESS_START not set."""
    patcher.delenv("COVERAGE_PROCESS_START")
    # Should not raise or try to import coverage
    _maybe_start_coverage()


# ─── Plugin conflict validation tests ────────────────────────────────────────


def test_conflicting_coverage_providers_raises() -> None:
    """Two plugins providing CoverageProvider raises ConflictingCoverageError."""
    provider_a = _FakeProvider()
    provider_b = _FakeProvider()

    registry = PluginRegistry(
        entries=[
            PluginEntry(
                module_name="plugin_a", plugin=Plugin(coverage_provider=provider_a)
            ),
            PluginEntry(
                module_name="plugin_b", plugin=Plugin(coverage_provider=provider_b)
            ),
        ]
    )

    with oxitest.raises(ConflictingCoverageError):
        registry.validate()
