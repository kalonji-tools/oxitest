"""Tests for Plugin.async_backend field and PluginRegistry.async_backends property."""

from __future__ import annotations

from typing import Never

from oxitest._bridge.plugin_loader import (
    PluginEntry,
    _PluginRegistryBuilder,
)
from oxitest.plugin import Plugin


class _FakeBackend:
    """Minimal async backend stub that raises NotImplementedError on use."""

    supports_nested_acquire = False

    @property
    def name(self) -> str:
        return "fake"

    def acquire_session(self) -> Never:
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


def test_registry_entries_contain_async_backend() -> None:
    """Async backends are accessible through plugin entries on the registry."""
    fake = _FakeBackend()
    builder = _PluginRegistryBuilder()
    builder.add_entry(
        PluginEntry(module_name="with_backend", plugin=Plugin(async_backend=fake))
    )
    builder.add_entry(PluginEntry(module_name="no_backend", plugin=Plugin()))
    reg = builder.build()
    backends = [
        e.plugin.async_backend
        for e in reg.entries
        if e.plugin is not None and e.plugin.async_backend is not None
    ]
    assert len(backends) == 1, f"expected 1 backend, got {len(backends)}"
    assert backends[0] is fake, f"expected fake backend, got {backends[0]!r}"
