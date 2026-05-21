from __future__ import annotations

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


def test_default_asyncio_with_empty_registry():
    backend = resolve_backend("asyncio", PluginRegistry())
    assert isinstance(backend, AsyncioBackend), (
        f"expected AsyncioBackend, got {type(backend).__name__}"
    )


def test_plugin_backend_resolves_by_name():
    fake = _FakeBackend()
    reg = _registry_with(("my_plugin", Plugin(async_backend=fake)))
    backend = resolve_backend("fake", reg)
    assert backend is fake, f"expected fake backend, got {backend!r}"


def test_backend_not_found_error():
    try:
        resolve_backend("trio", PluginRegistry())
        assert False, "expected BackendNotFoundError"  # noqa: PT015
    except BackendNotFoundError as exc:
        assert "trio" in str(exc), f"expected 'trio' in error, got {exc!r}"


def test_conflicting_backend_error():
    fake1 = _FakeBackend()
    fake2 = _FakeBackend()
    reg = _registry_with(
        ("plugin_a", Plugin(async_backend=fake1)),
        ("plugin_b", Plugin(async_backend=fake2)),
    )
    try:
        resolve_backend("fake", reg)
        assert False, "expected ConflictingBackendError"  # noqa: PT015
    except ConflictingBackendError as exc:
        msg = str(exc)
        assert "plugin_a" in msg, f"expected 'plugin_a' in error, got {msg!r}"
        assert "plugin_b" in msg, f"expected 'plugin_b' in error, got {msg!r}"


def test_plugin_asyncio_name_conflicts_with_builtin():
    collider = _AsyncioNamedBackend()
    reg = _registry_with(("bad_plugin", Plugin(async_backend=collider)))
    try:
        resolve_backend("asyncio", reg)
        assert False, "expected ConflictingBackendError"  # noqa: PT015
    except ConflictingBackendError as exc:
        assert "bad_plugin" in str(exc), f"expected 'bad_plugin' in error, got {exc!r}"
