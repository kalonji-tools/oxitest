"""Fixture resolution and instantiation — extracted from FixtureSession."""

from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "DispatchContext",
    "FixtureInstantiator",
    "FixtureOutcome",
    "HasTeardown",
    "NoTeardown",
    "ScopeRefs",
    "_ResolutionContext",
    "_check_async_dep",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
    "_resolve_deps",
    "_unpack_sync",
]

import contextlib
import inspect
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, assert_never

from oxitest._bridge._async_fixture_handle import (
    AsyncFixtureHandle,
    register_async_teardown,
)
from oxitest._bridge._async_orchestrator import (
    AsyncPolicy,
    _check_async_dep,
    _reject_async_in_sync,
    _reject_nonshared_async,
)
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._errors import (
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
)
from oxitest._bridge._fixture_context import (
    _fixture_scope,
    _test_run_context,
    _warn_teardown,
)
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureScope,
    ModuleSource,
    PluginSource,
    _fixture_inner_type,
)
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.proxy import FrozenProxy
from oxitest._bridge.result import FixtureTiming

if TYPE_CHECKING:
    from oxitest._bridge._builtins._base import BuiltinFixture
    from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry
    from oxitest._bridge._fixture_session import _Scope
    from oxitest._bridge.plugin_loader import PluginRegistry


@dataclass(frozen=True, slots=True)
class ScopeRefs:
    """References to the scope a fixture should be cached/torn down in."""

    cache: dict[str, Any]
    teardowns: list[Callable[[], None]]
    hits: dict[str, int]
    misses: dict[str, int]


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Context threaded through source-based fixture dispatch.

    Bundled args for FixtureInstantiator.resolve_by_source. Separate from
    _ResolutionContext (which threads by-name resolution state including
    cycle detection).

    Fields:
        meta: forwarded to BuiltinSource injection
        fn_teardowns: accumulator for PluginSource teardown lambdas
        resolve_user_fixture: cycle-safe resolver for ConftestSource
    """

    meta: TestMeta
    fn_teardowns: list[Callable[[], None]]
    resolve_user_fixture: Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class _ResolutionContext:
    """Shared context threaded through fixture resolution."""

    module_path: str
    fn_teardowns: list[Callable[[], None]]
    resolving: frozenset[str]
    scope_callback: Callable[[FixtureDef[Any], str], ScopeRefs | None]


def _cache_key(defn: FixtureDef[Any]) -> str:
    """Key a fixture within its scope's cache.

    Module scope is the first path on which a *namespaced* fixture is cached,
    so ``defn.name`` alone is no longer unique: two module-lifetime fixtures
    with the same short name in different namespaces (``pkg_a.resource`` and
    ``pkg_b.resource``) used by one test module would collide, and the second
    would silently receive the first's instance.

    Shared scope deliberately keeps the bare name. ``FixtureSession.get_cache_stats``
    surfaces these keys verbatim as user-facing fixture names in the cache
    report, so qualifying them would change reporter output — a change that
    belongs with the old-API retirement in slice 13, not here.
    """
    if defn.scope is FixtureScope.MODULE and defn.namespace:
        return f"{defn.namespace}.{defn.name}"
    return defn.name


def _resolve_deps(
    instantiator: FixtureInstantiator,
    fn: Callable[..., Any],
    ctx: _ResolutionContext,
    fn_name: str,
    resolve_user: Callable[[str], Any],
) -> dict[str, Any]:
    """Resolve fixture dependencies from type hints."""
    # Build a minimal TestMeta for fixture-to-fixture resolution (builtins
    # only need module_path and fn_name; node_id/markers are test-level).
    dep_meta = TestMeta(module_path=ctx.module_path, fn_name=fn_name, node_id="")
    hints = _get_hints(fn)
    deps: dict[str, Any] = {}
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        resolved, value = instantiator.resolve_param(
            param_name,
            hint,
            dep_meta,
            fn_teardowns=ctx.fn_teardowns,
            resolve_user_fixture=resolve_user,
        )
        if resolved:
            deps[param_name] = value
    return deps


@dataclass(frozen=True, slots=True)
class HasTeardown:
    """Fixture unpacking produced a value plus a teardown callable."""

    value: Any
    teardown: Callable[[], None]


@dataclass(frozen=True, slots=True)
class NoTeardown:
    """Fixture unpacking produced a plain value; no teardown to run."""

    value: Any


FixtureOutcome = HasTeardown | NoTeardown


def _unpack_sync(result: Any, name: str) -> FixtureOutcome:
    """Unpack a sync fixture call: plain value or generator.

    Coroutines and async generators are passed through as-is: the async
    execution middleware (`_unpack_async_fixtures`) awaits/advances them
    inside the test's event loop for parameter-injected async fixtures,
    and `executor._drive_arrange_async_each` handles the arrange path.
    """
    if inspect.isgenerator(result):
        value = next(result)

        def teardown(gen: Any = result, fixture_name: str = name) -> None:
            def _drain() -> None:
                with contextlib.suppress(StopIteration):
                    next(gen)

            safe_teardown(_drain, fixture_name, warn=_warn_teardown)

        return HasTeardown(value=value, teardown=teardown)
    return NoTeardown(value=result)


# ── FixtureInstantiator ──────────────────────────────────────────────────────


class FixtureInstantiator:
    """Resolves and creates fixture values. Stateless — scope refs passed in.

    The Instantiator never owns fixture scopes; callers supply scope information
    via a ``scope_callback`` that maps a ``(FixtureDef, module_path)`` pair to
    ``ScopeRefs | None``. ``None`` means function scope (no caching); a
    ``ScopeRefs`` means a cached scope — shared, or the module bucket selected
    by *module_path*.
    """

    def __init__(
        self,
        registry: FixtureRegistry,
        plugin_registry: PluginRegistry,
        async_mgr: Any = None,  # SharedAsyncManager, optional to avoid import
        session_scope: _Scope | None = None,
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry
        self._async_mgr = async_mgr
        self._session_scope = session_scope
        self._setup_times: dict[str, list[float]] = defaultdict(list)
        self._teardown_times: dict[str, list[float]] = defaultdict(list)

    @property
    def plugin_registry(self) -> PluginRegistry:
        return self._plugin_registry

    @plugin_registry.setter
    def plugin_registry(self, value: PluginRegistry) -> None:
        self._plugin_registry = value

    @property
    def async_mgr(self) -> Any:
        return self._async_mgr

    @async_mgr.setter
    def async_mgr(self, value: Any) -> None:
        self._async_mgr = value

    # ── Parameter resolution ─────────────────────────────────────────────

    def resolve_param(
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
        injectable (not Fixture[T]).

        Note: bare ``Fixtures`` hints are handled by the caller before this
        method is called.
        """
        is_fx, inner = _fixture_inner_type(hint)
        if not is_fx:
            return False, None

        # Hoist ctx once — reused by both branches below
        ctx = DispatchContext(
            meta=meta,
            fn_teardowns=fn_teardowns,
            resolve_user_fixture=resolve_user_fixture,
        )

        # Broad type fallback (Fixture[Any] / Fixture[object])
        if inner is Any or inner is object:
            defn = self._registry.get(param_name)
            if defn is None:
                raise FixtureNotFoundError(param_name)
            return True, self.resolve_by_source(defn, ctx)

        # Unified type-based resolution — try type first
        try:
            defn = self._registry.resolve(inner, qualifier=param_name)
        except FixtureNotFoundError:
            # No type-based match. Fall back to name-based lookup.
            if self._registry.get(param_name) is not None:
                return True, resolve_user_fixture(param_name)
            raise FixtureNotFoundError(param_name) from None

        # For Builtin/Plugin sources found by type, use direct instantiation
        if not isinstance(defn.source, (ConftestSource, ModuleSource)):
            return True, self.resolve_by_source(defn, ctx)

        # For ConftestSource/ModuleSource: prefer name-based (preserves cycle
        # detection), fall back to type-resolved name.
        resolve_name = (
            param_name if self._registry.get(param_name) is not None else defn.name
        )
        return True, resolve_user_fixture(resolve_name)

    def resolve_by_source(
        self,
        defn: FixtureDef[Any],
        ctx: DispatchContext,
    ) -> Any:
        """Instantiate a fixture from its FixtureDef via source-based dispatch.

        Dispatches per ``FixtureSource`` variant:

        - ``ConftestSource`` / ``ModuleSource``: routes through
          ``ctx.resolve_user_fixture`` to preserve cycle detection and scope
          caching.  Slice 1: both variants behave identically here; divergence
          appears in slices 2 (module-lifetime scope cache), 6 (B1 boundary
          enforcement), and 9 (autouse).
        - ``PluginSource``: invokes ``provider.create(ctx=None)`` and appends the
          provider's teardown to ``ctx.fn_teardowns``.
        - ``BuiltinSource``: delegates to ``inject_builtin`` with function scope.
        """
        match defn.source:
            case ConftestSource() | ModuleSource():
                return ctx.resolve_user_fixture(defn.name)
            case PluginSource(provider=provider):
                value = provider.create(ctx=None)
                ctx.fn_teardowns.append(lambda v=value, p=provider: p.teardown(value=v))
                return value
            case BuiltinSource(impl_cls=impl_cls):
                return self.inject_builtin(
                    impl_cls, ctx.meta, "function", ctx.fn_teardowns
                )

    # ── Fixture resolution ───────────────────────────────────────────────

    def resolve_fixture(
        self,
        name: str,
        ctx: _ResolutionContext,
    ) -> Any:
        """Resolve a fixture by name.

        Raises FixtureCycleError if a cycle is detected, FixtureNotFoundError
        if the fixture is not registered.
        """
        if name in ctx.resolving:
            raise FixtureCycleError(name, set(ctx.resolving))
        defn = self._registry.get(name)
        # Bare-name lookup, so this route never sees a namespace — the inline
        # module restriction has to be applied here too. Filtering only the
        # proxy path (`get_fixture_in_namespace`) left an inline fixture
        # injectable into a sibling file by `Fixture[T]` annotation, which is the
        # route a user reaches for first.
        if defn is None or not defn.is_visible_from(ctx.module_path):
            raise FixtureNotFoundError(name)
        return self._resolve_fixture_defn(
            defn, replace(ctx, resolving=ctx.resolving | {name})
        )

    def resolve_fixture_in_namespace(
        self,
        defn: FixtureDef[Any],
        name: str,
        ctx: _ResolutionContext,
    ) -> Any:
        """Resolve a fixture definition found by namespace lookup.

        Bypasses cycle-detection entry guard (acceptable trade-off — self-referential
        namespace fixtures are nonsensical and unsupported).
        """
        return self._resolve_fixture_defn(
            defn, replace(ctx, resolving=frozenset({name}))
        )

    def _resolve_fixture_defn(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
    ) -> Any:
        """Resolve a fixture definition, handling scope caching."""
        scope_refs = ctx.scope_callback(defn, ctx.module_path)

        if scope_refs is not None:
            # Cached fixture (shared or module scope) — check cache first
            key = _cache_key(defn)
            if key in scope_refs.cache:
                if defn.is_async and self._async_mgr is not None:
                    self._async_mgr.was_used = True
                scope_refs.hits[key] = scope_refs.hits.get(key, 0) + 1
                return scope_refs.cache[key]

            scope_refs.misses[key] = scope_refs.misses.get(key, 0) + 1

            if defn.is_async:
                return self._resolve_shared_async(defn, ctx, scope_refs)

            value = FrozenProxy(self._instantiate(defn, ctx, scope_refs.teardowns))
            scope_refs.cache[key] = value
            return value

        # Function scope — no caching
        return self._instantiate(defn, ctx, ctx.fn_teardowns)

    def _resolve_shared_async(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs,
    ) -> Any:
        """Eagerly resolve a shared async fixture on the session event loop."""
        deps = _resolve_deps(
            self,
            defn.func,
            ctx,
            fn_name=defn.name,
            resolve_user=lambda n: self.resolve_fixture(n, ctx),
        )
        for dep_name, dep_val in deps.items():
            _reject_nonshared_async(dep_name, dep_val, defn.name)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            _start = time.monotonic()
            value = self._async_mgr.resolve(defn.func, deps)
            self._setup_times[_cache_key(defn)].append(
                (time.monotonic() - _start) * 1000.0
            )

        proxy = FrozenProxy(value)
        scope_refs.cache[_cache_key(defn)] = proxy
        return proxy

    def resolve_async_in_namespace(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
    ) -> AsyncFixtureHandle:
        """Return an awaitable handle for an async fixture reached via ``fx.``.

        Deliberately does *not* route through :meth:`_resolve_shared_async`.
        That path resolves eagerly with ``session.run(...)`` — i.e.
        ``run_until_complete`` — which raises ``Cannot run the event loop
        while another loop is running`` when called from inside the test
        body's loop, which is exactly where ``fx.`` resolution happens. The
        handle awaits on whatever loop the body is already using instead.
        """
        scope_refs = ctx.scope_callback(defn, ctx.module_path)
        return AsyncFixtureHandle(
            lambda: self._build_async(defn, ctx, scope_refs), defn.name
        )

    async def _build_async(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs | None,
    ) -> Any:
        """Build (or return the cached) value for an async fixture.

        Runs on the caller's event loop. *scope_refs* is ``None`` for function
        lifetime — no caching — and the tier's scope otherwise.
        """
        key = _cache_key(defn)
        if scope_refs is not None:
            if key in scope_refs.cache:
                scope_refs.hits[key] = scope_refs.hits.get(key, 0) + 1
                return scope_refs.cache[key]
            scope_refs.misses[key] = scope_refs.misses.get(key, 0) + 1

        deps = await self._resolve_async_deps(defn, ctx, scope_refs)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            _start = time.monotonic()
            try:
                raw = defn.func(**deps)
                if inspect.isasyncgen(raw):
                    value = await raw.__anext__()
                    self._queue_async_teardown(defn, ctx, scope_refs, raw)
                elif inspect.iscoroutine(raw):
                    value = await raw
                else:
                    value = raw
            except Exception as exc:
                raise FixtureSetupError(defn.name, exc) from exc
            self._setup_times[key].append((time.monotonic() - _start) * 1000.0)

        if scope_refs is not None:
            value = FrozenProxy(value)
            scope_refs.cache[key] = value
        return value

    async def _resolve_async_deps(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs | None,
    ) -> dict[str, Any]:
        """Resolve *defn*'s dependencies, advancing any that are async.

        The sync resolution path hands coroutines and async generators back
        untouched, so anything async arrives un-advanced and has to be driven
        here, on the caller's loop.
        """
        deps = _resolve_deps(
            self,
            defn.func,
            ctx,
            fn_name=defn.name,
            resolve_user=lambda n: self.resolve_fixture(n, ctx),
        )
        for dep_name, dep_val in deps.items():
            if scope_refs is not None:
                # This fixture outlives the test; a dependency that does not
                # would be captured into the wider cache and handed to every
                # later test — and, being loop-bound, to tests whose loop it
                # no longer belongs to. The eager path rejects the same shape.
                _check_async_dep(
                    dep_name,
                    dep_val,
                    defn.name,
                    f"fixture '{defn.name}' (scope '{defn.scope.value}') "
                    f"cannot depend on the shorter-lived async fixture "
                    f"'{dep_name}' — its value is bound to one test's event "
                    f"loop and would be reused after that loop is gone",
                )
            if inspect.iscoroutine(dep_val):
                deps[dep_name] = await dep_val
            elif inspect.isasyncgen(dep_val):
                deps[dep_name] = await dep_val.__anext__()
                self._queue_async_teardown(defn, ctx, scope_refs, dep_val)
        return deps

    def _queue_async_teardown(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs | None,
        agen: Any,
    ) -> None:
        """Queue an async generator's post-``yield`` half at the right boundary.

        Function lifetime (*scope_refs* is ``None``) drains inside the test
        body, while the loop that created the generator is still open — an
        async generator can only be resumed on the loop it was started on, so
        anything later is a teardown on a dead loop.

        Wider lifetimes outlive that loop by definition, so they go to the
        session-lifetime manager, tagged with the boundary whose exit should
        dispose them. Module lifetime is keyed by module path; anything else
        falls back to session end.
        """
        if scope_refs is None and register_async_teardown(defn.name, agen):
            return
        if self._async_mgr is None:
            return
        boundary = ctx.module_path if defn.scope is FixtureScope.MODULE else None
        self._async_mgr.register_teardown(defn.name, agen, boundary=boundary)

    def _instantiate(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_teardowns: list[Callable[[], None]],
    ) -> Any:
        """Instantiate a fixture: resolve deps, call factory, track timing."""
        deps = _resolve_deps(
            self,
            defn.func,
            ctx,
            fn_name=defn.name,
            resolve_user=lambda n: self.resolve_fixture(n, ctx),
        )
        # Async fixtures may depend on other async fixtures; only reject in sync context
        if not defn.is_async:
            for dep_name, dep_val in deps.items():
                _reject_async_in_sync(dep_name, dep_val, defn.name)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            try:
                _start = time.monotonic()
                result = defn.func(**deps)
                outcome = _unpack_sync(result, defn.name)
                self._setup_times[_cache_key(defn)].append(
                    (time.monotonic() - _start) * 1000.0
                )
            except Exception as exc:
                raise FixtureSetupError(defn.name, exc) from exc

        match outcome:
            case HasTeardown(value=value, teardown=teardown_fn):

                def _timed_teardown(
                    _orig: Callable[[], None] = teardown_fn,
                    _name: str = _cache_key(defn),
                ) -> None:
                    _td_start = time.monotonic()
                    _orig()
                    self._teardown_times[_name].append(
                        (time.monotonic() - _td_start) * 1000.0
                    )

                scope_teardowns.append(_timed_teardown)
                return value
            case NoTeardown(value=value):
                return value
            case _:
                assert_never(outcome)

    # ── Built-in injection ───────────────────────────────────────────────

    def inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        meta: TestMeta,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
        session_scope: _Scope | None = None,
    ) -> Any:
        """Create and return a built-in fixture value, respecting its declared scope."""
        run_ctx = _test_run_context.get()
        _keep_tmp = run_ctx.keep_tmp
        _result_cell = run_ctx.result_cell

        effective_session_scope = session_scope or self._session_scope
        if impl_cls.scope == "session" and effective_session_scope is not None:
            return effective_session_scope.get_or_create(
                f"__builtin_{impl_cls.__name__}",
                lambda: impl_cls().create(
                    ctx=_BuiltinContext(
                        meta=meta,
                        inject_scope="session",
                        teardown_stack=effective_session_scope.teardowns,
                        plugin_registry=self._plugin_registry,
                        keep_tmp=_keep_tmp,
                        result_cell=_result_cell,
                    )
                ),
            )
        return impl_cls().create(
            ctx=_BuiltinContext(
                meta=meta,
                inject_scope=inject_scope,
                teardown_stack=teardown_stack,
                plugin_registry=self._plugin_registry,
                keep_tmp=_keep_tmp,
                result_cell=_result_cell,
            )
        )

    # ── Timing ───────────────────────────────────────────────────────────

    def get_fixture_timings(self) -> tuple[FixtureTiming, ...]:
        """Return per-fixture setup and teardown timing aggregates."""
        names = sorted(set(self._setup_times.keys()) | set(self._teardown_times.keys()))
        return tuple(
            FixtureTiming(
                name=n,
                total_setup_ms=float(sum(self._setup_times.get(n, []))),
                setup_count=len(self._setup_times.get(n, [])),
                total_teardown_ms=float(sum(self._teardown_times.get(n, []))),
                teardown_count=len(self._teardown_times.get(n, [])),
            )
            for n in names
        )
