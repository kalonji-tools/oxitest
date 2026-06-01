"""Unit tests: plugin_lister formatting."""

from __future__ import annotations

from dataclasses import dataclass

import oxitest
from oxitest._bridge.plugin_lister import list_plugins_from_registry
from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
from oxitest.plugin import Plugin


@dataclass
class _FakeLogBackend:
    name: str = "fake"

    def install(self) -> None: ...  # noqa: D102

    def uninstall(self) -> None: ...  # noqa: D102

    @property
    def records(self) -> list[object]:  # noqa: D102
        return []


@dataclass
class _FakeReporter:
    name: str = "fake"

    def test_started(self, item: object) -> None: ...  # noqa: D102

    def test_completed(  # noqa: D102
        self, item: object, outcome: object, duration_ms: float
    ) -> None: ...

    def finish(  # noqa: D102
        self, collect_errors: list[object], interrupted: bool
    ) -> None: ...


@dataclass
class _FakeAsyncBackend:
    @property
    def name(self) -> str:  # noqa: D102
        return "fake"

    def run(self, coro: object) -> object:  # noqa: D102
        return None  # type: ignore[return-value]

    def create_shared_session(self) -> object:  # noqa: D102
        return None


@dataclass(frozen=True)
class VerbosityCase:
    verbosity: int


def _make_registry(*entries: PluginEntry) -> PluginRegistry:
    return PluginRegistry(entries=list(entries))


def test_no_plugins_configured():
    """Empty registry shows 'no plugins configured'."""
    registry = _make_registry()
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert result == "no plugins configured", f"got: {result!r}"


def test_single_plugin_one_protocol():
    """Single plugin with one protocol shows module name and protocol."""
    entry = PluginEntry(
        module_name="my_plugin",
        plugin=Plugin(log_backends=[_FakeLogBackend()]),
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert "my_plugin" in result, f"expected module name in output, got: {result!r}"
    assert "LogBackend" in result, f"expected LogBackend in output, got: {result!r}"


def test_single_plugin_multiple_protocols():
    """Plugin implementing multiple protocols shows all of them."""
    entry = PluginEntry(
        module_name="multi_plugin",
        plugin=Plugin(
            log_backends=[_FakeLogBackend()],
            reporters=[_FakeReporter()],
        ),
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert "multi_plugin" in result, f"expected module name in output, got: {result!r}"
    assert "LogBackend" in result, f"expected LogBackend in output, got: {result!r}"
    assert "Reporter" in result, f"expected Reporter in output, got: {result!r}"


def test_multiple_plugins():
    """Multiple plugins are each listed."""
    entry1 = PluginEntry(
        module_name="plugin_a",
        plugin=Plugin(log_backends=[_FakeLogBackend()]),
    )
    entry2 = PluginEntry(
        module_name="plugin_b",
        plugin=Plugin(reporters=[_FakeReporter()]),
    )
    registry = _make_registry(entry1, entry2)
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert "plugin_a" in result, f"expected plugin_a in output, got: {result!r}"
    assert "plugin_b" in result, f"expected plugin_b in output, got: {result!r}"


@oxitest.parametrize(
    standard=VerbosityCase(verbosity=1),
    rich=VerbosityCase(verbosity=2),
)
def test_verbose_shows_plugin_count(case: VerbosityCase):
    """Verbose output includes plugin count summary."""
    entry = PluginEntry(
        module_name="my_plugin",
        plugin=Plugin(log_backends=[_FakeLogBackend()]),
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(
        registry, verbosity=case.verbosity, use_color=False
    )
    assert "1 plugin" in result, f"expected count summary in output, got: {result!r}"


def test_singleton_protocol_async_backend():
    """Plugin providing async_backend (singleton, not list) shows AsyncBackend."""
    entry = PluginEntry(
        module_name="my_async",
        plugin=Plugin(async_backend=_FakeAsyncBackend()),  # ty: ignore[invalid-argument-type]
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert "my_async" in result, f"expected module name in output, got: {result!r}"
    assert "AsyncBackend" in result, f"expected AsyncBackend in output, got: {result!r}"


def test_plugin_with_no_protocols():
    """Plugin with all defaults shows '(no protocols)'."""
    entry = PluginEntry(
        module_name="empty_plugin",
        plugin=Plugin(),
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(registry, verbosity=0, use_color=False)
    assert "empty_plugin" in result, f"expected module name in output, got: {result!r}"
    assert "(no protocols)" in result, (
        f"expected '(no protocols)' in output, got: {result!r}"
    )


def test_no_color_strips_ansi():
    """use_color=False output has no ANSI escape sequences."""
    entry = PluginEntry(
        module_name="my_plugin",
        plugin=Plugin(log_backends=[_FakeLogBackend()]),
    )
    registry = _make_registry(entry)
    result = list_plugins_from_registry(registry, verbosity=1, use_color=False)
    assert "\033[" not in result, f"expected no ANSI codes in output, got: {result!r}"
