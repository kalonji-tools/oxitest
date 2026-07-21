"""Tests for resolve_backend — async backend selection and conflict detection."""

from __future__ import annotations

from typing import Never

import oxitest
from oxitest._bridge._async_backend import (
    AsyncioBackend,
    resolve_backend,
)
from oxitest._bridge._errors import BackendNotFoundError, ConflictingBackendError
from oxitest._bridge.plugin_loader import (
    ActivatedPluginEntry,
    PluginRegistry,
    _PluginRegistryBuilder,
)
from oxitest.plugin import Plugin


class _FakeBackend:
    """Minimal backend stub that raises NotImplementedError on use."""

    supports_nested_acquire = False

    @property
    def name(self) -> str:
        return "fake"

    def acquire_session(self) -> Never:
        raise NotImplementedError


class _AsyncioNamedBackend:
    """A plugin backend that collides with the built-in name."""

    supports_nested_acquire = False

    @property
    def name(self) -> str:
        return "asyncio"

    def acquire_session(self) -> Never:
        raise NotImplementedError


def _registry_with(*entries: tuple[str, Plugin]) -> PluginRegistry:
    builder = _PluginRegistryBuilder()
    for module_name, plugin in entries:
        builder.add_entry(ActivatedPluginEntry(module_name=module_name, plugin=plugin))
    return builder.build()


def test_default_asyncio_with_empty_registry() -> None:
    """resolve_backend('asyncio') with no plugins returns AsyncioBackend."""
    backend = resolve_backend("asyncio", PluginRegistry())
    assert isinstance(backend, AsyncioBackend), (
        f"expected AsyncioBackend, got {type(backend).__name__}"
    )


def test_plugin_backend_resolves_by_name() -> None:
    """resolve_backend should return the plugin backend matching the requested name."""
    fake = _FakeBackend()
    reg = _registry_with(("my_plugin", Plugin(async_backend=fake)))
    backend = resolve_backend("fake", reg)
    assert backend is fake, f"expected fake backend, got {backend!r}"


def test_backend_not_found_error() -> None:
    """resolve_backend should raise BackendNotFoundError when the name is unknown."""
    with oxitest.raises(BackendNotFoundError, match="trio"):
        resolve_backend("trio", PluginRegistry())


def test_conflicting_backend_error() -> None:
    """resolve_backend raises ConflictingBackendError when two plugins share a name."""
    fake1 = _FakeBackend()
    fake2 = _FakeBackend()
    reg = _registry_with(
        ("plugin_a", Plugin(async_backend=fake1)),
        ("plugin_b", Plugin(async_backend=fake2)),
    )
    with oxitest.raises(ConflictingBackendError, match="plugin_a"):
        resolve_backend("fake", reg)


def test_plugin_asyncio_name_conflicts_with_builtin() -> None:
    """A plugin backend named 'asyncio' should conflict with the built-in and raise."""
    collider = _AsyncioNamedBackend()
    reg = _registry_with(("bad_plugin", Plugin(async_backend=collider)))
    with oxitest.raises(ConflictingBackendError, match="bad_plugin"):
        resolve_backend("asyncio", reg)
