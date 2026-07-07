from __future__ import annotations

import oxitest
from oxitest._bridge._async_backend import (
    AsyncioBackend,
    resolve_backend,
)
from oxitest._bridge._errors import BackendNotFoundError, ConflictingBackendError
from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry
from oxitest.plugin import Plugin


class _FakeBackend:
    @property
    def name(self) -> str:
        return "fake"

    def run(self, coro):
        raise NotImplementedError

    def create_shared_session(self):
        raise NotImplementedError


class _AsyncioNamedBackend:
    """A plugin backend that collides with the built-in name."""

    @property
    def name(self) -> str:
        return "asyncio"

    def run(self, coro):
        raise NotImplementedError

    def create_shared_session(self):
        raise NotImplementedError


def _registry_with(*entries: tuple[str, Plugin]) -> PluginRegistry:
    reg = PluginRegistry()
    for module_name, plugin in entries:
        reg.entries.append(PluginEntry(module_name=module_name, plugin=plugin))
    return reg


def test_default_asyncio_with_empty_registry() -> None:
    backend = resolve_backend("asyncio", PluginRegistry())
    assert isinstance(backend, AsyncioBackend), (
        f"expected AsyncioBackend, got {type(backend).__name__}"
    )


def test_plugin_backend_resolves_by_name() -> None:
    fake = _FakeBackend()
    reg = _registry_with(("my_plugin", Plugin(async_backend=fake)))
    backend = resolve_backend("fake", reg)
    assert backend is fake, f"expected fake backend, got {backend!r}"


def test_backend_not_found_error() -> None:
    with oxitest.raises(BackendNotFoundError, match="trio"):
        resolve_backend("trio", PluginRegistry())


def test_conflicting_backend_error() -> None:
    fake1 = _FakeBackend()
    fake2 = _FakeBackend()
    reg = _registry_with(
        ("plugin_a", Plugin(async_backend=fake1)),
        ("plugin_b", Plugin(async_backend=fake2)),
    )
    with oxitest.raises(ConflictingBackendError, match="plugin_a"):
        resolve_backend("fake", reg)


def test_plugin_asyncio_name_conflicts_with_builtin() -> None:
    collider = _AsyncioNamedBackend()
    reg = _registry_with(("bad_plugin", Plugin(async_backend=collider)))
    with oxitest.raises(ConflictingBackendError, match="bad_plugin"):
        resolve_backend("asyncio", reg)
