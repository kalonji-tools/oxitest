from __future__ import annotations

__all__ = [
    "FixtureDef",
    "FixtureRegistry",
    "_fixture_inner_type",
    "_fixture_ref_inner_type",
]

import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar, get_args, get_origin

from oxitest._bridge._fixture_type import _FixtureMarker, _FixtureRefMarker

T = TypeVar("T")


@dataclass
class FixtureDef(Generic[T]):
    name: str
    func: Callable[..., T]
    autouse: bool
    params: tuple[Any, ...] | None
    conftest_path: str  # which conftest registered this (for locality precedence)
    shared: bool = False  # True = session-lifetime, immutable (FrozenProxy-wrapped)
    namespace: str = ""  # Fixtures() instance name; empty = no namespace
    is_async: bool = False  # True = async def or async generator fixture


class FixtureRegistry:
    """Registry of all fixture definitions collected from conftest files.

    Each fixture name maps to an ordered list of `FixtureDef` entries, one per
    conftest that defines it, from the root conftest to the most-local leaf.
    Resolution always picks the last (most-local) entry, implementing pytest's
    locality-wins override semantics.
    """

    def __init__(self) -> None:
        # name -> list of FixtureDef, ordered from root conftest to leaf conftest
        self._defs: dict[str, list[FixtureDef[Any]]] = {}
        self._namespaces: set[str] = set()  # O(1) namespace existence check

    def register(self, defn: FixtureDef[Any]) -> None:
        self._defs.setdefault(defn.name, []).append(defn)
        if defn.namespace:
            self._namespaces.add(defn.namespace)

    def get(self, name: str) -> FixtureDef[Any] | None:
        """Return the most-local (last-registered) FixtureDef for name."""
        defs = self._defs.get(name)
        return defs[-1] if defs else None

    def get_autouse(self) -> Iterator[FixtureDef[Any]]:
        """Yield all autouse fixtures (most-local version of each name)."""
        return (defs[-1] for defs in self._defs.values() if defs and defs[-1].autouse)

    def get_in_namespace(self, name: str, namespace: str) -> FixtureDef[Any] | None:
        """Return the most-local FixtureDef for name within the given namespace."""
        defs = self._defs.get(name)
        if not defs:
            return None
        for defn in reversed(defs):
            if defn.namespace == namespace:
                return defn
        return None

    def get_namespace_for_func(self, name: str, func: Callable[..., Any]) -> str | None:
        """Return the namespace of the FixtureDef whose func is *func*, or None.

        Also handles FixtureAccessor objects by unwrapping via `_fa_func`.
        """
        defs = self._defs.get(name)
        if not defs:
            return None
        # Unwrap FixtureAccessor (duck-typed to avoid circular import)
        raw = getattr(func, "_fa_func", func)
        for defn in defs:
            if defn.func is raw:
                return defn.namespace or None
        return None

    def has_namespace(self, namespace: str) -> bool:
        """Return True if any registered fixture belongs to the given namespace."""
        return namespace in self._namespaces

    def shared_fixture_groups(self) -> list[list[str]]:
        """Compute connected components of shared fixture dependencies.

        Walks fixture function signatures to build a dependency graph, then
        computes transitive closure to find groups of fixtures linked by
        shared fixture dependencies. Returns sorted list of sorted groups.
        """
        graph: dict[str, set[str]] = {}
        for name, defs in self._defs.items():
            if not defs:
                continue
            defn = defs[-1]  # most-local definition
            deps: set[str] = set()
            try:
                sig = inspect.signature(defn.func)
            except (ValueError, TypeError):
                pass
            else:
                for param_name in sig.parameters:
                    if param_name in self._defs and param_name != name:
                        deps.add(param_name)
            graph[name] = deps

        # Find which shared fixtures each fixture transitively reaches.
        def _transitive_shared(name: str, visited: set[str] | None = None) -> set[str]:
            if visited is None:
                visited = set()
            if name in visited:
                return set()
            visited.add(name)
            result: set[str] = set()
            defs = self._defs.get(name)
            if defs and defs[-1].shared:
                result.add(name)
            for dep in graph.get(name, ()):
                result |= _transitive_shared(dep, visited)
            return result

        # Collect fixtures with shared ancestors.
        shared_ancestors: dict[str, frozenset[str]] = {}
        for name in self._defs:
            ancestors = _transitive_shared(name)
            if ancestors:
                shared_ancestors[name] = frozenset(ancestors)

        if not shared_ancestors:
            return []

        # Merge overlapping ancestor sets into connected components.
        components: list[set[str]] = []
        for name, ancestors in shared_ancestors.items():
            all_names = {name} | ancestors
            merged: list[set[str]] = []
            new_component = set(all_names)
            for comp in components:
                if comp & new_component:
                    new_component |= comp
                else:
                    merged.append(comp)
            merged.append(new_component)
            components = merged

        return sorted(sorted(comp) for comp in components)

    def has_shared(self) -> bool:
        """Return True if any effective fixture definition has shared=True."""
        return len(self.shared_fixture_groups()) > 0

    def shared_names(self) -> list[str]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return sorted(
            name for name, defs in self._defs.items() if defs and defs[-1].shared
        )


def _fixture_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture, inner_type). is_fixture is True iff hint is Fixture[T]."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, _FixtureMarker) for m in meta):
        return False, None
    return True, inner


def _fixture_ref_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture_ref, inner_type). True iff hint is FixtureRef[T]."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, _FixtureRefMarker) for m in meta):
        return False, None
    return True, inner
