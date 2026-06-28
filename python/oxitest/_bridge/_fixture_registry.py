from __future__ import annotations

__all__ = [
    "BuiltinSource",
    "ConftestSource",
    "FixtureDef",
    "FixtureRegistry",
    "FixtureScope",
    "FixtureShadowWarning",
    "FixtureSource",
    "PluginSource",
    "_fixture_inner_type",
]

import inspect
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    Generic,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

from oxitest._bridge._errors import AmbiguousFixtureError, FixtureNotFoundError
from oxitest._bridge._fixture_type import _FixtureMarker
from oxitest._bridge.result import CollectedViolation, ViolationKind

T = TypeVar("T")


class FixtureScope(StrEnum):
    EACH = "each"
    SHARED = "shared"
    SESSION = "session"


@dataclass(frozen=True)
class ConftestSource:
    func: Callable  # type: ignore[type-arg]
    conftest_path: str


@dataclass(frozen=True)
class PluginSource:
    provider: Any  # FixtureProvider — use Any to avoid circular import
    plugin_module: str


@dataclass(frozen=True)
class BuiltinSource:
    impl_cls: type  # type[BuiltinFixture] — use type to avoid circular import


FixtureSource = ConftestSource | PluginSource | BuiltinSource


class FixtureShadowWarning(UserWarning):
    """Emitted when a child conftest shadows a parent conftest fixture."""


@dataclass(frozen=True)
class FixtureDef(Generic[T]):
    name: str
    func: Callable[..., T]
    autouse: bool
    conftest_path: str  # which conftest registered this (for locality precedence)
    shared: bool = False  # True = session-lifetime, immutable (FrozenProxy-wrapped)
    namespace: str = ""  # Fixtures() instance name; empty = no namespace
    is_async: bool = False  # True = async def or async generator fixture
    fixture_type: type | None = None  # binding type for type-based resolve


class FixtureRegistry:
    """Registry of all fixture definitions collected from conftest files.

    Each fixture name maps to an ordered list of `FixtureDef` entries, one per
    conftest that defines it, from the root conftest to the most-local leaf.
    Resolution always picks the last (most-local) entry, implementing pytest's
    locality-wins override semantics.
    """

    def __init__(self) -> None:
        # name -> list of FixtureDef, ordered from root conftest to leaf conftest
        self._by_name: dict[str, list[FixtureDef[Any]]] = {}
        # type -> list of FixtureDef (only for defs with fixture_type set)
        self._by_type: dict[type, list[FixtureDef[Any]]] = {}
        self._namespaces: set[str] = set()  # O(1) namespace existence check
        self._has_shared_cache: bool | None = None

    def register(self, defn: FixtureDef[Any]) -> list[CollectedViolation]:
        self._has_shared_cache = None
        existing = self._by_name.get(defn.name)
        if existing and existing[-1].conftest_path != defn.conftest_path:
            parent = existing[-1]
            warnings.warn(
                FixtureShadowWarning(
                    f"fixture '{defn.name}' in {defn.conftest_path} "
                    f"shadows definition in {parent.conftest_path}"
                ),
                stacklevel=2,
            )
        self._by_name.setdefault(defn.name, []).append(defn)
        if defn.fixture_type is not None:
            self._by_type.setdefault(defn.fixture_type, []).append(defn)
        if defn.namespace:
            self._namespaces.add(defn.namespace)

        # Skip annotation check for builtins (conftest_path starts with "<")
        if defn.conftest_path.startswith("<"):
            return []

        violations: list[CollectedViolation] = []
        try:
            hints = get_type_hints(defn.func)
        except Exception:  # noqa: BLE001
            hints = {}
        if "return" not in hints:
            violations.append(
                CollectedViolation(
                    node_id=defn.conftest_path,
                    kind=ViolationKind.MISSING_RETURN_ANNOTATION,
                    detail=defn.name,
                )
            )
        return violations

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def all_defs(self, name: str) -> list[FixtureDef[Any]]:
        defs = self._by_name.get(name)
        return list(defs) if defs else []

    def get(self, name: str) -> FixtureDef[Any] | None:
        """Return the most-local (last-registered) FixtureDef for name."""
        defs = self._by_name.get(name)
        return defs[-1] if defs else None

    def get_autouse(self) -> Iterator[FixtureDef[Any]]:
        """Yield all autouse fixtures (most-local version of each name)."""
        return (
            defs[-1] for defs in self._by_name.values() if defs and defs[-1].autouse
        )

    def get_in_namespace(self, name: str, namespace: str) -> FixtureDef[Any] | None:
        """Return the most-local FixtureDef for name within the given namespace."""
        defs = self._by_name.get(name)
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
        defs = self._by_name.get(name)
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
        for name, defs in self._by_name.items():
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
                    if param_name in self._by_name and param_name != name:
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
            defs = self._by_name.get(name)
            if defs and defs[-1].shared:
                result.add(name)
            for dep in graph.get(name, ()):
                result |= _transitive_shared(dep, visited)
            return result

        # Collect fixtures with shared ancestors.
        shared_ancestors: dict[str, frozenset[str]] = {}
        for name in self._by_name:
            ancestors = _transitive_shared(name)
            if ancestors:
                shared_ancestors[name] = frozenset(ancestors)

        if not shared_ancestors:
            return []

        # Union-Find to merge overlapping ancestor sets into connected components.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        for name, ancestors in shared_ancestors.items():
            for ancestor in ancestors:
                union(name, ancestor)

        groups: dict[str, set[str]] = {}
        for name in shared_ancestors:
            groups.setdefault(find(name), set()).add(name)

        return sorted(sorted(comp) for comp in groups.values())

    def has_shared(self) -> bool:
        """Return True if any effective fixture definition has shared=True."""
        if self._has_shared_cache is None:
            self._has_shared_cache = len(self.shared_fixture_groups()) > 0
        return self._has_shared_cache

    def shared_names(self) -> list[str]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return sorted(
            name for name, defs in self._by_name.items() if defs and defs[-1].shared
        )

    def resolve(
        self, fixture_type: type, qualifier: str | None = None
    ) -> FixtureDef[Any]:
        """Resolve a fixture by its binding type.

        When exactly one fixture provides *fixture_type*, return it (qualifier
        is ignored).  When multiple fixtures match, *qualifier* (the parameter
        name) is used to disambiguate.  Raises ``FixtureNotFoundError`` if no
        fixture matches, ``AmbiguousFixtureError`` if disambiguation fails.
        """
        candidates = self._by_type.get(fixture_type, [])
        if not candidates:
            raise FixtureNotFoundError(fixture_type.__name__)
        # Deduplicate by name — keep only the most-local (last) entry per name
        by_name: dict[str, FixtureDef[Any]] = {}
        for d in candidates:
            by_name[d.name] = d  # later entries override earlier ones
        unique = list(by_name.values())
        if len(unique) == 1:
            return unique[0]
        # Multiple matches — try qualifier
        if qualifier:
            named = self._by_name.get(qualifier, [])
            matched = [d for d in named if d.fixture_type == fixture_type]
            if len(matched) == 1:
                return matched[0]
        raise AmbiguousFixtureError(
            fixture_type.__name__,
            [d.name for d in unique],
        )


def _extract_annotated_type(hint: Any, marker_type: type) -> tuple[bool, Any]:
    """Return (has_marker, inner_type) for an Annotated[T, marker, ...] hint."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, marker_type) for m in meta):
        return False, None
    return True, inner


def _fixture_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture, inner_type).

    is_fixture is True iff hint is Fixture[T] or @injectable.
    """
    found, inner = _extract_annotated_type(hint, _FixtureMarker)
    if found:
        return True, inner
    if isinstance(hint, type) and getattr(hint, "__oxitest_injectable__", False):
        return True, hint
    return False, None
