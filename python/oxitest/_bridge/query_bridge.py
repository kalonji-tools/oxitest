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


_BUILTIN_FIXTURES = (
    ("TempDir", "Unique temp directory, deleted after test"),
    ("TempDirFactory", "Session-scoped factory for multiple temp dirs"),
    ("StdCapture", "Capture sys.stdout / sys.stderr"),
    ("FdCapture", "Capture at fd level (C extensions, subprocesses)"),
    ("Patcher", "Temporary attribute / env / chdir overrides"),
    ("TestContext", "Test metadata and teardown stack"),
    ("LogCapture", "Capture logging records"),
    ("WarnCapture", "Capture warnings.warn() calls"),
)


def fixture_entries(registry: Any) -> list[dict[str, str]]:
    """Return fixture defs as list of dicts for Rust query engine."""
    entries = []

    # Built-in type-based fixtures
    for name, desc in _BUILTIN_FIXTURES:
        entries.append(
            {
                "name": name,
                "source": "<builtin>",
                "shared": "false",
                "autouse": "false",
                "async": "false",
                "description": desc,
            }
        )

    # User-defined conftest fixtures
    for defs in registry._defs.values():
        entries.extend(
            {
                "name": fd.name,
                "source": fd.conftest_path,
                "shared": str(fd.shared).lower(),
                "autouse": str(fd.autouse).lower(),
                "async": str(fd.is_async).lower(),
            }
            for fd in defs
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
