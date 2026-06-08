"""Plugin loading and registry management.

Called from Rust (via bridge.rs) at session start with the list of
plugin module paths and their per-plugin config dicts.
"""

from __future__ import annotations

import functools
import itertools

__all__ = ["load_plugins", "PluginRegistry"]
import importlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from oxitest.plugin import Plugin

if TYPE_CHECKING:
    from oxitest._bridge._debugger import DebuggerBackend
    from oxitest.plugin import (
        Collector,
        ExecutionWrapper,
        FixtureProvider,
        LogBackend,
        Reporter,
    )


# Protocols that must be available before test execution starts.
EAGER_PROTOCOLS = frozenset(
    {
        "reporter",
        "collector",
        "async_backend",
        "coverage_provider",
    }
)

# Protocols that can be deferred until first use.
LAZY_PROTOCOLS = frozenset(
    {
        "log_backend",
        "fixture_provider",
        "execution_wrapper",
        "debugger_backend",
    }
)


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded or is invalid."""


@dataclass
class PluginEntry:
    """A loaded plugin with its metadata."""

    module_name: str
    plugin: Plugin | None = None
    is_loaded: bool = True
    declared_protocols: list[str] | None = None

    @classmethod
    def deferred(cls, module_name: str, declared_protocols: list[str]) -> PluginEntry:
        """Create an entry that is not yet imported."""
        return cls(
            module_name=module_name,
            plugin=None,
            is_loaded=False,
            declared_protocols=declared_protocols,
        )

    @staticmethod
    def needs_eager_import(declared_protocols: list[str] | None) -> bool:
        """Return True if this plugin must be imported at session start."""
        if not declared_protocols:
            return True
        return bool(set(declared_protocols) & EAGER_PROTOCOLS)

    def ensure_loaded(self) -> Plugin:
        """Import and initialise the plugin if it has not been loaded yet."""
        if self.is_loaded:
            assert self.plugin is not None, (
                f"plugin {self.module_name!r} marked loaded but plugin is None"
            )
            return self.plugin
        module = importlib.import_module(self.module_name)
        entry_fn = getattr(module, "oxitest_plugin")
        self.plugin = entry_fn()
        self.is_loaded = True
        assert self.plugin is not None, (
            f"oxitest_plugin() in {self.module_name!r} returned None"
        )
        return self.plugin


@dataclass
class PluginRegistry:
    """Holds all loaded plugin instances."""

    entries: list[PluginEntry] = field(default_factory=list)

    @functools.cached_property
    def log_backends(self) -> list[LogBackend]:
        """All log backends from all plugins."""
        return list(
            itertools.chain.from_iterable(
                e.plugin.log_backends for e in self.entries if e.plugin is not None
            )
        )

    @functools.cached_property
    def fixture_providers(self) -> list[FixtureProvider]:
        """All fixture providers from all plugins."""
        return list(
            itertools.chain.from_iterable(
                e.plugin.fixture_providers for e in self.entries if e.plugin is not None
            )
        )

    @functools.cached_property
    def execution_wrappers(self) -> list[ExecutionWrapper]:
        """All execution wrappers from all plugins."""
        return list(
            itertools.chain.from_iterable(
                e.plugin.execution_wrappers
                for e in self.entries
                if e.plugin is not None
            )
        )

    @functools.cached_property
    def collectors(self) -> list[Collector]:
        """All collectors from all plugins."""
        return list(
            itertools.chain.from_iterable(
                e.plugin.collectors for e in self.entries if e.plugin is not None
            )
        )

    @functools.cached_property
    def reporters(self) -> list[Reporter]:
        """All reporters from all plugins."""
        return list(
            itertools.chain.from_iterable(
                e.plugin.reporters for e in self.entries if e.plugin is not None
            )
        )

    @functools.cached_property
    def async_backends(self) -> list[tuple[str, Any]]:
        """All async backends from all plugins, as (module_name, backend) pairs."""
        return [
            (entry.module_name, entry.plugin.async_backend)
            for entry in self.entries
            if entry.plugin is not None and entry.plugin.async_backend is not None
        ]

    @functools.cached_property
    def debugger_backends(self) -> list[tuple[str, DebuggerBackend]]:
        """All debugger backends from all plugins, as (module_name, backend) pairs."""
        return [
            (entry.module_name, entry.plugin.debugger_backend)
            for entry in self.entries
            if entry.plugin is not None and entry.plugin.debugger_backend is not None
        ]

    @functools.cached_property
    def coverage_providers(self) -> list[tuple[str, object]]:
        """All coverage providers from all plugins, as (module_name, provider) pairs."""
        return [
            (entry.module_name, entry.plugin.coverage_provider)
            for entry in self.entries
            if entry.plugin is not None and entry.plugin.coverage_provider is not None
        ]

    def register_deferred(self, entry: PluginEntry) -> None:
        """Append a deferred (not yet imported) plugin entry."""
        self.entries.append(entry)

    def resolve_fixture_providers(self) -> list:
        """Return all fixture providers, loading deferred fixture_provider plugins."""
        providers = []
        for entry in self.entries:
            if (
                not entry.is_loaded
                and entry.declared_protocols
                and "fixture_provider" in entry.declared_protocols
            ):
                entry.ensure_loaded()
            if entry.is_loaded and entry.plugin:
                providers.extend(entry.plugin.fixture_providers)
        return providers

    def validate(self) -> None:
        """Check for conflicting plugin declarations.

        Raises:
            ConflictingDebuggerError: if multiple plugins provide a debugger backend.
            ConflictingCoverageError: if multiple plugins provide a coverage provider.
        """
        from oxitest._bridge._errors import (
            ConflictingCoverageError,
            ConflictingDebuggerError,
        )

        if len(self.debugger_backends) > 1:
            providers = [name for name, _ in self.debugger_backends]
            raise ConflictingDebuggerError(providers)

        if len(self.coverage_providers) > 1:
            providers = [name for name, _ in self.coverage_providers]
            raise ConflictingCoverageError(providers)


def load_plugins(
    plugin_modules: Sequence[str],
    plugin_configs: dict[str, dict[str, object]],
) -> PluginRegistry:
    """Load and validate all declared plugins.

    Args:
        plugin_modules: Ordered list of plugin module paths from
            `[tool.oxitest] plugins = [...]`
        plugin_configs: Per-plugin config dicts from
            `[tool.oxitest.plugin_settings.<name>]` sections.

    Returns:
        A PluginRegistry containing all loaded plugins.

    Raises:
        PluginLoadError: If any plugin cannot be loaded or is invalid.
    """
    registry = PluginRegistry()

    for module_name in plugin_modules:
        # Check if this plugin declares only lazy protocols and can be deferred.
        mod_settings = plugin_configs.get(module_name, {})
        _raw_protocols = mod_settings.get("protocols")
        declared_protocols: list[str] | None = (
            cast("list[str]", list(_raw_protocols))
            if isinstance(_raw_protocols, (list, tuple))
            else None
        )

        if declared_protocols and not PluginEntry.needs_eager_import(
            declared_protocols
        ):
            entry = PluginEntry.deferred(module_name, list(declared_protocols))
            registry.register_deferred(entry)
            continue

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

    registry.validate()
    return registry
