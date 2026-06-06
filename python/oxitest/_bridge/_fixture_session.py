from __future__ import annotations

__all__ = [
    "FixtureAccessor",
    "FixtureContext",
    "Fixtures",
    "FixtureSession",
    "FixtureTeardownWarning",
    "SharedAsyncManager",
    "TestRunContext",
    "_NullFixtureSession",
    "_SessionProtocol",
    "_Scope",
    "_fixture_context",
    "_fixture_scope",
    "_test_run_context",
    "_current_teardown_node_id",
    "_warn_teardown",
]

import inspect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from oxitest._bridge._async_backend import (
    AsyncBackend,
    AsyncioBackend,
    SharedAsyncSession,
)
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._errors import (
    FixtureNotFoundError,
    FixtureSetupError,
    UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_context import (
    FixtureContext,
    FixtureTeardownWarning,
    TestRunContext,
    _current_teardown_node_id,
    _fixture_context,
    _fixture_scope,
    _test_run_context,
    _warn_teardown,
)
from oxitest._bridge._fixture_instantiator import (
    ScopeRefs,
)
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._fixtures import (
    FixtureAccessor,
    Fixtures,
)
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest._bridge.result import CacheEntry, CacheStats, FixtureTiming


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

    def get_fixture_timings(self) -> list[FixtureTiming]: ...


class _NullFixtureSession:
    """Null Object for when no conftest session is available.

    Allows run_test to treat session as always present, eliminating guards.
    """

    _plugin_registry: PluginRegistry = PluginRegistry()
    _async_backend: AsyncBackend = AsyncioBackend()

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

    def get_cache_stats(self) -> CacheStats:
        """Return empty cache stats (null session has no shared fixtures)."""
        return CacheStats(total_hits=0, total_misses=0, breakdown=())

    def get_fixture_timings(self) -> list[FixtureTiming]:
        """Return empty fixture timings (null session has no fixtures)."""
        return []


# ── FixtureSession ────────────────────────────────────────────────────────────


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

        from oxitest._bridge._fixture_instantiator import (
            FixtureInstantiator as _FixtureInstantiator,
        )
        from oxitest._bridge._fixture_validator import (
            FixtureValidator as _FixtureValidator,
        )

        self._instantiator = _FixtureInstantiator(
            self._registry,
            self._plugin_registry,
            self._async_mgr,
            session_scope=self._session_scope,
        )
        self._validator = _FixtureValidator(
            self._registry, self._plugin_registry, self._module_cache
        )
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

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        # Rust bridge replaces _plugin_registry via setattr after construction;
        # propagate so the Instantiator always uses the current registry.
        if name == "_plugin_registry" and hasattr(self, "_instantiator"):
            self._instantiator._plugin_registry = value

    def _scope_for(self, defn: FixtureDef) -> ScopeRefs | None:
        """Map a fixture def to its scope refs. None = function scope."""
        if defn.shared:
            s = self._shared_scope
            return ScopeRefs(s.cache, s.teardowns, s._hits, s._misses)
        return None

    # ── Async delegation properties (used by executor.py via getattr) ────────

    @property
    def _async_backend(self) -> AsyncBackend:
        return self._async_mgr.backend

    @_async_backend.setter
    def _async_backend(self, value: AsyncBackend) -> None:
        self._async_mgr.cleanup()
        self._async_mgr = SharedAsyncManager(value)
        self._instantiator._async_mgr = self._async_mgr

    @property
    def _shared_session(self) -> SharedAsyncSession | None:
        return self._async_mgr.session

    @property
    def _used_shared_async(self) -> bool:
        return self._async_mgr.was_used

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def end_module(self, module_path: str) -> None:
        self._module_cache.evict(module_path)

    def end_session(self) -> None:
        # Tear down shared async fixtures first (reverse order), then sync scopes.
        self._async_mgr.cleanup()
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()

    def get_cache_stats(self) -> CacheStats:
        """Return shared fixture cache hit/miss statistics."""
        s = self._shared_scope
        names = sorted(set(s._hits.keys()) | set(s._misses.keys()))
        return CacheStats(
            total_hits=sum(s._hits.values()),
            total_misses=sum(s._misses.values()),
            breakdown=tuple(
                CacheEntry(
                    name=n,
                    hits=s._hits.get(n, 0),
                    misses=s._misses.get(n, 0),
                )
                for n in names
            ),
        )

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
        return list(self._registry)

    def validate_fixture_names(
        self,
        items: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Return ``(node_id, fixture_name)`` pairs that cannot resolve.

        Called by the Rust ``FixtureValidationPhase`` after collection to catch
        typos and missing fixtures before any test executes.
        """
        # Pass current _plugin_registry explicitly: Rust may have replaced it
        # via setattr after the validator was constructed.
        return self._validator.validate_fixture_names(items, self._plugin_registry)

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
        return self._validator.find_unused_fixtures(items)

    def get_fixture_timings(self) -> list[FixtureTiming]:
        """Return per-fixture setup and teardown timing aggregates."""
        return self._instantiator.get_fixture_timings()

    def _inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        meta: TestMeta,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
    ) -> Any:
        """Forward to instantiator for session-scoped builtins."""
        return self._instantiator.inject_builtin(
            impl_cls,
            meta,
            inject_scope,
            teardown_stack,
            session_scope=self._session_scope,
        )

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
                resolved, value = self._instantiator.resolve_param(
                    param_name,
                    hint,
                    meta,
                    fn_teardowns=fn_teardowns,
                    resolve_user_fixture=lambda n: self.get_fixture(
                        n, meta.module_path, fn_teardowns
                    ),
                    proxy_session=self,
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
        return self._instantiator.resolve_fixture(
            name, module_path, fn_teardowns, frozenset(), self._scope_for
        )

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
        return self._instantiator.resolve_fixture_in_namespace(
            defn, name, module_path, fn_teardowns, self._scope_for
        )

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return self._registry.get_namespace_for_func(name, func)


# Trigger built-in fixture registrations by importing the _builtins package.
# The import is at module scope so it runs once on first load.
import oxitest._bridge._builtins  # noqa: F401, E402
