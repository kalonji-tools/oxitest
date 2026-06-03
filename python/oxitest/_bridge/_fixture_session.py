from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "FixtureContext",
    "FixtureSession",
    "FixtureTeardownWarning",
    "SharedAsyncManager",
    "_FixtureOutcome",
    "_NullFixtureSession",
    "_SessionProtocol",
    "_Scope",
    "_fixture_context",
    "_fixture_scope",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
    "_resolve_deps",
    "_unpack_sync",
    "_current_teardown_node_id",
    "_warn_teardown",
]

import inspect
import time
import warnings
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from oxitest._bridge._async_backend import (
    AsyncBackend,
    AsyncioBackend,
    SharedAsyncSession,
)
from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
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
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry

_current_teardown_node_id: ContextVar[str] = ContextVar(
    "_current_teardown_node_id", default=""
)


@dataclass
class FixtureContext:
    """Context for fixture resolution during test execution.

    Bundles the session, module path, and per-test teardown list into a single
    ContextVar value, replacing the previous dual-state mechanism
    (_instantiation_context ContextVar + _teardown_local threading.local).
    """

    session: Any  # FixtureSession (avoiding circular import)
    module_path: str
    fn_teardowns: list[Callable[[], None]]


_fixture_context: ContextVar[FixtureContext | None] = ContextVar(
    "_fixture_context", default=None
)


@contextmanager
def _fixture_scope(
    session: Any,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
):
    """Scoped fixture context — handles parent lookup and guaranteed reset."""
    parent = _fixture_context.get(None)
    effective = parent.fn_teardowns if parent is not None else fn_teardowns
    token = _fixture_context.set(FixtureContext(session, module_path, effective))
    try:
        yield
    finally:
        _fixture_context.reset(token)


def _check_async_dep(dep_name: str, dep_val: Any, fixture_name: str, msg: str) -> None:
    """Reject an async dependency value with a descriptive error message."""
    if inspect.iscoroutine(dep_val) or inspect.isasyncgen(dep_val):
        if inspect.iscoroutine(dep_val):
            dep_val.close()
        raise FixtureSetupError(fixture_name, RuntimeError(msg))


def _reject_async_in_sync(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Sync fixtures cannot depend on async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"sync fixture '{fixture_name}' cannot depend on async fixture '{dep_name}'",
    )


def _reject_nonshared_async(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Shared fixtures cannot depend on non-shared async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"shared fixture '{fixture_name}' cannot depend on "
        f"non-shared async fixture '{dep_name}' \u2014 "
        f"lifetime mismatch",
    )


AsyncPolicy = Callable[[str, Any, str], None]


def _resolve_deps(
    session: FixtureSession,
    fn: Callable[..., Any],
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    fn_name: str,
    resolve_user: Callable[[str], Any],
    async_policy: AsyncPolicy | None = None,
) -> dict[str, Any]:
    """Resolve fixture dependencies from type hints.

    async_policy: if provided, called as policy(dep_name, dep_val, fn_name)
    for each resolved dependency. Raises on invalid async dependency patterns.
    """
    # Build a minimal TestMeta for fixture-to-fixture resolution (builtins
    # only need module_path and fn_name; node_id/markers are test-level).
    dep_meta = TestMeta(module_path=module_path, fn_name=fn_name, node_id="")
    hints = _get_hints(fn)
    deps: dict[str, Any] = {}
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        resolved, value = session._resolve_param(
            param_name,
            hint,
            dep_meta,
            fn_teardowns=fn_teardowns,
            resolve_user_fixture=resolve_user,
        )
        if resolved:
            deps[param_name] = value
    if async_policy is not None:
        for dep_name, dep_val in deps.items():
            async_policy(dep_name, dep_val, fn_name)
    return deps


@dataclass
class _FixtureOutcome:
    """Result of unpacking a fixture function call."""

    value: Any
    teardown: Callable[[], None] | None = None


def _unpack_sync(result: Any, name: str) -> _FixtureOutcome:
    """Unpack a sync fixture call: plain value or generator."""
    if inspect.isgenerator(result):
        value = next(result)

        def teardown(gen: Any = result, n: str = name) -> None:
            try:
                next(gen)
            except StopIteration:
                pass
            except Exception as exc:
                _warn_teardown(n, exc)

        return _FixtureOutcome(value, teardown)
    return _FixtureOutcome(result)


class _SessionProtocol(Protocol):
    """Structural protocol for objects that can provide fixtures to a test.

    Both `FixtureSession` (full session with a registry) and `_NullFixtureSession`
    (no-op used when no conftest is present) satisfy this protocol, allowing
    `run_test` to treat them uniformly without None guards.
    """

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        meta: TestMeta,
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

    def get_fixture_timings(self) -> list[dict[str, Any]]: ...


class _NullFixtureSession:
    """Null Object for when no conftest session is available.

    Allows run_test to treat session as always present, eliminating guards.
    """

    _plugin_registry: PluginRegistry = PluginRegistry()
    _async_backend: AsyncBackend = AsyncioBackend()
    _keep_tmp: str | None = None
    _result_cell: list[Any] | None = None

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        meta: TestMeta,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        return {}, []

    def get_fixture(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        raise FixtureNotFoundError(name)

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        raise FixtureNotFoundError(name, namespace=namespace)

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return None

    def get_cache_stats(self) -> dict[str, Any]:
        """Return empty cache stats (null session has no shared fixtures)."""
        return {"total_hits": 0, "total_misses": 0, "breakdown": []}

    def get_fixture_timings(self) -> list[dict[str, Any]]:
        """Return empty fixture timings (null session has no fixtures)."""
        return []


# ── FixtureSession ────────────────────────────────────────────────────────────


class FixtureTeardownWarning(UserWarning):
    """Emitted when an exception occurs inside a yield-fixture teardown.

    Captured by `WarnCapture` when a test annotates `warn: WarnCapture`.
    """


def _warn_teardown(name: str, exc: Exception, *, node_id: str = "") -> None:
    effective_id = node_id or _current_teardown_node_id.get()
    if name and effective_id:
        msg = f"fixture '{name}' teardown failed during {effective_id}: {exc}"
    elif name:
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
    _hits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _misses: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self.cache:
            self.cache[name] = factory()
            self._misses[name] += 1
        else:
            self._hits[name] += 1
        return self.cache[name]

    def drain(self) -> None:
        """Run teardowns in reverse, then clear the stack."""
        for fn in reversed(self.teardowns):
            _safe_call(fn)
        self.teardowns.clear()


async def _task_group_factory():  # type: ignore[return-value]
    """Built-in async yield fixture providing a managed asyncio.TaskGroup.

    Tracks all tasks created via `task_group.create_task()` and cancels any
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


class SharedAsyncManager:
    """Manages shared async fixture lifecycle: session creation, resolution, teardown.

    Extracted from FixtureSession to isolate the async fixture management concern.
    The manager lazily creates a SharedAsyncSession on the first resolve() call,
    tracks async generator teardowns, and drains them in LIFO order on cleanup().
    """

    def __init__(self, async_backend: AsyncBackend) -> None:
        self._backend = async_backend
        self._session: SharedAsyncSession | None = None
        self._teardowns: list[tuple[str, Any]] = []
        self._used = False

    @property
    def backend(self) -> AsyncBackend:
        """The async backend used by this manager."""
        return self._backend

    @property
    def was_used(self) -> bool:
        """Whether a shared async fixture was resolved for the current test."""
        return self._used

    @was_used.setter
    def was_used(self, value: bool) -> None:
        self._used = value

    @property
    def session(self) -> SharedAsyncSession | None:
        """The underlying shared async session, or None if not yet created."""
        return self._session

    def resolve(self, func: Callable[..., Any], deps: dict[str, Any]) -> Any:
        """Run an async fixture, track teardowns, return the resolved value.

        Creates the shared session lazily on first call. Handles plain coroutines,
        async generators (with teardown tracking), and sync passthrough.

        Args:
            func: The fixture function to call.
            deps: Already-resolved dependency kwargs.

        Returns:
            The fixture value (awaited if async).

        Raises:
            FixtureSetupError: If the fixture raises during setup.
        """
        if self._session is None:
            self._session = self._backend.create_shared_session()

        self._used = True

        try:
            result = func(**deps)
            if inspect.isasyncgen(result):
                value = self._session.run(anext(result))
                self._teardowns.append((getattr(func, "__name__", ""), result))
            elif inspect.iscoroutine(result):
                value = self._session.run(result)
            else:
                value = result
        except Exception as exc:
            name = getattr(func, "__name__", "")
            raise FixtureSetupError(name, exc) from exc

        return value

    def cleanup(self) -> None:
        """Drain async teardowns in LIFO order, then close the session."""
        if self._session is None:
            return
        for name, gen in reversed(self._teardowns):
            try:
                self._session.run(anext(gen))
            except StopAsyncIteration:
                pass
            except Exception as exc:
                _warn_teardown(name, exc)
        self._session.close()
        self._session = None
        self._teardowns.clear()


class FixtureSession:
    """Manages fixture lifecycle for a single oxitest run.

    Owns three fixture scopes:

    - **function scope** (per-test teardown list, `fn_teardowns`) — default
      for all user-defined fixtures.
    - **shared scope** (`_shared_scope`) — for fixtures declared with
      ``shared=True``; initialised once and torn down at `end_session`.
    - **session scope** (`_session_scope`) — for built-in session-lifetime
      fixtures such as `TempDirFactory`.

    Built-in fixtures (e.g. `TempDir`, `LogCapture`) are injected by type via
    `Fixture[T]` annotations.  User fixtures are looked up by parameter name in
    the `FixtureRegistry`.  Async fixtures are delegated to the configured
    `AsyncBackend`.

    The session is constructed once by `conftest_loader` and passed into every
    `run_test` call for the duration of the run.
    """

    def __init__(
        self,
        registry: FixtureRegistry,
        plugin_registry: PluginRegistry | None = None,
        async_backend: AsyncBackend | None = None,
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry or PluginRegistry()
        self._async_mgr = SharedAsyncManager(async_backend or AsyncioBackend())
        self._session_scope = _Scope()
        self._shared_scope = (
            _Scope()
        )  # shared=True fixtures — init once, drain at end_session
        self._module_cache = ModuleCache()
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
        self._keep_tmp: str | None = None
        self._result_cell: list[Any] | None = None
        self._setup_times: dict[str, list[float]] = defaultdict(list)
        self._teardown_times: dict[str, list[float]] = defaultdict(list)

    # ── Async delegation properties (used by executor.py via getattr) ────────

    @property
    def _async_backend(self) -> AsyncBackend:
        return self._async_mgr.backend

    @_async_backend.setter
    def _async_backend(self, value: AsyncBackend) -> None:
        self._async_mgr.cleanup()
        self._async_mgr = SharedAsyncManager(value)

    @property
    def _shared_session(self) -> SharedAsyncSession | None:
        return self._async_mgr.session

    @property
    def _used_shared_async(self) -> bool:
        return self._async_mgr.was_used

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def begin_module(self, module_path: str) -> None:
        # Rust no longer calls this; kept as a no-op for test-suite compatibility.
        pass

    def end_module(self, module_path: str) -> None:
        self._module_cache.evict(module_path)

    def end_session(self) -> None:
        # Tear down shared async fixtures first (reverse order), then sync scopes.
        self._async_mgr.cleanup()
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()

    def get_cache_stats(self) -> dict[str, Any]:
        """Return shared fixture cache hit/miss statistics."""
        s = self._shared_scope
        names = sorted(set(s._hits.keys()) | set(s._misses.keys()))
        return {
            "total_hits": sum(s._hits.values()),
            "total_misses": sum(s._misses.values()),
            "breakdown": [
                {
                    "name": n,
                    "hits": s._hits.get(n, 0),
                    "misses": s._misses.get(n, 0),
                }
                for n in names
            ],
        }

    def has_shared_fixtures(self) -> bool:
        """Return True if the effective (most-local) definition has shared=True."""
        return self._registry.has_shared()

    def shared_fixture_names(self) -> list[str]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return self._registry.shared_names()

    def shared_fixture_groups(self) -> list[list[str]]:
        """Return connected components of shared fixture dependencies."""
        return self._registry.shared_fixture_groups()

    def registered_fixture_names(self) -> list[str]:
        """Return all fixture names known to the registry."""
        return list(self._registry._defs.keys())

    def validate_fixture_names(
        self,
        items: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Return ``(node_id, fixture_name)`` pairs that cannot resolve.

        Called by the Rust ``FixtureValidationPhase`` after collection to catch
        typos and missing fixtures before any test executes.
        """
        # Build set of types that plugin fixture providers can inject.
        plugin_types: set[type] = set()
        if hasattr(self, "_plugin_registry"):
            for provider in self._plugin_registry.fixture_providers:
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

    def get_fixture_timings(self) -> list[dict[str, Any]]:
        """Return per-fixture setup and teardown timing aggregates."""
        names = sorted(set(self._setup_times.keys()) | set(self._teardown_times.keys()))
        return [
            {
                "name": n,
                "total_setup_ms": float(sum(self._setup_times.get(n, []))),
                "setup_count": len(self._setup_times.get(n, [])),
                "total_teardown_ms": float(sum(self._teardown_times.get(n, []))),
                "teardown_count": len(self._teardown_times.get(n, [])),
            }
            for n in names
        ]

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

    def _inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        meta: TestMeta,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
    ) -> Any:
        """Create and return a built-in fixture value, respecting its declared scope."""
        if impl_cls.scope == "session":
            return self._session_scope.get_or_create(
                f"__builtin_{impl_cls.__name__}",
                lambda: impl_cls().create(
                    _BuiltinContext(
                        meta=meta,
                        inject_scope="session",
                        teardown_stack=self._session_scope.teardowns,
                        plugin_registry=self._plugin_registry,
                        keep_tmp=self._keep_tmp,
                        result_cell=self._result_cell,
                    )
                ),
            )
        return impl_cls().create(
            _BuiltinContext(
                meta=meta,
                inject_scope=inject_scope,
                teardown_stack=teardown_stack,
                plugin_registry=self._plugin_registry,
                keep_tmp=self._keep_tmp,
                result_cell=self._result_cell,
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
        meta: TestMeta,
        fn_teardowns: list[Callable[[], None]],
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
            return True, FixturesProxy(
                self, meta.module_path, fn_teardowns, fn_name=meta.fn_name
            )  # type: ignore[arg-type]
        is_fx, inner = _fixture_inner_type(hint)
        if not is_fx:
            return False, None
        impl_cls = BuiltinFixture.for_type(inner)
        if impl_cls is not None:
            return True, self._inject_builtin(impl_cls, meta, "function", fn_teardowns)
        # Check plugin fixture providers (matched by type, not name)
        plugin_value = self._try_plugin_fixture(inner, fn_teardowns)
        if plugin_value is not None:
            return True, plugin_value
        return True, resolve_user_fixture(param_name)

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        meta: TestMeta,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        """Return (fixture_kwargs, fn_teardowns) for calling fn(**fixture_kwargs).

        Only parameters annotated with Fixture[T] are resolved. Parameters without
        that annotation (plain types, parametrize values) are skipped. Parameters
        named in skip_names are skipped even if annotated with Fixture[T].
        """
        fn_teardowns: list[Callable[[], None]] = []
        self._async_mgr.was_used = False  # reset per-test
        with _fixture_scope(self, meta.module_path, fn_teardowns):
            hints = _get_hints(fn)

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
                    self.get_fixture(defn.name, meta.module_path, fn_teardowns)

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
                    meta,
                    fn_teardowns=fn_teardowns,
                    resolve_user_fixture=lambda n: self.get_fixture(
                        n, meta.module_path, fn_teardowns
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
                    self._async_mgr.was_used = True
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
        # Resolve sync dependencies (shared async fixtures can only depend on
        # sync fixtures — non-shared async fixtures are unresolved coroutines).
        deps = _resolve_deps(
            self,
            defn.func,
            module_path,
            fn_teardowns=fn_teardowns,
            fn_name=defn.name,
            resolve_user=lambda n: self._resolve_fixture(
                n, module_path, fn_teardowns, resolving
            ),
            async_policy=_reject_nonshared_async,
        )

        with _fixture_scope(self, module_path, fn_teardowns):
            _start = time.monotonic()
            value = self._async_mgr.resolve(defn.func, deps)
            self._setup_times[defn.name].append((time.monotonic() - _start) * 1000.0)

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
        deps = _resolve_deps(
            self,
            defn.func,
            module_path,
            fn_teardowns=fn_teardowns,
            fn_name=defn.name,
            resolve_user=lambda n: self._resolve_fixture(
                n, module_path, fn_teardowns, resolving
            ),
            async_policy=_reject_async_in_sync if not defn.is_async else None,
        )

        # Set fixture context so FixtureAccessor attribute access
        # (e.g. kvault.store.namespace("x") inside a fixture body) can
        # resolve the live fixture instance via _fixture_context.
        with _fixture_scope(self, module_path, fn_teardowns):
            try:
                _start = time.monotonic()
                result = defn.func(**deps)
                outcome = _unpack_sync(result, defn.name)
                self._setup_times[defn.name].append(
                    (time.monotonic() - _start) * 1000.0
                )
            except Exception as exc:
                raise FixtureSetupError(defn.name, exc) from exc

        if outcome.teardown is not None:
            _original_td = outcome.teardown
            _td_name = defn.name

            def _timed_teardown(
                _orig: Callable[[], None] = _original_td,
                _name: str = _td_name,
            ) -> None:
                _td_start = time.monotonic()
                _orig()
                self._teardown_times[_name].append(
                    (time.monotonic() - _td_start) * 1000.0
                )

            scope_teardowns.append(_timed_teardown)
        return outcome.value


# Trigger built-in fixture registrations by importing the _builtins package.
# The import is at module scope so it runs once on first load.
import oxitest._bridge._builtins  # noqa: F401, E402
