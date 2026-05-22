from __future__ import annotations

__all__ = [
    "FixtureSession",
    "FixtureTeardownWarning",
    "_NullFixtureSession",
    "_SessionProtocol",
    "_TestContext",
    "_Node",
    "_Scope",
    "_instantiation_context",
    "_teardown_local",
    "_warn_teardown",
]

import inspect
import threading
import warnings
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol

from oxitest._bridge._async_backend import (
    AsyncBackend,
    AsyncioBackend,
    SharedAsyncSession,
)
from oxitest._bridge._errors import (
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
    UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge.plugin_loader import PluginRegistry

# ContextVar set in _instantiate so FixtureAccessor can resolve lazily
# inside fixture bodies. Holds (session, module_path) — immutable only.
# The teardown list is carried separately in _teardown_local (threading.local)
# to avoid sharing a mutable reference across contexts that might inherit a
# copied ContextVar snapshot (e.g. asyncio tasks).
_instantiation_context: ContextVar[tuple[Any, str] | None] = ContextVar(
    "_instantiation_context", default=None
)
# Per-worker-thread teardown stack. Set by resolve_for_test before fixture
# resolution begins; read by FixtureAccessor.__getattr__ when a fixture body
# accesses a lazy fixture attribute. Per-test isolation is established by
# resolve_for_test assigning a fresh list at the start of each test and
# clearing this slot afterwards; threading.local prevents cross-thread
# interference in multi-threaded configurations.
_teardown_local: threading.local = threading.local()


class _SessionProtocol(Protocol):
    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]: ...

    def get_fixture(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None: ...


class _NullFixtureSession:
    """Null Object for when no conftest session is available.

    Allows run_test to treat session as always present, eliminating guards.
    """

    _plugin_registry: PluginRegistry = PluginRegistry()
    _async_backend: AsyncBackend = AsyncioBackend()

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        return {}, []

    def get_fixture(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        from oxitest._bridge._errors import FixtureNotFoundError

        raise FixtureNotFoundError(name)

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        from oxitest._bridge._errors import FixtureNotFoundError

        raise FixtureNotFoundError(name, namespace=namespace)

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return None


# ── _TestContext ──────────────────────────────────────────────────────────────


class _Node(NamedTuple):
    """Minimal test-node info threaded through _TestContext."""

    fn_name: str
    module_path: str


class _TestContext:
    """Provides imperative teardown registration for a single test.

    Injected when a test parameter is annotated with ``TestContext``::

        def test_example(ctx: TestContext) -> None:
            resource = acquire()
            ctx.addfinalizer(resource.close)
            ...

    Use ``addfinalizer`` (or its alias ``on_teardown``) to register cleanup
    callbacks. All registered callbacks run after the test completes, in LIFO
    order, regardless of pass or fail.
    """

    __test__ = False  # prevent pytest from treating this as a test class

    def __init__(
        self,
        node: _Node,
        teardown_stack: list[Callable[[], None]],
    ) -> None:
        self.node = node
        self.param: Any = None
        self._teardown_stack = teardown_stack

    def addfinalizer(self, fn: Callable[[], None]) -> None:
        """Register a cleanup function to run after this test or fixture completes."""
        self._teardown_stack.append(fn)

    #: Beginner-friendly alias for addfinalizer.
    on_teardown = addfinalizer


# ── BuiltinFixture (imported after _TestContext to avoid circular import) ─────
# _builtins/__init__.py imports _TestContext from this module, so this import
# must come after _TestContext is defined.
from oxitest._bridge._builtins._base import (  # noqa: E402
    BuiltinFixture as BuiltinFixture,
    _BuiltinContext,
)

# ── FixtureSession ────────────────────────────────────────────────────────────


class FixtureTeardownWarning(UserWarning):
    """Emitted when an exception occurs inside a yield-fixture teardown.

    Captured by ``WarnCapture`` when a test annotates ``warn: WarnCapture``.
    """


def _warn_teardown(name: str, exc: Exception) -> None:
    if name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    warnings.warn(FixtureTeardownWarning(msg), stacklevel=2)


def _safe_call(fn: Callable[[], None], name: str = "") -> None:
    try:
        fn()
    except Exception as exc:
        _warn_teardown(name, exc)


@dataclass
class _Scope:
    """A single fixture scope: a cache dict and its associated teardown stack."""

    cache: dict[str, Any] = field(default_factory=dict)
    teardowns: list[Callable[[], None]] = field(default_factory=list)

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self.cache:
            self.cache[name] = factory()
        return self.cache[name]

    def drain(self) -> None:
        """Run teardowns in reverse, then clear the stack."""
        for fn in reversed(self.teardowns):
            _safe_call(fn)
        self.teardowns.clear()


async def _task_group_factory():  # type: ignore[return-value]
    """Built-in async yield fixture providing a managed asyncio.TaskGroup.

    Tracks all tasks created via ``task_group.create_task()`` and cancels any
    that are still running when the test body returns, preventing hangs on
    teardown.
    """
    import asyncio

    tasks: list[asyncio.Task[Any]] = []
    tg = asyncio.TaskGroup()
    orig_create_task = tg.create_task

    def _tracked_create(coro, *, name=None, context=None):  # type: ignore[misc]
        t = orig_create_task(coro, name=name, context=context)
        tasks.append(t)
        return t

    tg.create_task = _tracked_create  # type: ignore[method-assign]  # ty: ignore
    async with tg:
        yield tg
        for t in tasks:
            if not t.done():
                t.cancel()


class FixtureSession:
    def __init__(
        self,
        registry: FixtureRegistry,
        plugin_registry: PluginRegistry | None = None,
        async_backend: AsyncBackend | None = None,
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry or PluginRegistry()
        self._async_backend: AsyncBackend = async_backend or AsyncioBackend()
        self._session_scope = _Scope()
        self._shared_scope = (
            _Scope()
        )  # shared=True fixtures — init once, drain at end_session
        self._module_cache = ModuleCache()
        self._shared_session: SharedAsyncSession | None = None
        self._async_teardowns: list[tuple[str, Any]] = []  # (name, async_gen)
        self._used_shared_async = False  # per-test flag, reset in resolve_for_test
        self._registry.register(
            FixtureDef(
                name="task_group",
                func=_task_group_factory,
                autouse=False,
                params=None,
                conftest_path="<builtin>",
                is_async=True,
            )
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def begin_module(self, module_path: str) -> None:
        # Rust no longer calls this; kept as a no-op for test-suite compatibility.
        pass

    def end_module(self, module_path: str) -> None:
        self._module_cache.evict(module_path)

    def end_session(self) -> None:
        # Tear down shared async fixtures first (reverse order), then sync scopes.
        if self._shared_session is not None:
            for name, gen in reversed(self._async_teardowns):
                try:
                    self._shared_session.run(anext(gen))
                except StopAsyncIteration:
                    pass
                except Exception as exc:
                    _warn_teardown(name, exc)
            self._shared_session.close()
            self._shared_session = None
            self._async_teardowns.clear()
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()

    def has_shared_fixtures(self) -> bool:
        """Return True if the effective (most-local) definition has shared=True."""
        return self._registry.has_shared()

    def shared_fixture_names(self) -> list[str]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return self._registry.shared_names()

    def _inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        module_path: str,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
        fn_name: str = "",
    ) -> Any:
        """Create and return a built-in fixture value, respecting its declared scope."""
        if impl_cls.scope == "session":
            return self._session_scope.get_or_create(
                f"__builtin_{impl_cls.__name__}",
                lambda: impl_cls().create(
                    _BuiltinContext(
                        module_path=module_path,
                        inject_scope="session",
                        teardown_stack=self._session_scope.teardowns,
                        plugin_registry=self._plugin_registry,
                    )
                ),
            )
        return impl_cls().create(
            _BuiltinContext(
                module_path=module_path,
                inject_scope=inject_scope,
                teardown_stack=teardown_stack,
                fn_name=fn_name,
                plugin_registry=self._plugin_registry,
            )
        )

    def _try_plugin_fixture(
        self,
        inner: type,
        teardown_stack: list[Callable[[], None]],
    ) -> Any | None:
        """Check plugin registry for a FixtureProvider matching the requested type.

        Returns the fixture value if a provider matches, None otherwise.
        """
        for provider in self._plugin_registry.fixture_providers:
            if provider.fixture_type is inner:
                value = provider.create(None)
                teardown_stack.append(lambda v=value, p=provider: p.teardown(v))
                return value
        return None

    def _resolve_param(
        self,
        param_name: str,
        hint: Any,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        fn_name: str,
        resolve_user_fixture: Callable[[str], Any],
    ) -> tuple[bool, Any]:
        """Resolve a single parameter by its type hint.

        Returns (resolved, value) where resolved=True means the value should be
        injected for this parameter. Returns (False, None) if the hint is not
        injectable (not Fixture[T], not bare Fixtures).
        """
        # Lazy import to avoid circular dependency: Fixtures lives in
        # fixtures.py which imports from this module.
        from oxitest._bridge.fixtures import Fixtures
        from oxitest._bridge.proxy_ns import FixturesProxy

        if hint is Fixtures:
            return True, FixturesProxy(self, module_path, fn_teardowns, fn_name=fn_name)  # type: ignore[arg-type]
        is_fx, inner = _fixture_inner_type(hint)
        if not is_fx:
            return False, None
        impl_cls = BuiltinFixture.for_type(inner)
        if impl_cls is not None:
            return True, self._inject_builtin(
                impl_cls, module_path, "function", fn_teardowns, fn_name=fn_name
            )
        # Check plugin fixture providers (matched by type, not name)
        plugin_value = self._try_plugin_fixture(inner, fn_teardowns)
        if plugin_value is not None:
            return True, plugin_value
        return True, resolve_user_fixture(param_name)

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        """Return (fixture_kwargs, fn_teardowns) for calling fn(**fixture_kwargs).

        Only parameters annotated with Fixture[T] are resolved. Parameters without
        that annotation (plain types, parametrize values) are skipped. Parameters
        named in skip_names are skipped even if annotated with Fixture[T].
        """
        fn_teardowns: list[Callable[[], None]] = []
        self._used_shared_async = False  # reset per-test
        _teardown_local.fn_teardowns = fn_teardowns  # type: ignore[attr-defined]
        try:
            hints = _get_hints(fn)
            fn_name = getattr(fn, "__name__", "")

            # Collect names of explicitly-requested fixtures (to skip in autouse check)
            requested_names: set[str] = set()
            for param_name, hint in hints.items():
                if param_name == "return":
                    continue
                is_fx, inner = _fixture_inner_type(hint)
                if (
                    is_fx
                    and BuiltinFixture.for_type(inner) is None
                    and param_name not in skip_names
                ):
                    requested_names.add(param_name)

            # Autouse: run for side effects; value NOT injected unless
            # explicitly requested
            for defn in self._registry.get_autouse():
                if defn.name not in requested_names:
                    self.get_fixture(defn.name, module_path, fn_teardowns)

            # Resolve Fixture[T]-annotated parameters
            kwargs: dict[str, Any] = {}
            for param_name, hint in hints.items():
                if param_name == "return":
                    continue
                if param_name in skip_names:
                    continue
                resolved, value = self._resolve_param(
                    param_name,
                    hint,
                    module_path,
                    fn_teardowns=fn_teardowns,
                    fn_name=fn_name,
                    resolve_user_fixture=lambda n: self.get_fixture(
                        n, module_path, fn_teardowns
                    ),
                )
                if resolved:
                    kwargs[param_name] = value

            # Check for unannotated params whose name matches a known fixture
            for param_name in inspect.signature(fn).parameters:
                if param_name in skip_names or param_name in kwargs:
                    continue
                hint = hints.get(param_name)
                is_fx = _fixture_inner_type(hint)[0] if hint is not None else False
                if not is_fx and self._registry.get(param_name) is not None:
                    raise UnannotatedFixtureParamError(
                        param_name, getattr(fn, "__name__", repr(fn))
                    )

            return kwargs, fn_teardowns
        finally:
            del _teardown_local.fn_teardowns  # type: ignore[attr-defined]

    def get_fixture(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        return self._resolve_fixture(name, module_path, fn_teardowns, frozenset())

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        defn = self._registry.get_in_namespace(name, namespace)
        if defn is None:
            raise FixtureNotFoundError(name, namespace=namespace)
        # NOTE: bypasses the cycle-detection entry guard in _resolve_fixture;
        # self-referential namespace fixtures are not caught here. Acceptable
        # trade-off — such fixtures are nonsensical and unsupported.
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, frozenset({name})
        )

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return self._registry.get_namespace_for_func(name, func)

    def _resolve_fixture(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        if name in resolving:
            raise FixtureCycleError(name, set(resolving))
        defn = self._registry.get(name)
        if defn is None:
            raise FixtureNotFoundError(name)
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, resolving | {name}
        )

    def _resolve_fixture_defn(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        if defn.shared:
            s = self._shared_scope
            if defn.name in s.cache:
                if defn.is_async:
                    self._used_shared_async = True
                return s.cache[defn.name]

            if defn.is_async:
                return self._resolve_shared_async(
                    defn, module_path, fn_teardowns, resolving
                )

            from oxitest._bridge.proxy import FrozenProxy

            def _make_shared(
                defn: FixtureDef[Any] = defn,
                s: _Scope = s,
                resolving: frozenset[str] = resolving,
            ) -> FrozenProxy:
                return FrozenProxy(
                    self._instantiate(
                        defn, module_path, s.teardowns, fn_teardowns, resolving
                    )
                )

            return s.get_or_create(defn.name, _make_shared)

        return self._instantiate(
            defn, module_path, fn_teardowns, fn_teardowns, resolving
        )

    def _resolve_shared_async(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        """Eagerly resolve a shared async fixture on the session event loop."""
        if self._shared_session is None:
            self._shared_session = self._async_backend.create_shared_session()

        self._used_shared_async = True

        # Resolve sync dependencies (shared async fixtures can only depend on
        # sync fixtures — non-shared async fixtures are unresolved coroutines).
        hints = _get_hints(defn.func)
        deps: dict[str, Any] = {}
        for param_name, hint in hints.items():
            if param_name == "return":
                continue
            resolved, value = self._resolve_param(
                param_name,
                hint,
                module_path,
                fn_teardowns=fn_teardowns,
                fn_name=defn.name,
                resolve_user_fixture=lambda n: self._resolve_fixture(
                    n, module_path, fn_teardowns, resolving
                ),
            )
            if resolved:
                deps[param_name] = value

        # Reject non-shared async fixture values as dependencies of shared fixtures
        for dep_name, dep_val in deps.items():
            if inspect.iscoroutine(dep_val) or inspect.isasyncgen(dep_val):
                if inspect.iscoroutine(dep_val):
                    dep_val.close()
                raise FixtureSetupError(
                    defn.name,
                    RuntimeError(
                        f"shared fixture '{defn.name}' cannot depend on "
                        f"non-shared async fixture '{dep_name}' \u2014 "
                        f"lifetime mismatch"
                    ),
                )

        token = _instantiation_context.set((self, module_path))
        try:
            result = defn.func(**deps)
            if inspect.isasyncgen(result):
                value = self._shared_session.run(anext(result))
                self._async_teardowns.append((defn.name, result))
            elif inspect.iscoroutine(result):
                value = self._shared_session.run(result)
            else:
                value = result
        except Exception as exc:
            raise FixtureSetupError(defn.name, exc) from exc
        finally:
            _instantiation_context.reset(token)

        from oxitest._bridge.proxy import FrozenProxy

        proxy = FrozenProxy(value)
        self._shared_scope.cache[defn.name] = proxy
        return proxy

    def _instantiate(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        scope_teardowns: list[Callable[[], None]],
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        hints = _get_hints(defn.func)
        deps: dict[str, Any] = {}
        for param_name, hint in hints.items():
            if param_name == "return":
                continue
            resolved, value = self._resolve_param(
                param_name,
                hint,
                module_path,
                fn_teardowns=fn_teardowns,
                fn_name=defn.name,
                resolve_user_fixture=lambda n: self._resolve_fixture(
                    n, module_path, fn_teardowns, resolving
                ),
            )
            if resolved:
                deps[param_name] = value

        # Reject async fixture values as dependencies of sync fixtures
        if not defn.is_async:
            for dep_name, dep_val in deps.items():
                if inspect.iscoroutine(dep_val) or inspect.isasyncgen(dep_val):
                    if inspect.iscoroutine(dep_val):
                        dep_val.close()
                    raise FixtureSetupError(
                        defn.name,
                        RuntimeError(
                            f"sync fixture '{defn.name}' cannot depend on "
                            f"async fixture '{dep_name}'"
                        ),
                    )

        # Set instantiation context so FixtureAccessor attribute access
        # (e.g. kvault.store.namespace("x") inside a fixture body) can
        # resolve the live fixture instance via _instantiation_context.
        token = _instantiation_context.set((self, module_path))
        try:
            result = defn.func(**deps)
            _is_gen = inspect.isgenerator(result)
            if _is_gen:
                value = next(result)
            else:
                value = result
        except Exception as exc:
            raise FixtureSetupError(defn.name, exc) from exc
        finally:
            _instantiation_context.reset(token)

        if _is_gen:
            fixture_name = defn.name

            def teardown(gen=result, n=fixture_name):  # type: ignore[misc]
                try:
                    next(gen)
                except StopIteration:
                    pass
                except Exception as exc:
                    _warn_teardown(n, exc)

            scope_teardowns.append(teardown)
            return value

        return result


# Trigger built-in fixture registrations by importing the _builtins package.
# This import is deferred to avoid circular imports — _builtins/__init__.py
# imports _TestContext from this module, so it must be imported after _TestContext
# is defined. The import is at module scope so it runs once on first load.
import oxitest._bridge._builtins  # noqa: F401, E402
