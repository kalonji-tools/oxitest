"""Fixture validation and unused detection — extracted from FixtureSession."""

from __future__ import annotations

__all__ = ["FixtureValidator"]

from typing import Any, get_type_hints

from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_registry import (
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge.plugin_loader import PluginRegistry


def _parse_node_id(node_id: str) -> tuple[str, str] | None:
    """Parse a node ID into ``(module_path, fn_part)`` stripping any param id.

    Returns ``None`` if the node ID has no ``::`` separator.
    """
    parts = node_id.split("::", 1)
    if len(parts) < 2:
        return None
    module_path = parts[0]
    fn_part = parts[1].split("[", 1)[0]  # strip param_id
    return module_path, fn_part


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

        # Precompute builtin type names once for the entire validation pass
        builtin_type_names = {t.__name__ for t in BuiltinFixture._registry}

        errors: list[tuple[str, str]] = []
        for item in items:
            node_id: str = item["node_id"]
            fixref = set(item.get("fixref_names", ()))
            # Build set of qualifiers that resolve to builtin fixtures,
            # using type_name from fixture_deps to skip them during validation
            builtin_qualifiers: set[str] = set()
            for qualifier, type_name in item.get("fixture_deps", ()):
                if type_name in builtin_type_names:
                    builtin_qualifiers.add(qualifier)
            for name in item["fixture_names"]:
                if name in fixref:
                    continue
                if name in builtin_qualifiers:
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

        # 2. Expand transitively via depends_on (populated at registration)
        def _expand_deps(name: str, visited: set[str]) -> None:
            if name in visited:
                return
            visited.add(name)
            defn = self._registry.get(name)
            if defn is None:
                return
            for qualifier, _binding_type in defn.depends_on:
                if qualifier in self._registry:
                    _expand_deps(qualifier, visited)

        all_used: set[str] = set()
        for name in referenced:
            _expand_deps(name, all_used)

        # 3. Also expand autouse fixtures and their deps
        for defn in self._registry.get_autouse():
            _expand_deps(defn.name, all_used)

        # 4. Find unused (skip builtins, autouse, and non-conftest fixtures)
        unused: list[tuple[str, str]] = []
        for name in self._registry:
            defs = self._registry.all_defs(name)
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

    def _lookup_test_function(self, module_path: str, fn_part: str) -> Any | None:
        """Resolve a test function from module cache, handling class::method syntax."""
        mod = self._module_cache.get(module_path)
        if mod is None:
            return None
        # Handle class::method syntax
        if "::" in fn_part:
            cls_name, method_name = fn_part.split("::", 1)
            cls = getattr(mod, cls_name, None)
            return getattr(cls, method_name, None) if cls else None
        return getattr(mod, fn_part, None)

    @staticmethod
    def _type_matches_plugin(
        fn: Any,
        param_name: str,
        plugin_types: set[type],
    ) -> bool:
        """Check if *param_name*'s hint on *fn* is a plugin Fixture type."""
        try:
            hints = get_type_hints(fn, include_extras=True)
        except Exception:
            return False
        hint = hints.get(param_name)
        if hint is None:
            return False
        is_fx, inner = _fixture_inner_type(hint)
        return is_fx and inner in plugin_types

    def _can_plugin_resolve(
        self,
        node_id: str,
        param_name: str,
        plugin_types: set[type],
    ) -> bool:
        """Check if a parameter resolves to a plugin-provided fixture type."""
        parsed = _parse_node_id(node_id)
        if parsed is None:
            return False
        module_path, fn_part = parsed
        fn = self._lookup_test_function(module_path, fn_part)
        if fn is None:
            return False
        return self._type_matches_plugin(fn, param_name, plugin_types)
