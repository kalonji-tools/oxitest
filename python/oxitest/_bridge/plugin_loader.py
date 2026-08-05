"""Plugin loading and registry management.

Called from Rust (via bridge.rs) at session start with the list of
plugin module paths and their per-plugin config dicts.
"""

from __future__ import annotations

__all__ = [
    "ActivatedPluginEntry",
    "DeferredPluginEntry",
    "PluginEntry",
    "PluginFixtureHome",
    "PluginRegistry",
    "activate_deferred_plugins",
    "activate_entry",
    "load_plugins",
    "needs_eager_import",
    "plugin_fixture_homes",
]

import importlib
import itertools
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Any

from oxitest._bridge._coverage import _NULL_COVERAGE
from oxitest._bridge._debugger import (
    _NULL_DEBUGGER,
    DebuggerBackend,
)
from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import (
    ConflictingCoverageError,
    ConflictingDebuggerError,
    OxitestError,
    UsageError,
)
from oxitest._bridge._namespace_validation import validate_namespace_name
from oxitest._bridge._plugin_config import (
    CliExtension,
    IntrospectionError,
    introspect_config,
    merge_config,
)
from oxitest._bridge._plugin_entry import (
    ActivatedPluginEntry,
    DeferredPluginEntry,
    PluginEntry,
)
from oxitest._bridge.result import DiagnosticSeverity
from oxitest.plugin import CoverageProvider, Plugin

if TYPE_CHECKING:
    from oxitest._bridge._plugin_config import FieldDescriptor
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


def _coerce_str_list(raw: object) -> list[str]:
    """Convert a list/tuple of protocol names to list[str].

    Returns an empty list if raw is not a list or tuple.
    """
    if isinstance(raw, (list, tuple)):
        return [str(p) for p in raw]
    return []


def needs_eager_import(declared_protocols: tuple[str, ...]) -> bool:
    """Return ``True`` if this plugin must be imported at session start."""
    if not declared_protocols:
        return True
    return bool(set(declared_protocols) & EAGER_PROTOCOLS)


def activate_entry(entry: DeferredPluginEntry) -> ActivatedPluginEntry:
    """Import and initialise a deferred entry (no CLI-ext config path)."""
    try:
        module = importlib.import_module(entry.module_name)
    except ImportError as e:
        msg = f'plugin "{entry.module_name}" not found. Is it installed?\n  {e}'
        raise PluginLoadError(msg) from e

    entry_fn = getattr(module, "oxitest_plugin", None)
    if entry_fn is None:
        msg = f'plugin "{entry.module_name}" has no oxitest_plugin() function'
        raise PluginLoadError(msg)
    if not callable(entry_fn):
        msg = f'plugin "{entry.module_name}" oxitest_plugin is not callable'
        raise PluginLoadError(msg)

    try:
        result = entry_fn()
    except Exception as e:
        msg = f'plugin "{entry.module_name}" oxitest_plugin() raised: {e}'
        raise PluginLoadError(msg) from e

    if not isinstance(result, Plugin):
        msg = (
            f"oxitest_plugin() in {entry.module_name!r} must return"
            f" oxitest.Plugin, got {type(result).__name__}"
        )
        raise PluginLoadError(msg)
    return ActivatedPluginEntry(
        module_name=entry.module_name,
        plugin=result,
        declared_protocols=entry.declared_protocols,
    )


def _flatten_protocol(entries: Sequence[PluginEntry], attr: str) -> tuple[Any, ...]:
    """Flatten a list-valued protocol attribute across all activated plugins."""
    return tuple(
        itertools.chain.from_iterable(
            getattr(e.plugin, attr)
            for e in entries
            if isinstance(e, ActivatedPluginEntry)
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
    execution_wrappers: tuple[ExecutionWrapper, ...] = ()
    collectors: tuple[Collector, ...] = ()
    reporters: tuple[Reporter, ...] = ()

    # At-most-one (validated at build time; null-object defaults per ADR-0007 Rule 6)
    debugger_backend: DebuggerBackend = _NULL_DEBUGGER
    coverage_provider: CoverageProvider = _NULL_COVERAGE


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
        execution_wrappers = _flatten_protocol(self._entries, "execution_wrappers")
        collectors = _flatten_protocol(self._entries, "collectors")
        reporters = _flatten_protocol(self._entries, "reporters")

        # Extract at-most-one singletons
        debugger_entries = [
            entry
            for entry in self._entries
            if isinstance(entry, ActivatedPluginEntry)
            and entry.plugin.debugger_backend is not _NULL_DEBUGGER
        ]
        coverage_entries = [
            entry
            for entry in self._entries
            if isinstance(entry, ActivatedPluginEntry)
            and entry.plugin.coverage_provider is not _NULL_COVERAGE
        ]

        # Validate uniqueness
        if len(debugger_entries) > 1:
            providers = [e.module_name for e in debugger_entries]
            raise ConflictingDebuggerError(providers)
        if len(coverage_entries) > 1:
            providers = [e.module_name for e in coverage_entries]
            raise ConflictingCoverageError(providers)

        debugger_backend: DebuggerBackend = _NULL_DEBUGGER
        if debugger_entries:
            debugger_backend = debugger_entries[0].plugin.debugger_backend

        coverage_provider: CoverageProvider = _NULL_COVERAGE
        if coverage_entries:
            coverage_provider = coverage_entries[0].plugin.coverage_provider

        return PluginRegistry(
            entries=entries,
            cli_extensions=MappingProxyType(self._cli_extensions),
            log_backends=log_backends,
            fixture_providers=fixture_providers,
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
    declared_protocols: tuple[str, ...] = (
        tuple(_coerce_str_list(_raw_protocols)) if _raw_protocols is not None else ()
    )

    if declared_protocols and not needs_eager_import(declared_protocols):
        entry = DeferredPluginEntry(
            module_name=module_name,
            declared_protocols=declared_protocols,
        )
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
        _warn_on_reserved_config_fields(module_name, descriptors)
        overridden_ext = CliExtension(prefix=prefix, config_type=cli_ext.config_type)
        builder.add_cli_extension(module_name, overridden_ext, descriptors)
        # Phase 1 complete — defer activation to activate_deferred_plugins()
        builder.add_entry(DeferredPluginEntry(module_name=module_name))
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

    builder.add_entry(ActivatedPluginEntry(module_name=module_name, plugin=result))


@dataclass(frozen=True, slots=True)
class PluginFixtureHome:
    """An activated plugin package that declares fixtures (#1717).

    ``anchor_dir`` is canonical-absolute. That is load-bearing rather than
    tidy: ``_visibility`` requires anchors and module paths to arrive in the
    same form, and the collection walk compares this against paths
    ``collector.rs`` has already canonicalised, to avoid registering one
    ``__fixtures__.py`` twice when a plugin is vendored under ``testpaths``.
    """

    plugin_module: str
    anchor_dir: str
    namespace: str
    autouse: tuple[str, ...]


#: ``fx.oxi`` is resolved before any namespace lookup (``proxy_ns``), so a
#: plugin claiming it would be addressable by nothing.
_RESERVED_NAMESPACE = "oxi"

#: Keys oxitest reads out of ``[tool.oxitest.plugin_settings.<module>]`` for
#: itself. A plugin config field of the same name is read by both, from the
#: same table — which is not an error, but is worth saying out loud.
#: ``protocols`` has had this collision available since it shipped.
_RESERVED_CONFIG_KEYS = frozenset({"autouse", "namespace", "protocols"})


def _warn_on_reserved_config_fields(
    module_name: str, descriptors: Sequence[FieldDescriptor]
) -> None:
    """Notice when a plugin's own config field shadows a framework key."""
    for reserved in sorted(
        _RESERVED_CONFIG_KEYS.intersection(
            descriptor.name for descriptor in descriptors
        )
    ):
        emit_diagnostic(
            DiagnosticSeverity.NOTICE,
            "plugin config",
            f"plugin '{module_name}' declares a config field named "
            f"'{reserved}', which oxitest also reads as a framework key from "
            f"the same table — both see the same value.",
        )


def plugin_fixture_homes(
    *,
    activated_modules: Sequence[str],
    plugin_settings: dict[str, dict[str, object]],
    modules: Mapping[str, ModuleType] | None = None,
) -> tuple[PluginFixtureHome, ...]:
    """Resolve each activated plugin's ``__fixtures__.py`` home (#1717).

    Called once after ``activate_deferred_plugins``, when every plugin module
    is imported — verified, not assumed: a plugin listed in ``plugins`` is in
    ``sys.modules`` by the time tests execute.

    Args:
        activated_modules: Module paths from ``[tool.oxitest] plugins``.
        plugin_settings: Per-plugin tables from
            ``[tool.oxitest.plugin_settings.<module>]``.
        modules: Module table to resolve against. Defaults to ``sys.modules``;
            injected by tests so they need not mutate global import state.

    Returns:
        One entry per activated plugin package that has a ``__fixtures__.py``.

    Raises:
        UsageError: a namespace is reserved or claimed by two plugins.
        ValueError: a namespace is a Python keyword or builtin.
    """
    module_table = sys.modules if modules is None else modules
    homes: list[PluginFixtureHome] = []
    claimed: dict[str, str] = {}

    for module_name in activated_modules:
        settings = plugin_settings.get(module_name, {})
        module = module_table.get(module_name)
        if module is None:
            continue

        if not hasattr(module, "__path__"):
            # Only a package has a directory that can hold __fixtures__.py.
            # Silence would leave the user believing their fixtures are live.
            if "namespace" in settings or "autouse" in settings:
                emit_diagnostic(
                    DiagnosticSeverity.NOTICE,
                    "plugin fixtures",
                    f"plugin '{module_name}' sets namespace/autouse but is a "
                    f"single module, not a package. Only a package can hold a "
                    f"__fixtures__.py, so those keys do nothing.",
                )
            continue

        namespace = str(settings.get("namespace") or module_name)
        if namespace == _RESERVED_NAMESPACE:
            msg = (
                f"plugin '{module_name}' claims the reserved namespace "
                f"'{_RESERVED_NAMESPACE}'.\n"
                f"fx.{_RESERVED_NAMESPACE} is oxitest's own built-in namespace "
                f"and resolves before any plugin, so these fixtures would be "
                f"unreachable.\n"
                f"Hint: set a different namespace under "
                f"[tool.oxitest.plugin_settings.{module_name}]."
            )
            raise UsageError(msg)
        validate_namespace_name(namespace, module_name)

        if namespace in claimed:
            msg = (
                f"plugins '{claimed[namespace]}' and '{module_name}' both "
                f"claim namespace '{namespace}'.\n"
                f"fx.{namespace} would silently merge two distributions, and "
                f"which fixture wins would depend on activation order.\n"
                f'Hint: set namespace = "..." under '
                f"[tool.oxitest.plugin_settings.{module_name}]."
            )
            raise UsageError(msg)
        claimed[namespace] = module_name

        anchor = Path(str(module.__file__)).resolve().parent
        if not (anchor / "__fixtures__.py").is_file():
            continue

        homes.append(
            PluginFixtureHome(
                plugin_module=module_name,
                anchor_dir=str(anchor),
                namespace=namespace,
                autouse=tuple(_coerce_str_list(settings.get("autouse"))),
            )
        )

    return tuple(homes)


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
        if isinstance(entry, ActivatedPluginEntry):
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
            builder.replace_entry(
                i,
                ActivatedPluginEntry(
                    module_name=entry.module_name,
                    plugin=plugin,
                    declared_protocols=entry.declared_protocols,
                ),
            )
        else:
            # Activate remaining deferred plugins (e.g., fixture_provider)
            builder.replace_entry(i, activate_entry(entry))

    return builder.build()
