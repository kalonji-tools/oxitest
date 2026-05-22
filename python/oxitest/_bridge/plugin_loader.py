"""Plugin loading and registry management.

Called from Rust (via bridge.rs) at session start with the list of
plugin module paths and their per-plugin config dicts.
"""

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from oxitest.plugin import Plugin

if TYPE_CHECKING:
    from oxitest.plugin import (
        Collector,
        ExecutionWrapper,
        FixtureProvider,
        LogBackend,
        Reporter,
    )


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded or is invalid."""


@dataclass
class PluginEntry:
    """A loaded plugin with its metadata."""

    module_name: str
    plugin: Plugin


@dataclass
class PluginRegistry:
    """Holds all loaded plugin instances."""

    entries: list[PluginEntry] = field(default_factory=list)

    @functools.cached_property
    def log_backends(self) -> list[LogBackend]:
        """All log backends from all plugins."""
        backends = []
        for entry in self.entries:
            backends.extend(entry.plugin.log_backends)
        return backends

    @functools.cached_property
    def fixture_providers(self) -> list[FixtureProvider]:
        """All fixture providers from all plugins."""
        providers = []
        for entry in self.entries:
            providers.extend(entry.plugin.fixture_providers)
        return providers

    @functools.cached_property
    def execution_wrappers(self) -> list[ExecutionWrapper]:
        """All execution wrappers from all plugins."""
        wrappers = []
        for entry in self.entries:
            wrappers.extend(entry.plugin.execution_wrappers)
        return wrappers

    @functools.cached_property
    def collectors(self) -> list[Collector]:
        """All collectors from all plugins."""
        collectors = []
        for entry in self.entries:
            collectors.extend(entry.plugin.collectors)
        return collectors

    @functools.cached_property
    def reporters(self) -> list[Reporter]:
        """All reporters from all plugins."""
        reporters = []
        for entry in self.entries:
            reporters.extend(entry.plugin.reporters)
        return reporters

    @functools.cached_property
    def async_backends(self) -> list[tuple[str, Any]]:
        """All async backends from all plugins, as (module_name, backend) pairs."""
        return [
            (entry.module_name, entry.plugin.async_backend)
            for entry in self.entries
            if entry.plugin.async_backend is not None
        ]


_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Get the active plugin registry. Returns empty registry if not initialized."""
    return _registry or PluginRegistry()


def init_plugins(plugin_modules: list[str], settings_json: str) -> None:
    """Called from Rust to initialize the plugin system.

    Args:
        plugin_modules: List of plugin module paths.
        settings_json: JSON string of per-plugin config dicts.
    """
    import json

    global _registry  # noqa: PLW0603
    plugin_configs = json.loads(settings_json) if settings_json else {}
    _registry = load_plugins(plugin_modules, plugin_configs)


def load_plugins(
    plugin_modules: list[str],
    plugin_configs: dict[str, dict[str, object]],
) -> PluginRegistry:
    """Load and validate all declared plugins.

    Args:
        plugin_modules: Ordered list of plugin module paths from
            ``[tool.oxitest] plugins = [...]``
        plugin_configs: Per-plugin config dicts from
            ``[tool.oxitest.plugin_settings.<name>]`` sections.

    Returns:
        A PluginRegistry containing all loaded plugins.

    Raises:
        PluginLoadError: If any plugin cannot be loaded or is invalid.
    """
    registry = PluginRegistry()

    for module_name in plugin_modules:
        # Import the module
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise PluginLoadError(
                f'plugin "{module_name}" not found. Is it installed?\n  {e}'
            ) from e

        # Find the entry point function
        entry_fn = getattr(module, "oxitest_plugin", None)
        if entry_fn is None:
            raise PluginLoadError(
                f'plugin "{module_name}" has no oxitest_plugin() function'
            )
        if not callable(entry_fn):
            raise PluginLoadError(
                f'plugin "{module_name}" oxitest_plugin is not callable'
            )

        # Call the entry point with config
        config = plugin_configs.get(module_name)
        try:
            result = entry_fn(config=config)
        except Exception as e:
            raise PluginLoadError(
                f'plugin "{module_name}" oxitest_plugin() raised: {e}'
            ) from e

        # Validate return type
        if not isinstance(result, Plugin):
            raise PluginLoadError(
                f'plugin "{module_name}" oxitest_plugin() must return '
                f"oxitest.Plugin, got {type(result).__name__}"
            )

        registry.entries.append(PluginEntry(module_name=module_name, plugin=result))

    return registry
