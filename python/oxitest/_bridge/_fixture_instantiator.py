"""Fixture resolution and instantiation — extracted from FixtureSession."""

from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "FixtureInstantiator",
    "ScopeRefs",
    "_FixtureOutcome",
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
from typing import TYPE_CHECKING, Any

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
class _ResolutionContext:
    """Shared context threaded through fixture resolution."""

    module_path: str
    fn_teardowns: list[Callable[[], None]]
    resolving: frozenset[str]
    scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None]


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
class _FixtureOutcome:
    """Result of unpacking a fixture function call."""

    value: Any
    teardown: Callable[[], None] | None = None


def _unpack_sync(result: Any, name: str) -> _FixtureOutcome:
    """Unpack a sync fixture call: plain value or generator."""
    if inspect.isgenerator(result):
        value = next(result)

        def teardown(gen: Any = result, fixture_name: str = name) -> None:
            def _drain() -> None:
                with contextlib.suppress(StopIteration):
                    next(gen)

            safe_teardown(_drain, fixture_name, warn=_warn_teardown)

        return _FixtureOutcome(value, teardown)
    return _FixtureOutcome(result)


# ── FixtureInstantiator ──────────────────────────────────────────────────────


class FixtureInstantiator:
    """Resolves and creates fixture values. Stateless — scope refs passed in.

    The Instantiator never owns fixture scopes; callers supply scope information
    via a ``scope_callback`` that maps a ``FixtureDef`` to ``ScopeRefs | None``.
    ``None`` means function scope (no caching), ``ScopeRefs`` means shared scope.
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

        # Broad type fallback (Fixture[Any] / Fixture[object])
        if inner is Any or inner is object:
            defn = self._registry.get(param_name)
            if defn is None:
                raise FixtureNotFoundError(param_name)
            return True, self._resolve_by_source(
                defn, meta, fn_teardowns, resolve_user_fixture
            )

        # Unified type-based resolution — try type first
        try:
            defn = self._registry.resolve(inner, qualifier=param_name)
        except FixtureNotFoundError:
            defn = None

        # For Builtin/Plugin sources found by type, use direct instantiation
        if defn is not None and not isinstance(defn.source, ConftestSource):
            return True, self._resolve_by_source(
                defn, meta, fn_teardowns, resolve_user_fixture
            )

        # For ConftestSource: prefer name-based (preserves cycle detection),
        # fall back to type-resolved name, or raise if neither exists.
        resolve_name = (
            param_name
            if self._registry.get(param_name) is not None
            else defn.name
            if defn is not None
            else None
        )
        if resolve_name is None:
            raise FixtureNotFoundError(param_name)
        return True, resolve_user_fixture(resolve_name)

    def _resolve_by_source(
        self,
        defn: FixtureDef[Any],
        meta: TestMeta,
        fn_teardowns: list[Callable[[], None]],
        resolve_user_fixture: Callable[[str], Any],
    ) -> Any:
        """Dispatch instantiation based on the fixture's source variant.

        For ConftestSource, routes through ``resolve_user_fixture`` to preserve
        cycle detection and scope caching.  For PluginSource and BuiltinSource,
        creates the value directly (no cycle risk — they have no registry deps).
        """
        match defn.source:
            case ConftestSource():
                return resolve_user_fixture(defn.name)
            case PluginSource(provider=provider):
                value = provider.create(ctx=None)
                fn_teardowns.append(lambda v=value, p=provider: p.teardown(value=v))
                return value
            case BuiltinSource(impl_cls=impl_cls):
                return self.inject_builtin(impl_cls, meta, "function", fn_teardowns)

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
        if defn is None:
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
        scope_refs = ctx.scope_callback(defn)

        if scope_refs is not None:
            # Shared fixture — check cache first
            if defn.name in scope_refs.cache:
                if defn.is_async and self._async_mgr is not None:
                    self._async_mgr.was_used = True
                scope_refs.hits[defn.name] = scope_refs.hits.get(defn.name, 0) + 1
                return scope_refs.cache[defn.name]

            scope_refs.misses[defn.name] = scope_refs.misses.get(defn.name, 0) + 1

            if defn.is_async:
                return self._resolve_shared_async(defn, ctx, scope_refs)

            value = FrozenProxy(self._instantiate(defn, ctx, scope_refs.teardowns))
            scope_refs.cache[defn.name] = value
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
            self._setup_times[defn.name].append((time.monotonic() - _start) * 1000.0)

        proxy = FrozenProxy(value)
        scope_refs.cache[defn.name] = proxy
        return proxy

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
        _keep_tmp = run_ctx.keep_tmp if run_ctx else None
        _result_cell = run_ctx.result_cell if run_ctx else None

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

    def get_fixture_timings(self) -> list[FixtureTiming]:
        """Return per-fixture setup and teardown timing aggregates."""
        names = sorted(set(self._setup_times.keys()) | set(self._teardown_times.keys()))
        return [
            FixtureTiming(
                name=n,
                total_setup_ms=float(sum(self._setup_times.get(n, []))),
                setup_count=len(self._setup_times.get(n, [])),
                total_teardown_ms=float(sum(self._teardown_times.get(n, []))),
                teardown_count=len(self._teardown_times.get(n, [])),
            )
            for n in names
        ]
