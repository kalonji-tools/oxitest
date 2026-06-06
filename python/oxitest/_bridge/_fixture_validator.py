"""Fixture validation and unused detection — extracted from FixtureSession."""

from __future__ import annotations

__all__ = ["FixtureValidator"]

import inspect
from typing import Any

from oxitest._bridge._fixture_registry import (
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge.plugin_loader import PluginRegistry


class FixtureValidator:
    """Validates and queries fixture registrations. Stateless, no side effects."""

    def __init__(
        self,
        registry: FixtureRegistry,
        plugin_registry: PluginRegistry,
        module_cache: ModuleCache,
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry
        self._module_cache = module_cache

    def validate_fixture_names(
        self,
        items: list[dict[str, Any]],
        plugin_registry: PluginRegistry | None = None,
    ) -> list[tuple[str, str]]:
        """Return ``(node_id, fixture_name)`` pairs that cannot resolve.

        Called by the Rust ``FixtureValidationPhase`` after collection to catch
        typos and missing fixtures before any test executes.

        ``plugin_registry`` overrides the one stored at construction time. This
        allows FixtureSession to pass its current ``_plugin_registry`` even if
        Rust replaced it via ``setattr`` after the validator was created.
        """
        effective_registry = (
            plugin_registry if plugin_registry is not None else self._plugin_registry
        )
        # Build set of types that plugin fixture providers can inject.
        plugin_types: set[type] = set()
        for provider in effective_registry.fixture_providers:
            plugin_types.add(provider.fixture_type)

        errors: list[tuple[str, str]] = []
        for item in items:
            node_id: str = item["node_id"]
            fixref = set(item.get("fixref_names", ()))
            for name in item["fixture_names"]:
                if name in fixref:
                    continue
                if self._registry.get(name) is not None:
                    continue
                # Check if the name resolves to a plugin-provided fixture by
                # looking up the cached module and examining the type hint.
                if plugin_types and self._can_plugin_resolve(
                    node_id, name, plugin_types
                ):
                    continue
                errors.append((node_id, name))
        return errors

    def find_unused_fixtures(
        self,
        items: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Return (conftest_path, fixture_name) pairs for unused fixtures.

        A fixture is unused if:
        - It is not autouse
        - It is not referenced by any collected test's fixture_names
        - It is not a dependency of any referenced fixture (transitive)
        """
        # 1. Collect all directly-referenced fixture names from items
        referenced: set[str] = set()
        for item in items:
            referenced.update(item.get("fixture_names", ()))

        # 2. Expand transitively -- walk fixture function signatures
        def _expand_deps(name: str, visited: set[str]) -> None:
            if name in visited:
                return
            visited.add(name)
            defn = self._registry.get(name)
            if defn is None:
                return
            try:
                sig = inspect.signature(defn.func)
            except (ValueError, TypeError):
                return
            for param_name in sig.parameters:
                if param_name in self._registry._defs:
                    _expand_deps(param_name, visited)

        all_used: set[str] = set()
        for name in referenced:
            _expand_deps(name, all_used)

        # 3. Also expand autouse fixtures and their deps
        for defn in self._registry.get_autouse():
            _expand_deps(defn.name, all_used)

        # 4. Find unused (skip builtins, autouse, and non-conftest fixtures)
        unused: list[tuple[str, str]] = []
        for name, defs in self._registry._defs.items():
            if not defs:
                continue
            defn = defs[-1]  # most-local
            if defn.autouse:
                continue
            # Skip builtins (conftest_path starts with "<")
            if defn.conftest_path.startswith("<"):
                continue
            # Only flag fixtures from conftest files; module-level Fixtures()
            # instances may be used solely via FixtureRef in parametrize.
            if not defn.conftest_path.endswith("conftest.py"):
                continue
            if name in all_used:
                continue
            unused.append((defn.conftest_path, name))
        return sorted(unused)

    def _can_plugin_resolve(
        self,
        node_id: str,
        param_name: str,
        plugin_types: set[type],
    ) -> bool:
        """Check if a parameter resolves to a plugin-provided fixture type."""
        # node_id format: "path/to/test.py::test_fn" or "path/to/test.py::test_fn[case]"
        parts = node_id.split("::", 1)
        if len(parts) < 2:
            return False
        module_path = parts[0]
        fn_part = parts[1].split("[", 1)[0]  # strip param_id
        # Look up the cached module (already loaded during collection)
        mod = self._module_cache.get(module_path)
        if mod is None:
            return False
        # Handle class::method syntax
        if "::" in fn_part:
            cls_name, method_name = fn_part.split("::", 1)
            cls = getattr(mod, cls_name, None)
            fn = getattr(cls, method_name, None) if cls else None
        else:
            fn = getattr(mod, fn_part, None)
        if fn is None:
            return False
        try:
            from typing import get_type_hints

            hints = get_type_hints(fn, include_extras=True)
        except Exception:  # noqa: BLE001
            return False
        hint = hints.get(param_name)
        if hint is None:
            return False
        is_fx, inner = _fixture_inner_type(hint)
        return is_fx and inner in plugin_types
