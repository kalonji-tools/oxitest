from __future__ import annotations

__all__ = ["FixturesProxy", "NamespaceProxy", "OxiNamespaceProxy"]

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._builtins._capture import FdCapture, StdCapture
from oxitest._bridge._builtins._logcapture import LogCapture
from oxitest._bridge._builtins._patch import Patcher
from oxitest._bridge._builtins._tempdir import TempDir, TempDirFactory

if TYPE_CHECKING:
    from oxitest._bridge._fixture_session import FixtureSession

_OXI_NAMES: dict[str, type] = {
    "tmp": TempDir,
    "tmp_factory": TempDirFactory,
    "cap": StdCapture,
    "fd_cap": FdCapture,
    "patch": Patcher,
    "log": LogCapture,
    # "ctx" handled separately via _get_ctx_type() to avoid circular import
}

_CTX_NAME = "ctx"


def _get_ctx_type() -> type:
    from oxitest._bridge._builtin_context import TestContext

    return TestContext


class _CachingProxy:
    """Mixin that provides caching of resolved attributes via _get_cached.

    This mixin declares __slots__ = () to prevent __dict__ from being reintroduced.
    Concrete subclasses MUST also declare __slots__, including "_cache" as a slot
    to hold the caching dictionary. The mixin accesses _cache via
    object.__getattribute__ to work with __slots__.
    """

    __slots__ = ()

    def _get_cached(self, name: str, factory: Callable[[], Any]) -> Any:
        cache: dict[str, Any] = object.__getattribute__(self, "_cache")
        if name not in cache:
            cache[name] = factory()
        return cache[name]


class NamespaceProxy(_CachingProxy):
    """Lazy proxy for a single user-defined Fixtures() namespace.

    Attribute access resolves the named fixture from the given namespace.
    Results are cached on first access so that `fx.db.conn` always returns
    the same instance within a single test.
    """

    __slots__ = ("_namespace", "_session", "_module_path", "_fn_teardowns", "_cache")

    def __init__(
        self,
        namespace: str,
        session: FixtureSession,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_cache", {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._get_cached(
            name,
            lambda: self._session.get_fixture_in_namespace(
                name,
                self._namespace,
                self._module_path,
                self._fn_teardowns,
            ),
        )


class OxiNamespaceProxy(_CachingProxy):
    """Lazy proxy for oxitest built-in fixtures under the reserved 'oxi' namespace.

    Maps short names to built-in fixture types:
        tmp, tmp_factory, cap, fd_cap, patch, log, ctx

    Built-in values are cached on first access so that e.g. `fx.oxi.log` always
    returns the same `LogCapture` instance within a single test.
    """

    __slots__ = ("_session", "_module_path", "_fn_teardowns", "_fn_name", "_cache")

    def __init__(
        self,
        session: FixtureSession,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        fn_name: str = "",
    ) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_fn_name", fn_name)
        object.__setattr__(self, "_cache", {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _resolve() -> Any:
            if name == _CTX_NAME:
                inner: type | None = _get_ctx_type()
            else:
                inner = _OXI_NAMES.get(name)
            if inner is None:
                available = ", ".join(sorted([*_OXI_NAMES, _CTX_NAME]))
                raise AttributeError(
                    f"fx.oxi has no builtin '{name}'. Available: {available}"
                )
            impl_cls = BuiltinFixture.for_type(inner)
            if impl_cls is None:
                msg = (
                    f"fx.oxi: builtin type for '{name}' is not registered"
                    " — this is a bug"
                )
                raise RuntimeError(msg)
            from oxitest._bridge._test_meta import TestMeta

            meta = TestMeta(
                module_path=self._module_path,
                fn_name=self._fn_name,
                node_id="",
            )
            return self._session.inject_builtin(
                impl_cls,
                meta,
                "function",
                self._fn_teardowns,
            )

        return self._get_cached(name, _resolve)


class FixturesProxy(_CachingProxy):
    """Top-level proxy injected when a test parameter is annotated `fx: Fixtures`.

    Attribute access returns a NamespaceProxy for user-defined namespaces,
    or OxiNamespaceProxy for the reserved 'oxi' namespace.

    Namespace proxies are cached on first access so that `fx.oxi` and `fx.db`
    each return the same proxy object on repeated accesses within one test.
    """

    __slots__ = ("_session", "_module_path", "_fn_teardowns", "_fn_name", "_cache")

    def __init__(
        self,
        session: FixtureSession,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        fn_name: str = "",
    ) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_fn_name", fn_name)
        object.__setattr__(self, "_cache", {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _resolve() -> Any:
            if name == "oxi":
                return OxiNamespaceProxy(
                    self._session,
                    self._module_path,
                    self._fn_teardowns,
                    self._fn_name,
                )
            if not self._session.has_namespace(name):
                raise AttributeError(
                    f"no fixture namespace '{name}' — did you define a "
                    f"Fixtures() instance named '{name}' in conftest.py?"
                )
            return NamespaceProxy(
                name,
                self._session,
                self._module_path,
                self._fn_teardowns,
            )

        return self._get_cached(name, _resolve)
