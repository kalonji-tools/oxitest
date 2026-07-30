from __future__ import annotations

__all__ = [
    "FixtureSession",
    "_Scope",
    "_SessionProtocol",
]

import asyncio
import inspect
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, Self

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._async_orchestrator import SharedAsyncManager
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._errors import (
    AsyncFixtureAccessError,
    BoundaryError,
    FixtureError,
    FixtureNotFoundError,
    FixtureTypeNotFoundError,
    UnannotatedFixtureParamError,
    UsageError,
)
from oxitest._bridge._fixture_context import (
    _current_teardown_node_id,
    _fixture_scope,
    _warn_teardown,
)
from oxitest._bridge._fixture_instantiator import (
    DispatchContext,
    FixtureInstantiator as _FixtureInstantiator,
    ScopeRefs,
    _ResolutionContext,
)
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
    PluginSource,
    _fixture_inner_type,
)
from oxitest._bridge._fixture_validator import FixtureValidator as _FixtureValidator
from oxitest._bridge._fixtures import Fixtures
from oxitest._bridge._helper_registry import HelperDef, HelperRegistry
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._read_fixtures import _fixtures_registry_var
from oxitest._bridge._read_helpers import _helpers_registry_var
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest._bridge.proxy_ns import FixturesProxy
from oxitest._bridge.result import CacheEntry, CacheStats, Diagnostic

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import (
        AsyncBackend,
        AsyncSession,
    )
    from oxitest._bridge.result import FixtureTiming


class _SessionProtocol(Protocol):
    """Structural protocol for objects that can provide fixtures to a test.

    `FixtureSession` satisfies this protocol, allowing `run_test` to treat
    session as always present without None guards. The null case is handled
    by constructing `FixtureSession([])` when no conftest is present.
    """

    @property
    def plugin_registry(self) -> PluginRegistry: ...

    @property
    def module_cache(self) -> ModuleCache: ...

    @property
    def registry(self) -> FixtureRegistry: ...

    @property
    def async_backend(self) -> AsyncBackend: ...

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        meta: TestMeta,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]: ...

    def get_fixture_by_name(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...

    def get_fixture_by_type(
        self,
        t: type,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool = True,
    ) -> Any: ...

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None: ...

    def inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        meta: TestMeta,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
    ) -> Any: ...

    def has_namespace(self, namespace: str) -> bool: ...

    def get_fixture_timings(self) -> tuple[FixtureTiming, ...]: ...


# ── FixtureSession ────────────────────────────────────────────────────────────


@dataclass
class _Scope:
    """A single fixture scope: a cache dict and its associated teardown stack."""

    cache: dict[str, Any] = field(default_factory=dict)
    teardowns: list[Callable[[], None]] = field(default_factory=list)
    hits: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    misses: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self.cache:
            self.cache[name] = factory()
            self.misses[name] += 1
        else:
            self.hits[name] += 1
        return self.cache[name]

    def drain(self) -> None:
        """Run teardowns in reverse, then clear the stack."""
        for fn in reversed(self.teardowns):
            safe_teardown(fn, warn=_warn_teardown)
        self.teardowns.clear()


class _TrackedTaskGroup:
    """Thin wrapper around ``asyncio.TaskGroup`` that records created tasks.

    Replaces the monkey-patch on ``tg.create_task`` with a proper wrapper so
    that type checkers do not see a method-assign violation.  Callers receive
    this object from the ``task_group`` built-in fixture and use it as though
    it were an ``asyncio.TaskGroup``.
    """

    def __init__(self, tg: Any, tasks: list[Any]) -> None:
        self._tg = tg
        self._tasks = tasks

    def create_task(
        self,
        coro: Any,
        *,
        name: str | None = None,
        context: Any = None,
    ) -> Any:
        t = self._tg.create_task(coro, name=name, context=context)
        self._tasks.append(t)
        return t

    async def __aenter__(self) -> Self:
        await self._tg.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> Any:
        return await self._tg.__aexit__(*args)


async def _task_group_factory() -> AsyncGenerator[_TrackedTaskGroup, None]:
    """Built-in async yield fixture providing a managed asyncio.TaskGroup.

    Tracks all tasks created via ``task_group.create_task()`` and cancels any
    that are still running when the test body returns, preventing hangs on
    teardown.  Yields a ``_TrackedTaskGroup`` wrapper rather than the raw
    ``asyncio.TaskGroup`` to avoid monkey-patching ``create_task``.
    """
    tasks: list[asyncio.Task[Any]] = []
    tg = asyncio.TaskGroup()
    tracked = _TrackedTaskGroup(tg, tasks)
    async with tracked:
        yield tracked
        for t in tasks:
            if not t.done():
                t.cancel()


def _collect_requested_names(
    hints: dict[str, Any], skip_names: frozenset[str]
) -> set[str]:
    """Return names of explicitly-requested fixtures from type hints."""
    names: set[str] = set()
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        is_fx, _inner = _fixture_inner_type(hint)
        if is_fx and param_name not in skip_names:
            names.add(param_name)
    return names


def _check_unannotated_params(
    fn: Callable[..., Any],
    hints: dict[str, Any],
    kwargs: dict[str, Any],
    skip_names: frozenset[str],
    registry: FixtureRegistry,
) -> None:
    """Raise UnannotatedFixtureParamError if a param matches a known fixture.

    Only raised when the parameter lacks a ``Fixture[T]`` annotation.
    """
    for param_name in inspect.signature(fn).parameters:
        if param_name in skip_names or param_name in kwargs:
            continue
        hint = hints.get(param_name)
        is_fx = _fixture_inner_type(hint)[0] if hint is not None else False
        if not is_fx and registry.get(param_name) is not None:
            raise UnannotatedFixtureParamError(
                param_name, getattr(fn, "__name__", repr(fn))
            )


class FixtureSession:
    """Manages fixture lifecycle for a single oxitest run.

    Owns four fixture scopes:

    - **function scope** (per-test teardown list, `fn_teardowns`) — default
      for all user-defined fixtures.
    - **module scope** (`_module_scopes`) — one `_Scope` per module path, for
      fixtures declared ``@oxi.fixture(lifetime="module")``; created on first
      use and drained at `end_module`.
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
        conftest_defs: list[FixtureDef] | FixtureRegistry,
        plugin_registry: PluginRegistry | None = None,
        async_backend: AsyncBackend | None = None,
    ) -> None:
        BuiltinFixture.ensure_registered()
        self._registry = FixtureRegistry()
        self._plugin_registry = plugin_registry or PluginRegistry()
        self._async_mgr = SharedAsyncManager(async_backend or AsyncioBackend())
        self._session_scope = _Scope()
        self._shared_scope = (
            _Scope()
        )  # shared=True fixtures — init once, drain at end_session
        # lifetime="module" fixtures — one scope per module path, created on
        # first use and popped+drained by end_module. Popping (rather than
        # clearing) keeps a long run from retaining one _Scope per module.
        self._module_scopes: dict[str, _Scope] = {}
        # lifetime="package" fixtures — one scope per *anchor directory*, not per
        # module. Every module in the anchor's subtree lands in the same bucket,
        # which is the guarantee the tier makes; the scheduler co-locates that
        # subtree onto one worker so it holds under parallel execution too.
        self._package_scopes: dict[str, _Scope] = {}
        self._has_wide_async: bool | None = None
        # Module scopes are discarded at end_module, taking their counters with
        # them, so cache stats are folded into these before the pop. Without
        # that, a run using only module-lifetime fixtures reports no cache
        # activity at all.
        self._module_hits: defaultdict[str, int] = defaultdict(int)
        self._module_misses: defaultdict[str, int] = defaultdict(int)
        # Package scopes get their own counters rather than folding into the
        # module ones: reporting package-scope cache activity as module-scope
        # would misattribute it in every stats readout.
        self._package_hits: defaultdict[str, int] = defaultdict(int)
        self._package_misses: defaultdict[str, int] = defaultdict(int)
        self._module_cache = ModuleCache()

        # ── Register all fixture sources into the unified registry ──
        # 1. Builtins (lowest priority)
        for fixture_type, impl_cls in BuiltinFixture.registered_types().items():
            scope = (
                FixtureScope.SESSION
                if getattr(impl_cls, "scope", "function") == "session"
                else FixtureScope.EACH
            )
            self._registry.register(
                FixtureDef(
                    name=impl_cls.__name__,
                    fixture_type=fixture_type,
                    scope=scope,
                    source=BuiltinSource(impl_cls=impl_cls),
                )
            )

        # 2. Plugin fixtures (medium priority)
        self._register_plugin_fixtures()

        # 3. Conftest fixtures (highest priority)
        # Support both new list[FixtureDef] API and legacy FixtureRegistry API
        if isinstance(conftest_defs, FixtureRegistry):
            # Legacy path: iterate all defs from the registry
            for name in conftest_defs:
                for defn in conftest_defs.all_defs(name):
                    self._registry.register(defn)
        else:
            for defn in conftest_defs:
                self._registry.register(defn)

        # Built-in task_group fixture (async yield fixture for managed TaskGroup)
        self._registry.register(
            FixtureDef(
                name="task_group",
                fixture_type=object,
                scope=FixtureScope.EACH,
                source=ConftestSource(
                    func=_task_group_factory, conftest_path="<builtin>"
                ),
                autouse=False,
                is_async=True,
            )
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

        self.diagnostics: list[Diagnostic] = []
        self._prev_diag_var = _diagnostic_collector_var.get(None)
        if self._prev_diag_var is None:
            _diagnostic_collector_var.set(self.diagnostics)

        self._prev_fixtures_var = _fixtures_registry_var.get(None)
        self._prev_helpers_var = _helpers_registry_var.get(None)
        # Only overwrite the fixtures contextvar if there is no outer
        # session already owning it (avoids clobbering during tests that
        # create temporary sessions).
        if self._prev_fixtures_var is None:
            _fixtures_registry_var.set(self._registry)

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        # When plugin registry is replaced (e.g. by Rust bridge after init),
        # re-register plugin fixtures into the unified registry and propagate
        # to the instantiator.
        if name == "_plugin_registry" and hasattr(self, "_instantiator"):
            self._instantiator.plugin_registry = value
            # Register any new plugin fixture providers into the unified registry
            self._register_plugin_fixtures()

    def _register_plugin_fixtures(self) -> None:
        """Register all fixtures from the current plugin registry."""
        for provider in getattr(self._plugin_registry, "fixture_providers", ()):
            provider_scope = getattr(provider, "scope", "each")
            provider_autouse = getattr(provider, "autouse", False)
            self._registry.register(
                FixtureDef(
                    name=provider.name,
                    fixture_type=provider.fixture_type,
                    scope=FixtureScope(provider_scope),
                    source=PluginSource(
                        provider=provider,
                        plugin_module=getattr(provider, "__module__", "<plugin>"),
                    ),
                    autouse=provider_autouse,
                )
            )

    def _register_plugin_helpers(self, helper_registry: Any) -> None:
        """Register all helpers from the current plugin registry."""
        for provider in getattr(self._plugin_registry, "helper_providers", ()):
            namespace = getattr(provider, "__module__", "<plugin>")
            helper_registry.register(
                HelperDef(
                    name=provider.name,
                    func=provider.helper,
                    source=PluginSource(
                        provider=provider,
                        plugin_module=namespace,
                    ),
                    namespace=namespace,
                )
            )

    def set_helper_registry(self, registry: Any) -> None:
        """Attach a helper registry, register plugin helpers, and update contextvar.

        Only overwrites the contextvar when no outer session already owns it,
        preventing temporary sessions created during tests from clobbering the
        real session's helpers.
        """
        self._register_plugin_helpers(registry)
        self._helper_registry = registry
        if self._prev_helpers_var is None:
            _helpers_registry_var.set(registry)

    @staticmethod
    def _anchor_of(defn: FixtureDef) -> str:
        """The anchor directory a package-lifetime fixture is bound to.

        Only :class:`ModuleSource` carries an anchor. Any other source variant
        reaching package scope is a framework bug, not user error — the
        decorator is the only way to declare the tier, and it always produces a
        ``ModuleSource``.
        """
        source = defn.source
        if not isinstance(source, ModuleSource):
            msg = (
                f"fixture {defn.name!r} has package lifetime but a "
                f"{type(source).__name__} source, which carries no anchor "
                f"package. This is an oxitest bug — please report it."
            )
            raise UsageError(msg)
        return source.anchor_package_path

    def _scope_for(self, defn: FixtureDef, module_path: str) -> ScopeRefs | None:
        """Map a fixture def to its scope refs. None = function scope.

        *module_path* selects the bucket for module-lifetime fixtures; it is
        ignored for every other scope.
        """
        if defn.scope is FixtureScope.MODULE:
            # Not setdefault(): its default arg is evaluated on every call, so
            # each cache hit would build and discard a _Scope (two dicts, a
            # list, and two defaultdicts) on a per-resolution hot path.
            s = self._module_scopes.get(module_path)
            if s is None:
                s = self._module_scopes[module_path] = _Scope()
            return ScopeRefs(s.cache, s.teardowns, s.hits, s.misses)
        if defn.scope is FixtureScope.PACKAGE:
            # Keyed on the anchor directory, ignoring module_path entirely: every
            # module in the anchor's subtree must land in the same bucket, which
            # is the whole point of the tier. Keying on module_path here would
            # make package lifetime indistinguishable from module lifetime.
            anchor = self._anchor_of(defn)
            s = self._package_scopes.get(anchor)
            if s is None:
                s = self._package_scopes[anchor] = _Scope()
            return ScopeRefs(s.cache, s.teardowns, s.hits, s.misses)
        if defn.scope is FixtureScope.SESSION:
            # One bucket for the whole process, ignoring both module_path and
            # anchor. session is the tier that does not constrain the scheduler
            # (ADR-0009 Rule 2), so its boundary is the worker process itself,
            # not any directory — which is exactly why it cannot be a run-wide
            # singleton. Drained by end_session.
            #
            # This branch is what makes a *user-declared* lifetime="session"
            # fixture reach the session scope. Builtins arrive there by a
            # different route (the FixtureScope.SESSION mapping applied when
            # registering `impl_cls`), so their working behaviour said nothing
            # about this path; without this branch a declared session fixture
            # fell through to function scope and was rebuilt per test.
            scope = self._session_scope
            return ScopeRefs(scope.cache, scope.teardowns, scope.hits, scope.misses)
        if defn.shared:
            s = self._shared_scope
            return ScopeRefs(s.cache, s.teardowns, s.hits, s.misses)
        return None

    # ── Async delegation properties ─────────────────────────────────────────

    @property
    def async_backend(self) -> AsyncBackend:
        return self._async_mgr.backend

    @async_backend.setter
    def async_backend(self, value: AsyncBackend) -> None:
        self._async_mgr.cleanup()
        self._async_mgr = SharedAsyncManager(value)
        self._instantiator.async_mgr = self._async_mgr

    @property
    def _shared_session(self) -> AsyncSession | None:
        return self._async_mgr.session

    @property
    def _used_shared_async(self) -> bool:
        return self._async_mgr.was_used

    def has_wide_async_fixtures(self) -> bool:
        """Whether any registered async fixture outlives a single test.

        Drives promotion of async test bodies onto the shared loop. The check
        is *visibility*, not use: ``fx.<ns>.<name>`` resolves lazily inside the
        body, long after the strategy was chosen, so waiting to find out
        whether a test actually touched one is waiting too long.

        Deliberately conservative — a test that could reach such a fixture but
        never does is still promoted. The alternative is a value built on a
        per-test loop and cached past that loop's death, which is the dominant
        failure mode of wider-than-test async fixtures across every framework
        surveyed in #1739.

        Computed once. Registration finishes before the first test runs, so
        the answer cannot change mid-run — and rebuilding the registry's
        tuple on every async test to re-derive a constant is pure waste.
        """
        if self._has_wide_async is None:
            self._has_wide_async = any(
                defn.is_async and defn.scope is not FixtureScope.EACH
                for defn in self._registry.all()
            )
        return self._has_wide_async

    def ensure_async_session(self) -> AsyncSession | None:
        """Acquire the shared async session up front, for promoted tests."""
        return self._async_mgr.ensure_session()

    @property
    def plugin_registry(self) -> PluginRegistry:
        """Read-only access to the plugin registry."""
        return self._plugin_registry

    @property
    def module_cache(self) -> ModuleCache:
        """Read-only access to the module cache."""
        return self._module_cache

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def end_module(self, module_path: str) -> None:
        """Dispose everything scoped to *module_path*.

        Drains before evicting so a module-lifetime teardown can still touch
        the module it was defined in.
        """
        # Async generators first: their post-yield half may touch the sync
        # values below, and both belong to the module that is ending.
        self._async_mgr.drain_boundary(module_path)
        scope = self._module_scopes.pop(module_path, None)
        if scope is not None:
            for name, count in scope.hits.items():
                self._module_hits[name] += count
            for name, count in scope.misses.items():
                self._module_misses[name] += count
            # No single test owns a module-scope teardown, so the per-test node
            # id is empty here. Name the module instead — otherwise a failure
            # reports only the fixture name, leaving the user to guess which
            # module it came from.
            token = _current_teardown_node_id.set(module_path)
            try:
                scope.drain()
            finally:
                _current_teardown_node_id.reset(token)
        self._module_cache.evict(module_path)

    def end_package(self, package_path: str) -> None:
        """Dispose everything scoped to *package_path*.

        Peer to :meth:`end_module` one tier up. The seam exists because package
        disposal cannot ride on ``end_session``: a serial run uses one session
        for the whole run, so the session drain fires long after the package's
        last test.

        Fires once per package boundary, after every module in that package and
        its descendants has had :meth:`end_module`. The serial path drives this
        from the group loop; under parallel execution the scheduler co-locates
        the package's subtree onto one worker so the boundary is a single task.
        """
        # Async generators first, for the same reason as end_module: their
        # post-yield half may touch the sync values below.
        self._async_mgr.drain_boundary(package_path)
        scope = self._package_scopes.pop(package_path, None)
        if scope is None:
            # Every group fires end_package, including ones whose package
            # fixtures were never requested. Nothing to drain is normal.
            return
        for name, count in scope.hits.items():
            self._package_hits[name] += count
        for name, count in scope.misses.items():
            self._package_misses[name] += count
        # No single test owns a package-scope teardown, so name the package
        # instead — otherwise a failure reports only the fixture name and the
        # user has to guess which boundary it came from.
        token = _current_teardown_node_id.set(package_path)
        try:
            scope.drain()
        finally:
            _current_teardown_node_id.reset(token)

    def end_session(self) -> None:
        # Tear down shared async fixtures first (reverse order), then sync scopes.
        self._async_mgr.cleanup()
        # Any package scope still held is drained here as a backstop. The serial
        # path pops each one at end_package, so this normally finds nothing; a
        # worker never calls end_package at all, because its session covers
        # exactly one task and the coordinator co-locates a package's subtree
        # into that one task. Without this, every package-lifetime teardown
        # would silently never run under parallel execution.
        for package_path in list(self._package_scopes):
            self.end_package(package_path)
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()
        # Only restore contextvars if this session was the one that set them
        # (i.e., _prev was None, meaning we were the outermost session).
        prev_fx = getattr(self, "_prev_fixtures_var", None)
        prev_hlp = getattr(self, "_prev_helpers_var", None)
        prev_diag = getattr(self, "_prev_diag_var", None)
        if prev_fx is None:
            _fixtures_registry_var.set(None)
        if prev_hlp is None:
            _helpers_registry_var.set(None)
        if prev_diag is None:
            _diagnostic_collector_var.set(None)

    def get_cache_stats(self) -> CacheStats:
        """Return fixture cache hit/miss statistics across every cached tier.

        Covers ``shared=True`` fixtures and ``lifetime="module"`` ones. Module
        counters come from ``_module_hits`` / ``_module_misses`` rather than
        the live scopes, which ``end_module`` has already discarded by the time
        anything reads stats.
        """
        s = self._shared_scope
        # update(), not a dict merge: a name present in both tiers must sum,
        # not have one tier's count silently replace the other's.
        hits: Counter[str] = Counter(s.hits)
        hits.update(self._module_hits)
        misses: Counter[str] = Counter(s.misses)
        misses.update(self._module_misses)
        names = sorted(set(hits) | set(misses))
        return CacheStats(
            total_hits=sum(hits.values()),
            total_misses=sum(misses.values()),
            breakdown=tuple(
                CacheEntry(
                    name=n,
                    hits=hits.get(n, 0),
                    misses=misses.get(n, 0),
                )
                for n in names
            ),
        )

    @property
    def registry(self) -> FixtureRegistry:
        """Read-only access to the fixture registry."""
        return self._registry

    @property
    def helper_registry(self) -> HelperRegistry:
        """Read-only access to the helper registry."""
        return self._helper_registry

    def shared_fixture_names(self) -> tuple[str, ...]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return self._registry.shared_names()

    def shared_fixture_groups(self) -> tuple[tuple[str, ...], ...]:
        """Return connected components of shared fixture dependencies."""
        return self._registry.shared_fixture_groups()

    def registered_fixture_names(self) -> tuple[str, ...]:
        """Return all fixture names known to the registry."""
        return tuple(self._registry)

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

    def get_fixture_timings(self) -> tuple[FixtureTiming, ...]:
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

    def inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        meta: TestMeta,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
    ) -> Any:
        """Public accessor for _inject_builtin (used by proxy_ns)."""
        return self._inject_builtin(impl_cls, meta, inject_scope, teardown_stack)

    def has_namespace(self, namespace: str) -> bool:
        """Return True if any registered fixture belongs to the given namespace."""
        return self._registry.has_namespace(namespace)

    def fixture_lookup_error(
        self, name: str, namespace: str, module_path: str
    ) -> FixtureError:
        """The right error for a failed *visible* lookup: boundary or not-found.

        The error type is a function of the **segment** alone, never the leaf:

        - segment reachable from here → ``FixtureNotFoundError``. The access is
          legal; the name is wrong.
        - segment unreachable but declared somewhere → ``BoundaryError``. Stated
          first even when the leaf is also misspelled, because the boundary
          statement is true either way and a leaf-first message implies that
          fixing the spelling makes the access work.
        - segment unknown anywhere → ``FixtureNotFoundError``.

        "Reachable" means *reachable under B1*, which is why the first branch
        asks :meth:`FixtureRegistry.has_visible_anchor` rather than anything
        broader. Unanchored defs are exempt from B1 and report visible from
        everywhere, and a namespace may hold both kinds at once: the registrar
        only rejects a repeated ``(namespace, name)`` pair, so a conftest
        ``api.conn`` and a ``tests/api/__fixtures__.py`` declaring ``api.other``
        coexist. Counting the conftest def as evidence of reachability would
        make this branch unconditionally true for that namespace and strand
        every genuine cross-boundary access on ``FixtureNotFoundError`` — the
        go-hunt-for-a-typo-in-a-correctly-spelled-name failure ``BoundaryError``
        exists to remove.

        A namespace built only from conftest ``Fixtures()`` instances reports no
        anchors and therefore never reaches the ``BoundaryError`` branch — the
        legacy API is exempt from B1 until #1720 retires it (#1760).

        The guarantee is scoped to directory anchors. Inline declarations are
        registered on module import, so their presence in the full catalog
        depends on worker assignment, ``-k`` selection, and import order; a
        ``BoundaryError`` for one would be a diagnostic that changes with the
        schedule. They fall through to ``FixtureNotFoundError``, whose hint
        names the inline cap unconditionally (#1759).
        """
        if self._registry.has_visible_anchor(namespace, module_path):
            return FixtureNotFoundError(name, namespace=namespace)
        anchors = self._registry.namespace_anchors(namespace)
        if not anchors:
            return FixtureNotFoundError(name, namespace=namespace)
        defn = self._registry.get_in_namespace(name, namespace)
        anchor = (defn.anchor if defn is not None else None) or anchors[0]
        return BoundaryError(
            name, namespace, anchor, module_path, leaf_exists=defn is not None
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
            requested_names = _collect_requested_names(hints, skip_names)

            # Autouse: run for side effects; value NOT injected unless
            # explicitly requested
            for defn in self._registry.get_autouse():
                if defn.name not in requested_names:
                    self.get_fixture_by_name(defn.name, meta.module_path, fn_teardowns)

            # Resolve Fixture[T]-annotated parameters
            kwargs: dict[str, Any] = {}
            for param_name, hint in hints.items():
                if param_name == "return":
                    continue
                if param_name in skip_names:
                    continue
                if hint is Fixtures:
                    kwargs[param_name] = FixturesProxy(
                        self,
                        meta.module_path,
                        fn_teardowns,
                        fn_name=meta.fn_name,
                        # A sync test cannot await, so the proxy rejects async
                        # fixtures at access rather than handing back a
                        # coroutine nothing will ever await (ADR-0006).
                        test_is_async=inspect.iscoroutinefunction(fn),
                    )
                    continue
                resolved, value = self._instantiator.resolve_param(
                    param_name,
                    hint,
                    meta,
                    fn_teardowns=fn_teardowns,
                    resolve_user_fixture=lambda n: self.get_fixture_by_name(
                        n, meta.module_path, fn_teardowns
                    ),
                )
                if resolved:
                    kwargs[param_name] = value

            _check_unannotated_params(fn, hints, kwargs, skip_names, self._registry)
            return kwargs, fn_teardowns

    def get_fixture_by_name(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        ctx = _ResolutionContext(
            module_path, fn_teardowns, frozenset(), self._scope_for, module_path
        )
        return self._instantiator.resolve_fixture(name, ctx)

    def get_fixture_by_type(
        self,
        t: type,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        """Resolve a fixture by its public type through the unified registry.

        Handles any registered @injectable — builtin (via BuiltinFixture registry),
        plugin (via FixtureProvider), or conftest fixture registered by return type.
        Raises ``FixtureNotFoundError`` if ``t`` is not resolvable.

        Note: ``TestContext`` resolved via this method will have empty ``name``,
        ``node_id``, and ``marks`` — no test identity is available outside a test.
        """
        try:
            defn = self._registry.resolve(t, qualifier=t.__name__)
        except FixtureNotFoundError:
            raise FixtureTypeNotFoundError(t.__name__) from None
        meta = TestMeta(module_path=module_path, fn_name="", node_id="")
        ctx = DispatchContext(
            meta=meta,
            fn_teardowns=fn_teardowns,
            resolve_user_fixture=lambda n: self.get_fixture_by_name(
                n, module_path, fn_teardowns
            ),
        )
        return self._instantiator.resolve_by_source(defn, ctx)

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool = True,
    ) -> Any:
        defn = self._registry.get_visible_in_namespace(name, namespace, module_path)
        if defn is None:
            raise self.fixture_lookup_error(name, namespace, module_path)
        ctx = _ResolutionContext(
            module_path, fn_teardowns, frozenset(), self._scope_for, module_path
        )
        if defn.is_async:
            # ADR-0006's illegal cell, on the proxy path. Raised here rather
            # than on await because a sync test has no loop to await on — the
            # mistake is the access itself.
            if not test_is_async:
                raise AsyncFixtureAccessError(name, namespace, defn.scope.value)
            return self._instantiator.resolve_async_in_namespace(defn, ctx)
        return self._instantiator.resolve_fixture_in_namespace(defn, name, ctx)

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return self._registry.get_namespace_for_func(name, func)
