"""Bridge functions returning structured query data for fixtures and plugins."""

from __future__ import annotations

__all__ = ["fixture_entries", "plugin_entries"]

from typing import Any

# Protocol fields in display order, matching Plugin dataclass field names.
_PROTOCOL_FIELDS = (
    ("log_backends", "LogBackend"),
    ("fixture_providers", "FixtureProvider"),
    ("execution_wrappers", "ExecutionWrapper"),
    ("collectors", "Collector"),
    ("reporters", "Reporter"),
    ("async_backend", "AsyncBackend"),
    ("debugger_backend", "DebuggerBackend"),
)


def fixture_entries(registry: Any) -> list[dict[str, str]]:
    """Return all fixture defs as dicts for the Rust query engine."""
    from oxitest._bridge._fixture_registry import (
        BuiltinSource,
        ConftestSource,
        FixtureScope,
        PluginSource,
    )

    entries = []
    for defn in registry.all():
        match defn.source:
            case ConftestSource(conftest_path=p):
                source = p
            case PluginSource(plugin_module=m):
                source = f"<plugin:{m}>"
            case BuiltinSource():
                source = "<builtin>"
            case _:
                source = "<unknown>"

        doc = ""
        if isinstance(defn.source, ConftestSource):
            doc = (defn.source.func.__doc__ or "").strip()
        elif isinstance(defn.source, BuiltinSource):
            doc = (defn.source.impl_cls.__doc__ or "").strip()

        entries.append(
            {
                "name": defn.name,
                "source": source,
                "shared": str(defn.scope == FixtureScope.SHARED).lower(),
                "autouse": str(defn.autouse).lower(),
                "async": str(defn.is_async).lower(),
                "description": doc,
                "scope": defn.scope.value,
                "type": getattr(defn.fixture_type, "__name__", "None"),
            }
        )
    return entries


def plugin_entries(plugin_registry: Any) -> list[dict[str, str]]:
    """Return plugin info as list of dicts for Rust query engine."""
    entries = []
    for entry in plugin_registry.entries:
        protocols = _protocols_for(entry.plugin)
        entries.append(
            {
                "name": entry.module_name,
                "protocol": ",".join(protocols) if protocols else "",
            }
        )
    return entries


def _protocols_for(plugin: object) -> list[str]:
    """Inspect a Plugin object for implemented protocols."""
    protocols = []
    for attr, label in _PROTOCOL_FIELDS:
        val = getattr(plugin, attr, None)
        if val:
            protocols.append(label)
    return protocols
