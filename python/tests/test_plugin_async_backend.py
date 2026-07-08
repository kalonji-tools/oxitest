"""Tests for Plugin.async_backend field and PluginRegistry.async_backends property."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Never

from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
from oxitest.plugin import Plugin


class _FakeBackend:
    """Minimal async backend stub that raises NotImplementedError on use."""

    @property
    def name(self) -> str:
        return "fake"

    def run(self, coro: Coroutine[Any, Any, Any]) -> Never:
        raise NotImplementedError

    def create_shared_session(self) -> Never:
        raise NotImplementedError


def test_plugin_accepts_async_backend() -> None:
    """Plugin should store and expose the provided async_backend instance."""
    fake = _FakeBackend()
    p = Plugin(async_backend=fake)
    assert p.async_backend is fake, f"expected fake, got {p.async_backend!r}"


def test_plugin_async_backend_default_none() -> None:
    """Plugin.async_backend should default to None when not provided."""
    p = Plugin()
    assert p.async_backend is None, f"expected None, got {p.async_backend!r}"


def test_registry_async_backends_property() -> None:
    """PluginRegistry.async_backends should list only plugins that supply a backend."""
    fake = _FakeBackend()
    reg = PluginRegistry()
    reg.entries.append(
        PluginEntry(module_name="with_backend", plugin=Plugin(async_backend=fake))
    )
    reg.entries.append(PluginEntry(module_name="no_backend", plugin=Plugin()))
    backends = reg.async_backends
    assert len(backends) == 1, f"expected 1 backend, got {len(backends)}"
    module_name, backend = backends[0]
    assert module_name == "with_backend", (
        f"expected 'with_backend', got {module_name!r}"
    )
    assert backend is fake, f"expected fake backend, got {backend!r}"
