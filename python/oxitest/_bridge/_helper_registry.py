from __future__ import annotations

__all__ = ["HelperDef", "HelperRegistry"]

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from oxitest._bridge._fixture_registry import ConftestSource, PluginSource

HelperSource = ConftestSource | PluginSource


@dataclass(frozen=True, slots=True)
class HelperDef:
    """Definition of a registered helper callable."""

    name: str
    func: Callable[..., Any]
    source: HelperSource
    namespace: str = ""


class HelperRegistry:
    """Session-level registry for helper definitions."""

    def __init__(self) -> None:
        self._by_name: dict[str, list[HelperDef]] = {}
        self._namespaces: set[str] = set()

    def register(self, defn: HelperDef) -> None:
        """Register a helper definition."""
        self._by_name.setdefault(defn.name, []).append(defn)
        if defn.namespace:
            self._namespaces.add(defn.namespace)

    def get(self, name: str) -> HelperDef | None:
        """Return the most-local (last-registered) HelperDef for name."""
        defs = self._by_name.get(name)
        return defs[-1] if defs else None

    def get_in_namespace(self, name: str, namespace: str) -> HelperDef | None:
        """Return the most-local HelperDef for name within the given namespace."""
        defs = self._by_name.get(name)
        if not defs:
            return None
        for defn in reversed(defs):
            if defn.namespace == namespace:
                return defn
        return None

    def all(self) -> tuple[HelperDef, ...]:
        """Return all effective (most-local) helper defs."""
        return tuple(defs[-1] for defs in self._by_name.values() if defs)

    def has_namespace(self, namespace: str) -> bool:
        """Return True if any registered helper belongs to the given namespace."""
        return namespace in self._namespaces

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)
