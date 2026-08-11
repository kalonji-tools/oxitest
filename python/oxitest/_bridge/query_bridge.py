"""Bridge functions returning structured query data for fixtures and plugins."""

from __future__ import annotations

__all__ = [
    "autouse_entries",
    "fixture_entries",
    "plugin_entries",
    "test_fixture_deps",
]

from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Final

from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    FrameworkSource,
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


#: Declaration-home basenames, per ADR-0009 Rule 5. Anything else a
#: ``ModuleSource`` can name is an inline declaration, because only a collected
#: test module can host one — so this does not need ``python_files``.
_DECLARATION_HOMES: Final = MappingProxyType(
    {"__fixtures__.py": "fixtures-file", "__init__.py": "package-init"}
)


def _declaration_facts(source: Any) -> tuple[str, str, str]:
    """Return ``(anchor, home, lifetime)`` for one fixture source.

    An empty *home* means the fixture is ambient — a builtin, a framework
    fixture, or a plugin's. ``CONTEXT.md``: "Plugin, framework, and builtin
    fixtures have no anchor." The Rust graph reads that emptiness as "do not
    build a declaration node for this fixture" (#1722).

    ``PluginModuleSource`` carries a lifetime but is ambient by construction,
    so it reports a lifetime and no home.
    """
    match source:
        case ModuleSource(
            defining_module_path=path, anchor_package_path=anchor, lifetime=lifetime
        ):
            # An empty path reports no home rather than "inline": a home is what
            # makes the Rust graph build a declaration node, and one built from
            # an empty path would render a nameless row.
            name = PurePath(path).name if path else ""
            home = _DECLARATION_HOMES.get(name, "inline") if name else ""
            return anchor, home, lifetime.value
        case PluginModuleSource(lifetime=lifetime):
            return "", "", lifetime.value
        case _:
            return "", "", ""


def fixture_entries(registry: Any) -> list[dict[str, str]]:
    """Return all fixture defs as dicts for the Rust query engine."""
    entries = []
    for defn in registry.all():
        match defn.source:
            case FrameworkSource(origin=p):
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
        if isinstance(defn.source, (FrameworkSource, ModuleSource, PluginModuleSource)):
            doc = (defn.source.func.__doc__ or "").strip()
        elif isinstance(defn.source, BuiltinSource):
            doc = (defn.source.impl_cls.__doc__ or "").strip()

        deps = ",".join(q for q, _ in defn.depends_on) if defn.depends_on else ""
        anchor, home, lifetime = _declaration_facts(defn.source)

        entries.append(
            {
                "name": defn.name,
                "source": source,
                "autouse": str(defn.autouse).lower(),
                "async": str(defn.is_async).lower(),
                "description": doc,
                "scope": defn.scope.value,
                "lifetime": lifetime,
                "anchor": anchor,
                "home": home,
                "type": getattr(defn.fixture_type, "__name__", "None"),
                "uses": deps,
            }
        )
    return entries


def autouse_entries(module_paths: list[str], session: Any) -> list[dict[str, str]]:
    """Return the autouse fixtures that apply to each module, in firing order.

    Keyed on the module rather than on the test because
    :meth:`FixtureRegistry.get_autouse` is: every test in one module has the
    same autouse set.

    This reports which fixtures **apply**, never which test builds one. ADR-0009
    Rule 7 makes the counts a rate rather than a boundary event — the build
    happens inside the first test to reach the boundary, and a boundary whose
    tests are all skipped or deselected never fires at all — so which test pays
    depends on execution order, worker assignment and deselection. ``inspect``
    runs no tests and cannot know it.

    ``get_autouse`` is what makes this correct rather than a filter on the
    declared ``autouse`` flag: it resolves each name through ``_deepest_visible``
    and yields the winner only when the winner is itself autouse, which is what
    implements Rule 7's opt-out (declare the same name without ``autouse`` at a
    deeper anchor). Yield order is widest lifetime first, so it is preserved
    here rather than re-sorted (#1722).
    """
    entries: list[dict[str, str]] = []
    for path in module_paths:
        names: list[str] = []
        lifetimes: list[str] = []
        for defn in session.registry.get_autouse(path):
            names.append(defn.name)
            # Always the Lifetime, never the Scope. Only a `ModuleSource` or a
            # `PluginModuleSource` can carry `autouse` — those are the two
            # registration sites in `_module_source_registrar` — and both carry
            # a Lifetime, so the fallback below is unreachable rather than
            # merely unlikely. It exists so a new autouse-bearing source cannot
            # silently render an empty column.
            _, _, lifetime = _declaration_facts(defn.source)
            lifetimes.append(lifetime or defn.scope.value)
        entries.append(
            {
                "module_path": path,
                "fixture_names": ",".join(names),
                "lifetimes": ",".join(lifetimes),
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
