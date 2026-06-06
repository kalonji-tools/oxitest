"""Fixture resolution and instantiation — extracted from FixtureSession."""

from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "FixtureInstantiator",
    "ScopeRefs",
    "_FixtureOutcome",
    "_check_async_dep",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
    "_resolve_deps",
    "_unpack_sync",
]

import inspect
import time
import warnings
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._errors import (
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
)
from oxitest._bridge._fixture_registry import FixtureDef, _fixture_inner_type
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._test_meta import TestMeta

if TYPE_CHECKING:
    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge._fixture_session import FixtureSession, _Scope
    from oxitest._bridge.plugin_loader import PluginRegistry


@dataclass(frozen=True, slots=True)
class ScopeRefs:
    """References to the scope a fixture should be cached/torn down in."""

    cache: dict[str, Any]
    teardowns: list[Callable[[], None]]
    hits: dict[str, int]
    misses: dict[str, int]


def _warn_teardown(name: str, exc: Exception, *, node_id: str = "") -> None:
    from oxitest._bridge._fixture_session import (
        FixtureTeardownWarning,
        _current_teardown_node_id,
    )

    effective_id = node_id or _current_teardown_node_id.get()
    if name and effective_id:
        msg = f"fixture '{name}' teardown failed during {effective_id}: {exc}"
    elif name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    warnings.warn(FixtureTeardownWarning(msg), stacklevel=2)


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
    session: FixtureInstantiator | FixtureSession,
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
    # Duck-type: FixtureInstantiator exposes resolve_param (public),
    # FixtureSession still uses _resolve_param (private) until Task 7 wires it.
    _resolve: Callable[..., tuple[bool, Any]] = getattr(  # ty: ignore[invalid-assignment]
        session, "resolve_param", getattr(session, "_resolve_param", None)
    )
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        resolved, value = _resolve(
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
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry
        self._async_mgr = async_mgr
        self._setup_times: dict[str, list[float]] = defaultdict(list)
        self._teardown_times: dict[str, list[float]] = defaultdict(list)

    # ── Parameter resolution ─────────────────────────────────────────────

    def resolve_param(
        self,
        param_name: str,
        hint: Any,
        meta: TestMeta,
        fn_teardowns: list[Callable[[], None]],
        resolve_user_fixture: Callable[[str], Any],
        proxy_session: Any = None,
    ) -> tuple[bool, Any]:
        """Resolve a single parameter by its type hint.

        Returns (resolved, value) where resolved=True means the value should be
        injected for this parameter. Returns (False, None) if the hint is not
        injectable (not Fixture[T], not bare Fixtures).

        proxy_session: if provided, passed to FixturesProxy as the session
        (the caller — typically FixtureSession — passes itself so that the proxy
        can call back into it for namespace/builtin resolution).
        """
        from oxitest._bridge.fixtures import Fixtures
        from oxitest._bridge.proxy_ns import FixturesProxy

        if hint is Fixtures:
            session: Any = proxy_session or self
            return True, FixturesProxy(
                session, meta.module_path, fn_teardowns, fn_name=meta.fn_name
            )  # type: ignore[arg-type]
        is_fx, inner = _fixture_inner_type(hint)
        if not is_fx:
            return False, None
        impl_cls = BuiltinFixture.for_type(inner)
        if impl_cls is not None:
            return True, self.inject_builtin(impl_cls, meta, "function", fn_teardowns)
        # Check plugin fixture providers (matched by type, not name)
        plugin_value = self._try_plugin_fixture(inner, fn_teardowns)
        if plugin_value is not None:
            return True, plugin_value
        return True, resolve_user_fixture(param_name)

    # ── Fixture resolution ───────────────────────────────────────────────

    def resolve_fixture(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
        scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None],
    ) -> Any:
        """Resolve a fixture by name.

        Raises FixtureCycleError if a cycle is detected, FixtureNotFoundError
        if the fixture is not registered.
        """
        if name in resolving:
            raise FixtureCycleError(name, set(resolving))
        defn = self._registry.get(name)
        if defn is None:
            raise FixtureNotFoundError(name)
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, resolving | {name}, scope_callback
        )

    def resolve_fixture_in_namespace(
        self,
        defn: FixtureDef[Any],
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None],
    ) -> Any:
        """Resolve a fixture definition found by namespace lookup.

        Bypasses cycle-detection entry guard (acceptable trade-off — self-referential
        namespace fixtures are nonsensical and unsupported).
        """
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, frozenset({name}), scope_callback
        )

    def _resolve_fixture_defn(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
        scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None],
    ) -> Any:
        """Internal resolution of a fixture definition, handling scope caching."""
        scope_refs = scope_callback(defn)

        if scope_refs is not None:
            # Shared fixture — check cache first
            if defn.name in scope_refs.cache:
                if defn.is_async and self._async_mgr is not None:
                    self._async_mgr.was_used = True
                scope_refs.hits[defn.name] = scope_refs.hits.get(defn.name, 0) + 1
                return scope_refs.cache[defn.name]

            scope_refs.misses[defn.name] = scope_refs.misses.get(defn.name, 0) + 1

            if defn.is_async:
                return self._resolve_shared_async(
                    defn,
                    module_path,
                    fn_teardowns,
                    resolving,
                    scope_refs,
                    scope_callback,
                )

            from oxitest._bridge.proxy import FrozenProxy

            value = FrozenProxy(
                self._instantiate(
                    defn,
                    module_path,
                    scope_refs.teardowns,
                    fn_teardowns,
                    resolving,
                    scope_callback,
                )
            )
            scope_refs.cache[defn.name] = value
            return value

        # Function scope — no caching
        return self._instantiate(
            defn, module_path, fn_teardowns, fn_teardowns, resolving, scope_callback
        )

    def _resolve_shared_async(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
        scope_refs: ScopeRefs,
        scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None],
    ) -> Any:
        """Eagerly resolve a shared async fixture on the session event loop."""
        from oxitest._bridge._fixture_session import _fixture_scope

        deps = _resolve_deps(
            self,
            defn.func,
            module_path,
            fn_teardowns=fn_teardowns,
            fn_name=defn.name,
            resolve_user=lambda n: self.resolve_fixture(
                n, module_path, fn_teardowns, resolving, scope_callback
            ),
            async_policy=_reject_nonshared_async,
        )

        with _fixture_scope(self, module_path, fn_teardowns):
            _start = time.monotonic()
            value = self._async_mgr.resolve(defn.func, deps)
            self._setup_times[defn.name].append((time.monotonic() - _start) * 1000.0)

        from oxitest._bridge.proxy import FrozenProxy

        proxy = FrozenProxy(value)
        scope_refs.cache[defn.name] = proxy
        return proxy

    def _instantiate(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        scope_teardowns: list[Callable[[], None]],
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
        scope_callback: Callable[[FixtureDef[Any]], ScopeRefs | None],
    ) -> Any:
        """Instantiate a fixture: resolve deps, call factory, track timing."""
        from oxitest._bridge._fixture_session import _fixture_scope

        deps = _resolve_deps(
            self,
            defn.func,
            module_path,
            fn_teardowns=fn_teardowns,
            fn_name=defn.name,
            resolve_user=lambda n: self.resolve_fixture(
                n, module_path, fn_teardowns, resolving, scope_callback
            ),
            async_policy=_reject_async_in_sync if not defn.is_async else None,
        )

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
        from oxitest._bridge._fixture_session import _test_run_context

        run_ctx = _test_run_context.get()
        _keep_tmp = run_ctx.keep_tmp if run_ctx else None
        _result_cell = run_ctx.result_cell if run_ctx else None

        if impl_cls.scope == "session" and session_scope is not None:
            return session_scope.get_or_create(
                f"__builtin_{impl_cls.__name__}",
                lambda: impl_cls().create(
                    _BuiltinContext(
                        meta=meta,
                        inject_scope="session",
                        teardown_stack=session_scope.teardowns,
                        plugin_registry=self._plugin_registry,
                        keep_tmp=_keep_tmp,
                        result_cell=_result_cell,
                    )
                ),
            )
        return impl_cls().create(
            _BuiltinContext(
                meta=meta,
                inject_scope=inject_scope,
                teardown_stack=teardown_stack,
                plugin_registry=self._plugin_registry,
                keep_tmp=_keep_tmp,
                result_cell=_result_cell,
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

    # ── Timing ───────────────────────────────────────────────────────────

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
