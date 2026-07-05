from __future__ import annotations

__all__ = ["_FixturesProxy", "_fixtures_registry_var"]

from contextvars import ContextVar
from typing import Any

from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixtures import FixtureAccessor, Fixtures

_fixtures_registry_var: ContextVar[FixtureRegistry | None] = ContextVar(
    "_fixtures_registry_var", default=None
)


class _FixtureNamespaceProxy:
    """Proxy for a single fixture namespace. Returns FixtureAccessors."""

    __slots__ = ("_namespace", "_registry")

    def __init__(self, namespace: str, registry: FixtureRegistry) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_registry", registry)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        defn = self._registry.get_in_namespace(name, self._namespace)
        if defn is None:
            raise AttributeError(
                f"fixture namespace '{self._namespace}' has no fixture '{name}'"
            )
        # Build a minimal Fixtures() shell to wrap the accessor
        shell = Fixtures.__new__(Fixtures)
        shell.namespace_name = self._namespace
        return FixtureAccessor(name, shell, defn.func)


class _FixturesProxy:
    """Read-only proxy for accessing fixtures by namespace."""

    __slots__ = ()

    def __getattr__(self, name: str) -> _FixtureNamespaceProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        registry = _fixtures_registry_var.get()
        if registry is None:
            raise AttributeError(
                f"fixtures are only available during a test session — "
                f"cannot access fixtures.{name} outside of a test run"
            )
        if not registry.has_namespace(name):
            raise AttributeError(
                f"no fixture namespace '{name}' — did you define a "
                f"Fixtures() instance named '{name}' in conftest.py?"
            )
        return _FixtureNamespaceProxy(name, registry)
