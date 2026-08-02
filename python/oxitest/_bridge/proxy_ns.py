from __future__ import annotations

__all__ = ["FixturesProxy", "NamespaceProxy", "OxiNamespaceProxy"]

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtin_context import TestContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._builtins._capture import FdCapture, StdCapture
from oxitest._bridge._builtins._logcapture import LogCapture
from oxitest._bridge._builtins._patch import Patcher
from oxitest._bridge._builtins._tempdir import TempDir, TempDirFactory
from oxitest._bridge._test_meta import TestMeta

if TYPE_CHECKING:
    from oxitest._bridge._fixture_session import FixtureSession

_OXI_NAMES: dict[str, type] = {
    "tmp": TempDir,
    "tmp_factory": TempDirFactory,
    "cap": StdCapture,
    "fd_cap": FdCapture,
    "patch": Patcher,
    "log": LogCapture,
    # "ctx" handled separately via _CTX_NAME / TestContext (not in this dict)
}

_CTX_NAME = "ctx"


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

    __slots__ = (
        "_cache",
        "_fn_teardowns",
        "_module_path",
        "_namespace",
        "_session",
        "_test_is_async",
    )

    def __init__(
        self,
        namespace: str,
        session: FixtureSession,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool,
    ) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_test_is_async", test_is_async)
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
                test_is_async=self._test_is_async,
            ),
        )


class OxiNamespaceProxy(_CachingProxy):
    """Lazy proxy for oxitest built-in fixtures under the reserved 'oxi' namespace.

    Maps short names to built-in fixture types:
        tmp, tmp_factory, cap, fd_cap, patch, log, ctx

    Built-in values are cached on first access so that e.g. `fx.oxi.log` always
    returns the same `LogCapture` instance within a single test.
    """

    __slots__ = ("_cache", "_fn_teardowns", "_meta", "_session")

    def __init__(
        self,
        session: FixtureSession,
        meta: TestMeta,
        fn_teardowns: list[Callable[[], None]],
    ) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_meta", meta)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_cache", {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _resolve() -> Any:
            if name == _CTX_NAME:
                inner: type | None = TestContext
            else:
                inner = _OXI_NAMES.get(name)
            if inner is None:
                available = ", ".join(sorted([*_OXI_NAMES, _CTX_NAME]))
                msg = f"fx.oxi has no builtin '{name}'. Available: {available}"
                raise AttributeError(msg)
            impl_cls = BuiltinFixture.for_type(inner)
            if impl_cls is None:
                msg = (
                    f"fx.oxi: builtin type for '{name}' is not registered"
                    " — this is a bug"
                )
                raise RuntimeError(msg)
            # The running test's own meta, forwarded whole. This used to
            # rebuild a synthetic TestMeta from module_path + fn_name and drop
            # node_id, markers and kind — so `fx.oxi.ctx.node_id` returned ""
            # from a real test whose node id was in scope the entire time
            # (#1874).
            return self._session.inject_builtin(
                impl_cls,
                self._meta,
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

    __slots__ = (
        "_cache",
        "_fn_teardowns",
        "_meta",
        "_session",
        "_test_is_async",
    )

    def __init__(
        self,
        session: FixtureSession,
        meta: TestMeta,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool,
    ) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_meta", meta)
        object.__setattr__(self, "_fn_teardowns", fn_teardowns)
        object.__setattr__(self, "_test_is_async", test_is_async)
        object.__setattr__(self, "_cache", {})

    @property
    def session(self) -> FixtureSession:
        """The underlying fixture session."""
        return object.__getattribute__(self, "_session")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _resolve() -> Any:
            # Segment precedence, in order. The fixture-name branch sits
            # immediately BELOW the namespace branch: a package segment wins
            # over a same-named fixture, which stays reachable by its qualified
            # path (ADR-0009 Rule 5's naming-clash rule, live since #1714).
            # Reordering these two silently makes a package unaddressable by
            # its own name.
            if name == "oxi":
                return OxiNamespaceProxy(
                    self._session,
                    self._meta,
                    self._fn_teardowns,
                )
            if self._session.has_namespace(name):
                # Deliberately the FULL-catalog query. A segment this test
                # cannot reach still yields a proxy — inert, like every other
                # proxy here, until a leaf is touched. Refusing at the segment
                # would mean never learning WHICH fixture was wanted, and the
                # BoundaryError has to name the fixture's anchor.
                return NamespaceProxy(
                    name,
                    self._session,
                    self._meta.module_path,
                    self._fn_teardowns,
                    test_is_async=self._test_is_async,
                )
            # Last resort, and deliberately unconditional. Gating this on a
            # full-catalog "is it a fixture name?" query would make the *message*
            # depend on whether some other module happened to be imported into
            # this worker — inline declarations register per worker, so the same
            # source produced different diagnostics serially and under -n.
            # get_fixture_shortcut owns one message that is true either way.
            return self._session.get_fixture_shortcut(
                name,
                self._meta.module_path,
                self._fn_teardowns,
                test_is_async=self._test_is_async,
            )

        return self._get_cached(name, _resolve)
