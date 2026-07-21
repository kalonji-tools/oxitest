"""Tests for Plugin.async_backend field and PluginRegistry.async_backends property."""

from __future__ import annotations

from typing import Never

import oxitest as oxi
from oxitest._bridge._async_backend import (
    _NULL_ASYNC_BACKEND,
    AsyncBackend,
    resolve_backend,
)
from oxitest._bridge._coverage import _NULL_COVERAGE
from oxitest._bridge._debugger import _NULL_DEBUGGER
from oxitest._bridge._errors import BackendNotFoundError
from oxitest._bridge.plugin_loader import (
    ActivatedPluginEntry,
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


def test_plugin_async_backend_default_is_null_singleton() -> None:
    """Plugin.async_backend defaults to the null-object singleton (ADR-0007 Rule 6)."""
    p = Plugin()
    assert p.async_backend is _NULL_ASYNC_BACKEND, (
        f"expected _NULL_ASYNC_BACKEND, got {p.async_backend!r}"
    )


def test_plugin_debugger_backend_default_is_null_singleton() -> None:
    """Plugin.debugger_backend defaults to the null-object singleton (ADR-0007 Rule 6).

    Hard break: defaults are no longer None; callers that relied on None must migrate.
    """
    p = Plugin()
    assert p.debugger_backend is _NULL_DEBUGGER, (
        f"expected _NULL_DEBUGGER, got {p.debugger_backend!r}"
    )


def test_plugin_coverage_provider_default_is_null_singleton() -> None:
    """Plugin.coverage_provider defaults to the null-object singleton (ADR-0007 Rule 6).

    Hard break: defaults are no longer None; callers that relied on None must migrate.
    """
    p = Plugin()
    assert p.coverage_provider is _NULL_COVERAGE, (
        f"expected _NULL_COVERAGE, got {p.coverage_provider!r}"
    )


def test_registry_entries_contain_async_backend() -> None:
    """Async backends are accessible through plugin entries on the registry."""
    fake = _FakeBackend()
    builder = _PluginRegistryBuilder()
    builder.add_entry(
        ActivatedPluginEntry(
            module_name="with_backend", plugin=Plugin(async_backend=fake)
        )
    )
    # Plugin() with null async_backend default — "loaded but no backend provided"
    builder.add_entry(ActivatedPluginEntry(module_name="no_backend", plugin=Plugin()))
    reg = builder.build()
    backends = [
        e.plugin.async_backend
        for e in reg.entries
        if isinstance(e, ActivatedPluginEntry)
        and e.plugin.async_backend is not _NULL_ASYNC_BACKEND
    ]
    assert len(backends) == 1, f"expected 1 backend, got {len(backends)}"
    assert backends[0] is fake, f"expected fake backend, got {backends[0]!r}"


def test_null_async_backend_is_structural_backend() -> None:
    """The null singleton must satisfy the runtime_checkable AsyncBackend protocol."""
    assert isinstance(_NULL_ASYNC_BACKEND, AsyncBackend), (
        "null-object must structurally conform to AsyncBackend for discovery to work"
    )


def test_null_async_backend_name_is_sentinel() -> None:
    """Null backend `.name` returns 'null' so registry iteration never crashes."""
    assert _NULL_ASYNC_BACKEND.name == "null", (
        f"expected 'null', got {_NULL_ASYNC_BACKEND.name!r}"
    )


def test_null_async_backend_acquire_session_raises() -> None:
    """Null backend raises AssertionError — bypassing discovery filter is a bug."""
    with oxi.raises(AssertionError, match="discovery filter is broken"):
        _NULL_ASYNC_BACKEND.acquire_session()


def test_resolve_backend_null_name_is_never_matched() -> None:
    """A null-backend plugin must never match a 'null' config name query."""
    builder = _PluginRegistryBuilder()
    builder.add_entry(ActivatedPluginEntry(module_name="only_null", plugin=Plugin()))
    reg = builder.build()

    with oxi.raises(BackendNotFoundError):
        resolve_backend("null", reg)
