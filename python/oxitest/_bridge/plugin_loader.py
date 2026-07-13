"""Plugin loading and registry management.

Called from Rust (via bridge.rs) at session start with the list of
plugin module paths and their per-plugin config dicts.
"""

from __future__ import annotations

__all__ = ["PluginRegistry", "activate_deferred_plugins", "load_plugins"]

import dataclasses
import importlib
import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from oxitest._bridge._errors import (
    ConflictingCoverageError,
    ConflictingDebuggerError,
    OxitestError,
)
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


@dataclass(frozen=True, slots=True)
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

    def ensure_loaded(self) -> tuple[PluginEntry, Plugin]:
        """Import and initialise the plugin, returning (updated_entry, plugin)."""
        if self.plugin is not None:
            return self, self.plugin

        try:
            module = importlib.import_module(self.module_name)
        except ImportError as e:
            msg = f'plugin "{self.module_name}" not found. Is it installed?\n  {e}'
            raise PluginLoadError(msg) from e

        entry_fn = getattr(module, "oxitest_plugin", None)
        if entry_fn is None:
            msg = f'plugin "{self.module_name}" has no oxitest_plugin() function'
            raise PluginLoadError(msg)
        if not callable(entry_fn):
            msg = f'plugin "{self.module_name}" oxitest_plugin is not callable'
            raise PluginLoadError(msg)

        try:
            result = entry_fn()
        except Exception as e:
            msg = f'plugin "{self.module_name}" oxitest_plugin() raised: {e}'
            raise PluginLoadError(msg) from e

        if not isinstance(result, Plugin):
            msg = (
                f"oxitest_plugin() in {self.module_name!r} must return"
                f" oxitest.Plugin, got {type(result).__name__}"
            )
            raise PluginLoadError(msg)
        new_entry = dataclasses.replace(self, plugin=result)
        return new_entry, result


def _flatten_protocol(entries: Sequence[PluginEntry], attr: str) -> tuple[Any, ...]:
    """Flatten a list-valued protocol attribute across all loaded plugins."""
    return tuple(
        itertools.chain.from_iterable(
            getattr(e.plugin, attr) for e in entries if e.plugin is not None
        )
    )


# ── Frozen registry ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PluginRegistry:
    """Immutable registry of all loaded plugins.

    Constructed exclusively by ``_PluginRegistryBuilder.build()``.
    All fields are eagerly computed and frozen — no lazy caching needed.
    Valid by construction: the builder validates before returning.
    """

    entries: tuple[PluginEntry, ...] = ()
    cli_extensions: MappingProxyType[
        str, tuple[CliExtension, list[FieldDescriptor]]
    ] = field(default_factory=lambda: MappingProxyType({}))

    # Sequential protocol collections (iterated by consumers)
    log_backends: tuple[LogBackend, ...] = ()
    fixture_providers: tuple[FixtureProvider, ...] = ()
    helper_providers: tuple[HelperProvider, ...] = ()
    execution_wrappers: tuple[ExecutionWrapper, ...] = ()
    collectors: tuple[Collector, ...] = ()
    reporters: tuple[Reporter, ...] = ()

    # At-most-one (validated at build time)
    debugger_backend: DebuggerBackend | None = None
    coverage_provider: object | None = None


# ── Builder ──────────────────────────────────────────────────────────────────


class _PluginRegistryBuilder:
    """Mutable accumulator for constructing a frozen PluginRegistry.

    Private — never escapes this module.
    """

    def __init__(
        self,
        entries: Sequence[PluginEntry] = (),
        cli_extensions: (
            dict[str, tuple[CliExtension, list[FieldDescriptor]]] | None
        ) = None,
    ) -> None:
        self._entries: list[PluginEntry] = list(entries)
        self._cli_extensions: dict[str, tuple[CliExtension, list[FieldDescriptor]]] = (
            dict(cli_extensions) if cli_extensions is not None else {}
        )

    def add_entry(self, entry: PluginEntry) -> None:
        """Append a plugin entry."""
        self._entries.append(entry)

    def add_cli_extension(
        self,
        module_name: str,
        ext: CliExtension,
        descriptors: list[FieldDescriptor],
    ) -> None:
        """Register a CLI extension for a plugin module."""
        self._cli_extensions[module_name] = (ext, descriptors)

    def replace_entry(self, index: int, new_entry: PluginEntry) -> None:
        """Replace an entry at the given index."""
        self._entries[index] = new_entry

    def build(self) -> PluginRegistry:
        """Validate and construct a frozen PluginRegistry."""
        entries = tuple(self._entries)

        # Compute sequential protocol collections
        log_backends = _flatten_protocol(self._entries, "log_backends")
        fixture_providers = _flatten_protocol(self._entries, "fixture_providers")
        helper_providers = _flatten_protocol(self._entries, "helper_providers")
        execution_wrappers = _flatten_protocol(self._entries, "execution_wrappers")
        collectors = _flatten_protocol(self._entries, "collectors")
        reporters = _flatten_protocol(self._entries, "reporters")

        # Extract at-most-one singletons
        debugger_entries = [
            entry
            for entry in self._entries
            if entry.plugin is not None and entry.plugin.debugger_backend is not None
        ]
        coverage_entries = [
            entry
            for entry in self._entries
            if entry.plugin is not None and entry.plugin.coverage_provider is not None
        ]

        # Validate uniqueness
        if len(debugger_entries) > 1:
            providers = [e.module_name for e in debugger_entries]
            raise ConflictingDebuggerError(providers)
        if len(coverage_entries) > 1:
            providers = [e.module_name for e in coverage_entries]
            raise ConflictingCoverageError(providers)

        debugger_backend: DebuggerBackend | None = None
        if debugger_entries:
            plugin = debugger_entries[0].plugin
            if plugin is not None:
                debugger_backend = plugin.debugger_backend

        coverage_provider: object | None = None
        if coverage_entries:
            plugin = coverage_entries[0].plugin
            if plugin is not None:
                coverage_provider = plugin.coverage_provider

        return PluginRegistry(
            entries=entries,
            cli_extensions=MappingProxyType(self._cli_extensions),
            log_backends=log_backends,
            fixture_providers=fixture_providers,
            helper_providers=helper_providers,
            execution_wrappers=execution_wrappers,
            collectors=collectors,
            reporters=reporters,
            debugger_backend=debugger_backend,
            coverage_provider=coverage_provider,
        )


# ── Module-level functions ───────────────────────────────────────────────────


def _activate_plugin(
    module_name: str,
    cli_extensions: dict[str, tuple[CliExtension, list[FieldDescriptor]]]
    | MappingProxyType[str, tuple[CliExtension, list[FieldDescriptor]]],
    pyproject_values: dict[str, object],
    cli_values: dict[str, object],
) -> Plugin:
    """Activate a plugin with typed config (phase 2 of two-phase loading)."""
    mod = importlib.import_module(module_name)
    entry_fn = getattr(mod, "oxitest_plugin", None)
    if entry_fn is None:
        msg = f"plugin '{module_name}' has no oxitest_plugin() function"
        raise PluginLoadError(msg)

    if module_name in cli_extensions:
        ext, descriptors = cli_extensions[module_name]
        config = merge_config(
            ext.config_type, descriptors, pyproject_values, cli_values
        )
        return entry_fn(config=config)

    return entry_fn()


def _load_single_plugin(
    module_name: str,
    plugin_configs: dict[str, dict[str, object]],
    builder: _PluginRegistryBuilder,
) -> None:
    """Load and register a single plugin module into the builder.

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
        builder.add_entry(entry)
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
        builder.add_cli_extension(module_name, overridden_ext, descriptors)
        # Phase 1 complete — defer activation to activate_deferred_plugins()
        builder.add_entry(PluginEntry(module_name=module_name))
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

    builder.add_entry(PluginEntry(module_name=module_name, plugin=result))


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
        A frozen PluginRegistry containing all loaded plugins.

    Raises:
        PluginLoadError: If any plugin cannot be loaded or is invalid.

    """
    builder = _PluginRegistryBuilder()
    for module_name in plugin_modules:
        _load_single_plugin(module_name, plugin_configs, builder)
    return builder.build()


def activate_deferred_plugins(
    registry: PluginRegistry,
    plugin_settings_json: str,
    cli_values_json: str,
) -> PluginRegistry:
    """Activate plugins with CLI extensions that were deferred during load.

    Called from Rust after ``init_session()`` so that typed configs
    (merged from pyproject + CLI + env) reach the plugin entry point.

    Also activates any remaining deferred plugins (e.g., fixture_provider
    plugins) so all plugins are loaded before test execution begins.

    Args:
        registry: The current frozen registry.
        plugin_settings_json: JSON dict of per-module pyproject settings.
        cli_values_json: JSON dict of per-module CLI-provided values.

    Returns:
        A new frozen PluginRegistry with all deferred plugins activated.

    """
    try:
        plugin_settings: dict[str, dict[str, object]] = json.loads(plugin_settings_json)
    except json.JSONDecodeError as exc:
        msg = f"invalid plugin settings JSON from Rust bridge: {exc}"
        raise OxitestError(msg) from exc

    try:
        cli_values: dict[str, dict[str, object]] = json.loads(cli_values_json)
    except json.JSONDecodeError as exc:
        msg = f"invalid CLI values JSON from Rust bridge: {exc}"
        raise OxitestError(msg) from exc

    builder = _PluginRegistryBuilder(
        entries=registry.entries,
        cli_extensions=dict(registry.cli_extensions),
    )

    for i, entry in enumerate(registry.entries):
        if entry.is_loaded:
            continue

        # Activate CLI-extension plugins with typed config
        if entry.module_name in registry.cli_extensions:
            pyproject_values = plugin_settings.get(entry.module_name, {})
            plugin = _activate_plugin(
                entry.module_name,
                cli_extensions=registry.cli_extensions,
                pyproject_values=pyproject_values,
                cli_values=cli_values.get(entry.module_name, {}),
            )
            builder.replace_entry(i, dataclasses.replace(entry, plugin=plugin))
        else:
            # Activate remaining deferred plugins (e.g., fixture_provider)
            loaded, _ = entry.ensure_loaded()
            builder.replace_entry(i, loaded)

    return builder.build()
