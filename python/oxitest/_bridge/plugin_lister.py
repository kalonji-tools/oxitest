"""Format registered plugins for the `oxitest plugins` subcommand."""

from __future__ import annotations

__all__ = ["list_plugins_from_registry"]

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge.plugin_loader import PluginEntry, PluginRegistry

# Protocol names in display order, matching Plugin dataclass field names.
_PROTOCOL_FIELDS = (
    ("log_backends", "LogBackend"),
    ("fixture_providers", "FixtureProvider"),
    ("execution_wrappers", "ExecutionWrapper"),
    ("collectors", "Collector"),
    ("reporters", "Reporter"),
    ("async_backend", "AsyncBackend"),
    ("debugger_backend", "DebuggerBackend"),
)


def _dim(text: str, use_color: bool) -> str:
    if use_color:
        return f"\033[2m{text}\033[0m"
    return text


def _bold(text: str, use_color: bool) -> str:
    if use_color:
        return f"\033[1m{text}\033[0m"
    return text


def _protocols_for_entry(entry: PluginEntry) -> list[str]:
    """Return the list of protocol names this plugin provides."""
    protocols: list[str] = []
    plugin = entry.plugin
    for field_name, display_name in _PROTOCOL_FIELDS:
        value = getattr(plugin, field_name)
        if (
            isinstance(value, list)
            and value
            or value is not None
            and not isinstance(value, list)
        ):
            protocols.append(display_name)
    return protocols


def list_plugins_from_registry(
    registry: PluginRegistry,
    verbosity: int = 1,
    use_color: bool = True,
) -> str:
    """Format all registered plugins as a display string.

    Args:
        registry: The plugin registry to list.
        verbosity: 0=quiet, 1=standard, 2=rich.
        use_color: Whether to emit ANSI color codes.

    Returns:
        Formatted string ready for printing.
    """
    entries = registry.entries
    if not entries:
        return "no plugins configured"

    lines: list[str] = []

    # Compute column width for alignment
    max_name = max(len(e.module_name) for e in entries)
    col_width = max_name + 2  # 2 spaces padding

    for entry in entries:
        protocols = _protocols_for_entry(entry)
        proto_str = (
            ", ".join(protocols) if protocols else _dim("(no protocols)", use_color)
        )
        name = _bold(entry.module_name, use_color)
        # For alignment, pad based on raw name length (not ANSI-decorated)
        padding = " " * (col_width - len(entry.module_name))
        lines.append(f"  {name}{padding}{proto_str}")

    # Summary line
    count = len(entries)
    s = "" if count == 1 else "s"
    lines.append("")
    lines.append(_dim(f"  {count} plugin{s}", use_color))

    return "\n".join(lines)
