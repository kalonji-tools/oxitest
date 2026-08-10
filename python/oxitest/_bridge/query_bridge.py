"""Bridge functions returning structured query data for fixtures and plugins."""

from __future__ import annotations

__all__ = ["fixture_entries", "plugin_entries", "test_fixture_deps"]

from typing import Any

from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureScope,
    ModuleSource,
    PluginModuleSource,
    PluginSource,
)
from oxitest._bridge._plugin_entry import ActivatedPluginEntry
from oxitest._bridge.importer import collect_module
from oxitest._oxitest import trace as _rust_trace

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
    entries = []
    for defn in registry.all():
        match defn.source:
            case ConftestSource(conftest_path=p):
                source = p
            # A declaration reports the file it was written in, the same way a
            # conftest fixture does. Without this arm it fell to `case _` and
            # reported "<unknown>" — the source column's whole job is to say
            # where to go and edit it (#1720).
            case ModuleSource(defining_module_path=p):
                source = p
            case PluginSource(plugin_module=m) | PluginModuleSource(plugin_module=m):
                source = f"<plugin:{m}>"
            case BuiltinSource():
                source = "<builtin>"
            case _:
                source = "<unknown>"

        doc = ""
        if isinstance(defn.source, (ConftestSource, ModuleSource, PluginModuleSource)):
            doc = (defn.source.func.__doc__ or "").strip()
        elif isinstance(defn.source, BuiltinSource):
            doc = (defn.source.impl_cls.__doc__ or "").strip()

        deps = ",".join(q for q, _ in defn.depends_on) if defn.depends_on else ""

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
                "uses": deps,
            }
        )
    return entries


def plugin_entries(plugin_registry: Any) -> list[dict[str, str]]:
    """Return plugin info as list of dicts for Rust query engine."""
    entries = []
    for entry in plugin_registry.entries:
        protocols = (
            _protocols_for(entry.plugin)
            if isinstance(entry, ActivatedPluginEntry)
            else ()
        )
        entries.append(
            {
                "name": entry.module_name,
                "protocol": ",".join(protocols) if protocols else "",
            }
        )
    return entries


def test_fixture_deps(
    test_files: list[str],
    session: Any,
) -> list[dict[str, str]]:
    """Return test->fixture associations for the inspect graph.

    Each entry has 'test_node_id' and 'fixture_names' (comma-separated).
    """
    entries: list[dict[str, str]] = []
    for path in test_files:
        try:
            items, _ = collect_module(path, session)
        except Exception as exc:  # noqa: BLE001 — skip unimportable files during query
            _rust_trace("debug", __name__, f"Skipping {path}: {exc}")
            continue
        for item in items:
            if not item.fixture_deps:
                continue
            # Build node_id matching phase-1 format: "file::fn_name"
            node_id = f"{path}::{item.fn_name}"
            if item.param_id:
                node_id = f"{node_id}[{item.param_id}]"
            fixture_names = ",".join(q for q, _ in item.fixture_deps)
            entries.append(
                {
                    "test_node_id": node_id,
                    "fixture_names": fixture_names,
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
