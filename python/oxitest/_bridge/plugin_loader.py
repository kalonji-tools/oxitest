"""Plugin loading and registry management.

Called from Rust (via bridge.rs) at session start with the list of
plugin module paths and their per-plugin config dicts.
"""

from __future__ import annotations

__all__ = ["PluginRegistry", "load_plugins"]

import functools
import importlib
import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from oxitest._bridge._errors import ConflictingCoverageError, ConflictingDebuggerError
from oxitest._bridge._plugin_config import (
    CliExtension,
    IntrospectionError,
    introspect_config,
    merge_config,
)
from oxitest.plugin import Plugin

if TYPE_CHECKING:
    from oxitest._bridge._debugger import DebuggerBackend
    from oxitest._bridge._plugin_config import FieldDescriptor
    from oxitest.plugin import (
        Collector,
        ExecutionWrapper,
        FixtureProvider,
        HelperProvider,
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
        "helper_provider",
        "execution_wrapper",
        "debugger_backend",
    }
)


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded or is invalid."""


def _coerce_str_list(raw: object) -> list[str]:
    """Convert a list/tuple of protocol names to list[str].

    Returns an empty list if raw is not a list or tuple.
    """
    if isinstance(raw, (list, tuple)):
        return [str(p) for p in raw]
    return []


@dataclass
class PluginEntry:
    """A loaded plugin with its metadata."""

    module_name: str
    plugin: Plugin | None = None
    declared_protocols: list[str] | None = None

    @property
    def is_loaded(self) -> bool:
        """A plugin is loaded when its Plugin instance is available."""
        return self.plugin is not None

    @classmethod
    def deferred(cls, module_name: str, declared_protocols: list[str]) -> PluginEntry:
        """Create an entry that is not yet imported."""
        return cls(
            module_name=module_name,
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
        if self.plugin is not None:
            return self.plugin
        module = importlib.import_module(self.module_name)
        entry_fn = getattr(module, "oxitest_plugin")  # noqa: B009 — dynamic module attr
        result = entry_fn()
        if not isinstance(result, Plugin):
            msg = (
                f"oxitest_plugin() in {self.module_name!r} must return"
                f" oxitest.Plugin, got {type(result).__name__}"
            )
            raise PluginLoadError(msg)
        self.plugin = result
        return result


def _flatten_protocol(entries: list[PluginEntry], attr: str) -> tuple:
    """Flatten a list-valued protocol attribute across all loaded plugins."""
    return tuple(
        itertools.chain.from_iterable(
            getattr(e.plugin, attr) for e in entries if e.plugin is not None
        )
    )


@dataclass
class PluginRegistry:
    """Holds all loaded plugin instances.

    Registry pattern: dataclass with ``@functools.cached_property`` for
    lazy protocol flattening. Appropriate when entries are added in two
    phases (deferred registration, then activation) and computed views
    must be invalidated on mutation. ``_CACHED_PROPERTIES`` enumerates
    all cached views; ``_invalidate_caches()`` clears them. Compare with
    ``BuiltinFixture._registry`` (auto-registration), ``FixtureRegistry``
    (instance-based), and ``_MARK_REGISTRY`` (module-level dict).
    """

    _CACHED_PROPERTIES: ClassVar[tuple[str, ...]] = (
        "log_backends",
        "fixture_providers",
        "helper_providers",
        "execution_wrappers",
        "collectors",
        "reporters",
        "async_backends",
        "debugger_backends",
        "coverage_providers",
    )

    _entries: list[PluginEntry] = field(default_factory=list)
    _cli_extensions: dict[str, tuple[CliExtension, list[FieldDescriptor]]] = field(
        default_factory=dict
    )

    @property
    def entries(self) -> tuple[PluginEntry, ...]:
        """All registered plugin entries (immutable view)."""
        return tuple(self._entries)

    @property
    def cli_extensions(
        self,
    ) -> MappingProxyType[str, tuple[CliExtension, list[FieldDescriptor]]]:
        """All CLI extensions (immutable view)."""
        return MappingProxyType(self._cli_extensions)

    @functools.cached_property
    def log_backends(self) -> tuple[LogBackend, ...]:
        """All log backends from all plugins."""
        return _flatten_protocol(self._entries, "log_backends")

    @functools.cached_property
    def fixture_providers(self) -> tuple[FixtureProvider, ...]:
        """All fixture providers from all plugins."""
        return _flatten_protocol(self._entries, "fixture_providers")

    @functools.cached_property
    def helper_providers(self) -> tuple[HelperProvider, ...]:
        """All helper providers from all plugins."""
        return _flatten_protocol(self._entries, "helper_providers")

    @functools.cached_property
    def execution_wrappers(self) -> tuple[ExecutionWrapper, ...]:
        """All execution wrappers from all plugins."""
        return _flatten_protocol(self._entries, "execution_wrappers")

    @functools.cached_property
    def collectors(self) -> tuple[Collector, ...]:
        """All collectors from all plugins."""
        return _flatten_protocol(self._entries, "collectors")

    @functools.cached_property
    def reporters(self) -> tuple[Reporter, ...]:
        """All reporters from all plugins."""
        return _flatten_protocol(self._entries, "reporters")

    @functools.cached_property
    def async_backends(self) -> tuple[tuple[str, Any], ...]:
        """All async backends from all plugins, as (module_name, backend) pairs."""
        return tuple(
            (entry.module_name, entry.plugin.async_backend)
            for entry in self._entries
            if entry.plugin is not None and entry.plugin.async_backend is not None
        )

    @functools.cached_property
    def debugger_backends(self) -> tuple[tuple[str, DebuggerBackend], ...]:
        """All debugger backends from all plugins, as (module_name, backend) pairs."""
        return tuple(
            (entry.module_name, entry.plugin.debugger_backend)
            for entry in self._entries
            if entry.plugin is not None and entry.plugin.debugger_backend is not None
        )

    @functools.cached_property
    def coverage_providers(self) -> tuple[tuple[str, object], ...]:
        """All coverage providers from all plugins, as (module_name, provider) pairs."""
        return tuple(
            (entry.module_name, entry.plugin.coverage_provider)
            for entry in self._entries
            if entry.plugin is not None and entry.plugin.coverage_provider is not None
        )

    def _invalidate_caches(self) -> None:
        """Clear all cached protocol properties after plugin activation."""
        for attr in self._CACHED_PROPERTIES:
            self.__dict__.pop(attr, None)

    def register_deferred(self, entry: PluginEntry) -> None:
        """Append a deferred (not yet imported) plugin entry."""
        self._entries.append(entry)

    def activate_plugin(
        self,
        module_name: str,
        pyproject_values: dict[str, object],
        cli_values: dict[str, object],
    ) -> Plugin:
        """Activate a plugin with typed config (phase 2 of two-phase loading)."""
        mod = importlib.import_module(module_name)
        entry_fn = getattr(mod, "oxitest_plugin", None)
        if entry_fn is None:
            msg = f"plugin '{module_name}' has no oxitest_plugin() function"
            raise PluginLoadError(msg)

        if module_name in self._cli_extensions:
            ext, descriptors = self._cli_extensions[module_name]
            config = merge_config(
                ext.config_type, descriptors, pyproject_values, cli_values
            )
            return entry_fn(config=config)

        return entry_fn()

    def activate_deferred_plugins(
        self,
        plugin_settings_json: str,
        cli_values_json: str,
    ) -> None:
        """Activate plugins with CLI extensions that were deferred during load.

        Called from Rust after ``init_session()`` so that typed configs
        (merged from pyproject + CLI + env) reach the plugin entry point.

        Args:
            plugin_settings_json: JSON dict of per-module pyproject settings.
            cli_values_json: JSON dict of per-module CLI-provided values.

        """
        plugin_settings: dict[str, dict[str, object]] = json.loads(plugin_settings_json)
        cli_values: dict[str, dict[str, object]] = json.loads(cli_values_json)

        for entry in self._entries:
            if entry.is_loaded:
                continue
            if entry.module_name not in self._cli_extensions:
                continue

            pyproject_values = plugin_settings.get(entry.module_name, {})
            plugin = self.activate_plugin(
                entry.module_name,
                pyproject_values=pyproject_values,
                cli_values=cli_values.get(entry.module_name, {}),
            )
            entry.plugin = plugin

        # Invalidate cached protocol properties so they pick up new plugins.
        self._invalidate_caches()

    def resolve_fixture_providers(self) -> tuple[FixtureProvider, ...]:
        """Return all fixture providers, loading deferred fixture_provider plugins."""
        providers: list[FixtureProvider] = []
        for entry in self._entries:
            if (
                not entry.is_loaded
                and entry.declared_protocols
                and "fixture_provider" in entry.declared_protocols
            ):
                entry.ensure_loaded()
            if entry.plugin:
                providers.extend(entry.plugin.fixture_providers)
        return tuple(providers)

    def validate(self) -> None:
        """Check for conflicting plugin declarations.

        Raises:
            ConflictingDebuggerError: if multiple plugins provide a debugger backend.
            ConflictingCoverageError: if multiple plugins provide a coverage provider.

        """
        if len(self.debugger_backends) > 1:
            providers = [name for name, _ in self.debugger_backends]
            raise ConflictingDebuggerError(providers)

        if len(self.coverage_providers) > 1:
            providers = [name for name, _ in self.coverage_providers]
            raise ConflictingCoverageError(providers)


def _load_single_plugin(
    module_name: str,
    plugin_configs: dict[str, dict[str, object]],
    registry: PluginRegistry,
) -> None:
    """Load and register a single plugin module into the registry.

    Raises:
        PluginLoadError: If the plugin cannot be loaded or is invalid.

    """
    # Check if this plugin declares only lazy protocols and can be deferred.
    mod_settings = plugin_configs.get(module_name, {})
    _raw_protocols = mod_settings.get("protocols")
    declared_protocols: list[str] | None = (
        _coerce_str_list(_raw_protocols) if _raw_protocols is not None else None
    )

    if declared_protocols and not PluginEntry.needs_eager_import(declared_protocols):
        entry = PluginEntry.deferred(module_name, list(declared_protocols))
        registry.register_deferred(entry)
        return

    # Import the module
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        msg = f'plugin "{module_name}" not found. Is it installed?\n  {e}'
        raise PluginLoadError(msg) from e

    # Find the entry point function
    entry_fn = getattr(module, "oxitest_plugin", None)
    if entry_fn is None:
        msg = f'plugin "{module_name}" has no oxitest_plugin() function'
        raise PluginLoadError(msg)
    if not callable(entry_fn):
        msg = f'plugin "{module_name}" oxitest_plugin is not callable'
        raise PluginLoadError(msg)

    # Discover CLI extension if present
    cli_ext = getattr(module, "oxitest_cli_extension", None)
    if cli_ext is not None:
        if not isinstance(cli_ext, CliExtension):
            msg = (
                f'plugin "{module_name}" oxitest_cli_extension must be '
                f"CliExtension, got {type(cli_ext).__name__}"
            )
            raise PluginLoadError(msg)
        settings = plugin_configs.get(module_name, {})
        prefix: str = (
            str(settings.get("cli_prefix", cli_ext.prefix))
            if isinstance(settings, dict)
            else cli_ext.prefix
        )
        try:
            descriptors = introspect_config(cli_ext.config_type)
        except IntrospectionError as e:
            msg = f'plugin "{module_name}" config dataclass error: {e}'
            raise PluginLoadError(msg) from e
        overridden_ext = CliExtension(prefix=prefix, config_type=cli_ext.config_type)
        registry._cli_extensions[module_name] = (overridden_ext, descriptors)  # noqa: SLF001
        # Phase 1 complete — defer activation to activate_plugin()
        registry._entries.append(PluginEntry(module_name=module_name))  # noqa: SLF001
        return

    # Call the entry point with config
    config = plugin_configs.get(module_name)
    try:
        result = entry_fn(config=config)
    except Exception as e:
        msg = f'plugin "{module_name}" oxitest_plugin() raised: {e}'
        raise PluginLoadError(msg) from e

    # Validate return type
    if not isinstance(result, Plugin):
        msg = (
            f'plugin "{module_name}" oxitest_plugin() must return '
            f"oxitest.Plugin, got {type(result).__name__}"
        )
        raise PluginLoadError(msg)

    registry._entries.append(PluginEntry(module_name=module_name, plugin=result))  # noqa: SLF001


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
        _load_single_plugin(module_name, plugin_configs, registry)
    registry.validate()
    return registry
