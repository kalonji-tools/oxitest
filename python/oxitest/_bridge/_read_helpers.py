from __future__ import annotations

__all__ = ["_HelpersProxy", "_helpers_registry_var"]

from contextvars import ContextVar
from typing import Any

from oxitest._bridge._helper_registry import HelperRegistry

_helpers_registry_var: ContextVar[HelperRegistry | None] = ContextVar(
    "_helpers_registry_var", default=None
)


class _HelperNamespaceProxy:
    """Proxy for a single helper namespace. Attribute access returns callables."""

    __slots__ = ("_namespace", "_registry")

    def __init__(self, namespace: str, registry: HelperRegistry) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_registry", registry)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        defn = self._registry.get_in_namespace(name, self._namespace)
        if defn is not None:
            return defn.func
        msg = f"helper namespace '{self._namespace}' has no helper '{name}'"
        raise AttributeError(msg)


class _HelpersProxy:
    """Read-only proxy for accessing helpers by namespace."""

    __slots__ = ()

    def __getattr__(self, name: str) -> _HelperNamespaceProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        registry = _helpers_registry_var.get()
        if registry is None:
            msg = (
                f"helpers are only available during a test session — "
                f"cannot access helpers.{name} outside of a test run"
            )
            raise AttributeError(msg)
        if not registry.has_namespace(name):
            msg = (
                f"no helper namespace '{name}' — did you define a "
                f"Helpers() instance named '{name}' in conftest.py?"
            )
            raise AttributeError(msg)
        return _HelperNamespaceProxy(name, registry)
