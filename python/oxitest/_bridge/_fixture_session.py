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
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Protocol, Self

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._async_orchestrator import SharedAsyncManager
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._cwd_guard import report_and_repair
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
    _callback_name,
    _current_teardown_node_id,
    _fixture_scope,
    _in_teardown,
    _warn_callback_teardown,
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
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge._read_fixtures import _fixtures_registry_var
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
        test_is_async: bool,
    ) -> Any: ...

    def get_fixture_shortcut(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool,
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
        """Run teardowns in reverse, then empty the scope.

        The cache is cleared alongside the stack, and that pairing is
        load-bearing (#1777). ``_shared_scope`` and ``_session_scope`` are
        drained *in place* rather than popped like the module and package
        buckets, so anything left in ``cache`` outlives the teardown that was
        just run. That could not bite while a worker built a session per task —
        the whole scope died with it — but a session that spans task groups
        would hand the next one a cached value whose teardown has already
        fired, and whose replacement teardown was cleared with the stack. For
        ``TempDirFactory`` that means every temp dir created after a worker's
        first task group leaks, silently.

        ``hits``/``misses`` deliberately survive: they are cumulative counters
        that ``get_cache_stats`` reports, not scope contents.
        """
        # This one loop is the drain for every tier wider than function —
        # module, package, shared, session and process — so marking the
        # teardown window here covers all of them, and covers any tier added
        # later by construction (#1952).
        #
        # The list itself has the same live-append shape as
        # executor._run_teardowns, and is deliberately left unguarded against
        # it: a callback appended during this loop is skipped and then cleared.
        #
        # A public route to this list now exists, which it did not when this
        # comment was written. #1958 binds a wide-lifetime fixture's
        # dependencies to its own scope, so `ctx.addfinalizer` called from
        # inside such a fixture's teardown appends *here* rather than to an
        # fn_teardowns list. (`fx` is still not injectable into a fixture —
        # measured, not assumed: it raises "missing 1 required positional
        # argument".)
        #
        # This comment previously said to fix that the moment a route was
        # found. Keeping warn-and-drop instead is a decision, not an oversight:
        # #1952 settled the semantics — the loss is made audible and the list
        # is not changed — and guarding only the wider tiers would make the
        # process tier behave differently from the function tier for the
        # identical user mistake. The `_in_teardown` token below is what makes
        # the loss audible, and it covers every tier by construction.
        token = _in_teardown.set(True)
        try:
            for fn in reversed(self.teardowns):
                safe_teardown(fn, _callback_name(fn), warn=_warn_callback_teardown)
            # No per-test result exists at this boundary — the drain belongs to
            # a scope, not to any one test — so this reports and repairs but
            # cannot fail anything, unlike the function tier's check (#1957).
            report_and_repair("a fixture teardown at a lifetime wider than function")
        finally:
            _in_teardown.reset(token)
        self.teardowns.clear()
        self.cache.clear()


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

    Owns six fixture scopes:

    - **function scope** (`_function_scope`) — default for all user-defined
      fixtures; one `_Scope` per test, created by `resolve_for_test` and
      disposed at the test boundary, so every access route within one test
      observes the same instance (#1775). Its teardown list is the per-test
      `fn_teardowns` list drained by the executor.
    - **module scope** (`_module_scopes`) — one `_Scope` per module path, for
      fixtures declared ``@oxi.fixture(lifetime="module")``; created on first
      use and drained at `end_module`.
    - **shared scope** (`_shared_scope`) — for fixtures declared with
      ``shared=True``; initialised once and torn down at `end_task`.
    - **session scope** (`_session_scope`) — for built-in session-lifetime
      fixtures such as `TempDirFactory`; drained at `end_task`.
    - **process scope** (`_process_scope`) — for fixtures declared
      ``@oxi.fixture(lifetime="process")``, the tier #1777 makes genuinely
      per-process; drained at `end_process`. Separate from the builtins' bucket
      on purpose: hoisting `TempDirFactory` here would retain every temp dir a
      worker ever made until the process exits.

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
        # lifetime="process" user fixtures (#1777). Distinct from
        # _session_scope, which stays the builtins' bucket, because the two
        # drain at different boundaries: this one at end_process, that one at
        # end_task.
        self._process_scope = _Scope()
        self._shared_scope = (
            _Scope()
        )  # shared=True fixtures — init once, drain at end_task
        # lifetime="module" fixtures — one scope per module path, created on
        # first use and popped+drained by end_module. Popping (rather than
        # clearing) keeps a long run from retaining one _Scope per module.
        self._module_scopes: dict[str, _Scope] = {}
        # lifetime="package" fixtures — one scope per *anchor directory*, not per
        # module. Every module in the anchor's subtree lands in the same bucket,
        # which is the guarantee the tier makes; the scheduler co-locates that
        # subtree onto one worker so it holds under parallel execution too.
        self._package_scopes: dict[str, _Scope] = {}
        # lifetime="function" fixtures — one scope per *test* (#1775), created
        # by resolve_for_test and disposed by a teardown closure at the test
        # boundary. None whenever no test is active, which keeps direct
        # resolution calls outside a test uncached (their historical
        # behaviour). Its teardown list IS the per-test fn_teardowns list, so
        # the executor's existing drain stays the single teardown authority.
        self._function_scope: _Scope | None = None
        self._has_wide_async: bool | None = None
        # Anchor directories of the activated plugins whose __fixtures__.py was
        # registered into this session. Read back during collection to seed the
        # per-directory dedupe set, so a plugin vendored under `testpaths` is
        # not registered a second time as a user package (#1717).
        self._plugin_anchor_dirs: list[str] = []
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
        # Function-tier (per-test) counters, folded at each test's dispose the
        # same way module/package counters fold at their boundary — the scope
        # itself dies with the test, so without the fold the data is recorded
        # and then discarded. Deliberately NOT merged into get_cache_stats():
        # the summary line's absence for uncached-only runs is pinned by an
        # integration test, and a single-access function fixture counting as a
        # cache "miss" would skew the reported hit rate. Surfacing these is a
        # separate reporting decision, same standing as _package_hits above.
        self._function_hits: defaultdict[str, int] = defaultdict(int)
        self._function_misses: defaultdict[str, int] = defaultdict(int)
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
            process_scope=self._process_scope,
        )
        self._validator = _FixtureValidator(
            self._registry, self._plugin_registry, self._module_cache
        )

        self.diagnostics: list[Diagnostic] = []
        self._prev_diag_var = _diagnostic_collector_var.get(None)
        if self._prev_diag_var is None:
            _diagnostic_collector_var.set(self.diagnostics)

        self._prev_fixtures_var = _fixtures_registry_var.get(None)
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

    def record_plugin_anchor(self, anchor_dir: str) -> None:
        """Remember that *anchor_dir* was registered as a plugin fixture home.

        Called once per activated plugin package during session init, before
        collection. The collection walk reads these back so it can skip a
        directory it would otherwise register a second time — as an anchored
        user package under the same derived namespace (#1717).
        """
        self._plugin_anchor_dirs.append(anchor_dir)

    def plugin_anchor_dirs(self) -> tuple[str, ...]:
        """Anchor directories of every plugin fixture home in this session."""
        return tuple(self._plugin_anchor_dirs)

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
        """Map a fixture def to its scope refs. None = uncached (no active test).

        *module_path* selects the bucket for module-lifetime fixtures; it is
        ignored for every other scope. The function tier returns the per-test
        scope (``per_test=True``) while a test is active, ``None`` otherwise.
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
        if defn.scope in (FixtureScope.PROCESS, FixtureScope.SESSION):
            # One bucket for the whole process, ignoring both module_path and
            # anchor. These are the tiers that do not constrain the scheduler
            # (ADR-0009 Rule 2), so their boundary is the process itself, not
            # any directory — which is exactly why neither can be a run-wide
            # singleton.
            #
            # Two buckets share this branch but are NOT interchangeable: they
            # drain at different boundaries (#1777). PROCESS drains at
            # end_process and is where a *user-declared* lifetime="process"
            # fixture lands. SESSION drains at end_task and is the builtins'
            # bucket (`_TempDirFactoryFixture`), kept on the narrower rung so a
            # worker's temp dirs are released at its task boundary instead of
            # accumulating for the life of the process. Before #1777 both
            # routes shared one bucket, and that is precisely how the user tier
            # inherited the builtins' per-task boundary.
            scope = (
                self._process_scope
                if defn.scope is FixtureScope.PROCESS
                else self._session_scope
            )
            return ScopeRefs(scope.cache, scope.teardowns, scope.hits, scope.misses)
        # Function tier (#1775). A per-test scope exists only while a test is
        # being resolved or run; every access route during that window — the
        # autouse pass, Fixture[T] injection, fx. proxy access — lands in the
        # same cache, which is what makes "once per test" hold. Outside a
        # test there is no boundary that could dispose a cache, so direct
        # resolution stays uncached.
        scope = self._function_scope
        if scope is None:
            return None
        return ScopeRefs(
            scope.cache, scope.teardowns, scope.hits, scope.misses, per_test=True
        )

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

    def _anchors_ending_with(self, package_path: str) -> list[str]:
        """Live package anchors disposed by *package_path*'s boundary, innermost first.

        The subtree, not just the anchor itself. A package may contain a
        *nested* declaring package, and the scheduler merges both into one task
        group under the outermost anchor (``outermost_declaring_ancestor``), so
        the boundary that ends the outer one ends every inner one with it — the
        outer fixture spans the whole subtree, which is what made it indivisible
        in the first place.

        Innermost first, so a narrower value is disposed before the wider one it
        is allowed to depend on (B1 lets a descendant resolve an ancestor's
        fixture). Draining outermost-first would hand an inner teardown a value
        that had already been torn down.

        Containment is component-wise via :meth:`~pathlib.PurePath.is_relative_to`:
        ``tests/api2`` starts with the string ``tests/api`` but is not inside it,
        and string-prefix matching here would dispose an unrelated package early.
        """
        anchor_root = PurePath(package_path)
        live = [
            anchor
            for anchor in self._package_scopes
            if PurePath(anchor).is_relative_to(anchor_root)
        ]
        live.sort(key=lambda anchor: len(PurePath(anchor).parts), reverse=True)
        return live

    def end_package(self, package_path: str) -> None:
        """Dispose everything scoped to *package_path* and to packages beneath it.

        Peer to :meth:`end_module` one tier up. The seam exists because package
        disposal cannot ride on ``end_task``: a serial run uses one session
        for the whole run, so the session drain fires long after the package's
        last test.

        *package_path* is the **declaring anchor directory** — the key
        ``_package_scopes`` uses; a module path can never match (#1839).

        Fires once per package boundary, after every module in that package and
        its descendants has had :meth:`end_module`. The serial path drives this
        from the group loop, using the anchor its task group was merged under;
        under parallel execution the scheduler co-locates the package's subtree
        onto one worker, whose whole session is that one task, so the worker
        leaves the drain to ``end_task``.
        """
        for anchor in self._anchors_ending_with(package_path):
            self._dispose_package(anchor)

    def _dispose_package(self, anchor: str) -> None:
        """Drain the one package scope held at *anchor*."""
        # Async generators first, for the same reason as end_module: their
        # post-yield half may touch the sync values below.
        self._async_mgr.drain_boundary(anchor)
        scope = self._package_scopes.pop(anchor, None)
        if scope is None:
            # An anchored group whose package fixtures were never actually
            # requested built no scope. Nothing to drain is normal.
            return
        for name, count in scope.hits.items():
            self._package_hits[name] += count
        for name, count in scope.misses.items():
            self._package_misses[name] += count
        # No single test owns a package-scope teardown, so name the package
        # instead — otherwise a failure reports only the fixture name and the
        # user has to guess which boundary it came from.
        token = _current_teardown_node_id.set(anchor)
        try:
            scope.drain()
        finally:
            _current_teardown_node_id.reset(token)

    def end_task(self) -> None:
        """Dispose everything whose lifetime ends with this task group.

        The wider half of the old ``end_session`` (#1777). A worker calls this
        once per task it pops off the scheduler; the serial path calls it once,
        because a serial run's task group is the whole run.

        Peer to :meth:`end_process`, which owns the rungs that outlive a task.
        The pair must fire in that order — task first, then process — because a
        process-lifetime value may depend on nothing narrower, but the reverse
        is exactly what the tiers permit.
        """
        # Tear down task-lifetime async fixtures first (reverse order), then
        # sync scopes. `drain_task`, not `cleanup`: the process tier's async
        # teardowns and the event loop itself both outlive this task (#1777).
        self._async_mgr.drain_task()
        # Any package scope still held is drained here. This is not defence in
        # depth on the worker path — it is the only drain there, because a
        # worker never calls end_package at all: its session covers exactly one
        # task and the coordinator co-locates a package's subtree into that one
        # task. Without this, every package-lifetime teardown would silently
        # never run under parallel execution.
        #
        # On the coordinator path it is a genuine backstop and normally finds
        # nothing, because the serial loop pops each anchor at its boundary. A
        # regression there shows up here as late teardown, not missing (#1839).
        for package_path in list(self._package_scopes):
            self.end_package(package_path)
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()

    def end_process(self) -> None:
        """Dispose everything whose lifetime ends with this *process* (#1777).

        Called once per process — from the worker's ``main()`` ``finally`` and
        once by the coordinator after every execution phase — rather than once
        per task group.

        Restoring the contextvars belongs here rather than in :meth:`end_task`
        because both vars are process-global: the worker sets the diagnostic
        collector once in ``main()`` for the life of its pipe, and every
        collector downstream defers to an already-active one.
        """
        # Async first, mirroring end_task: a process-lifetime async teardown
        # may touch the sync values below, and the event loop it needs is
        # closed by this same call (#1777).
        self._async_mgr.cleanup()
        self._process_scope.drain()
        # Only restore contextvars if this session was the one that set them
        # (i.e., _prev was None, meaning we were the outermost session).
        prev_fx = getattr(self, "_prev_fixtures_var", None)
        prev_diag = getattr(self, "_prev_diag_var", None)
        if prev_fx is None:
            _fixtures_registry_var.set(None)
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

    def shared_fixture_names(self) -> tuple[str, ...]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return self._registry.shared_names()

    def shared_fixture_groups(self) -> tuple[tuple[str, ...], ...]:
        """Return connected components of shared fixture dependencies."""
        return self._registry.shared_fixture_groups()

    def process_lifetime_fixture_names(self) -> tuple[str, ...]:
        """Return sorted names of fixtures declared ``lifetime="process"``."""
        return self._registry.process_lifetime_names()

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

    def _fold_function_stats(self, scope: _Scope) -> None:
        """Fold a dying per-test scope's counters into the session aggregates.

        Mirrors what ``end_module`` / ``end_package`` do at their boundaries:
        the scope's counters die with it, and unfolded they were recorded for
        nothing.
        """
        for fixture_name, count in scope.hits.items():
            self._function_hits[fixture_name] += count
        for fixture_name, count in scope.misses.items():
            self._function_misses[fixture_name] += count

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
        # The per-test function scope (#1775). Its teardown list IS
        # fn_teardowns, so a build registers its teardown exactly where the
        # executor already drains — once per test, in reverse build order.
        # Unconditional replacement is what guarantees no instance survives
        # into the next test even when an error path skips the drain below.
        function_scope = _Scope(teardowns=fn_teardowns)
        self._function_scope = function_scope

        def _dispose_function_scope() -> None:
            self._fold_function_stats(function_scope)
            function_scope.cache.clear()
            if self._function_scope is function_scope:
                self._function_scope = None

        # Appended first: the executor drains fn_teardowns in reverse, so the
        # cache is dropped LAST — after every fixture teardown has run.
        fn_teardowns.append(_dispose_function_scope)
        self._async_mgr.was_used = False  # reset per-test
        with _fixture_scope(self, meta.module_path, fn_teardowns):
            hints = _get_hints(fn)
            requested_names = _collect_requested_names(hints, skip_names)

            # Autouse: run for side effects; value NOT injected unless
            # explicitly requested
            for defn in self._registry.get_autouse(meta.module_path):
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
                        # The whole meta, not module_path + fn_name: a builtin
                        # resolved through this proxy rebuilds its own view
                        # downstream, and the two fields this used to pass are
                        # the two that are NOT the test's identity (#1874).
                        meta,
                        fn_teardowns,
                        # A sync test cannot await, so the proxy rejects async
                        # fixtures at access rather than handing back a
                        # coroutine nothing will ever await (ADR-0006).
                        test_is_async=inspect.iscoroutinefunction(fn),
                    )
                    continue
                resolved, value = self._instantiator.resolve_param(
                    param_name,
                    hint,
                    DispatchContext(
                        meta=meta,
                        fn_teardowns=fn_teardowns,
                        resolve_user_fixture=lambda n: self.get_fixture_by_name(
                            n, meta.module_path, fn_teardowns
                        ),
                        # owner_scope defaults to None — test level, so a
                        # builtin resolved here keeps its own scope (#1777).
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

        Note: this route runs outside a test, so a ``TestContext`` resolved
        through it raises ``TestIdentityUnavailableError`` on ``name``,
        ``node_id``, ``marks`` and ``param_id`` (#1874). It used to return
        empty strings, which was true but silent — a caller could not tell an
        unnamed test from no test. ``addfinalizer`` and ``module_path`` are
        unaffected.
        """
        try:
            defn = self._registry.resolve(t, qualifier=t.__name__)
        except FixtureNotFoundError:
            raise FixtureTypeNotFoundError(t.__name__) from None
        meta = TestMeta(
            module_path=module_path, fn_name="", node_id="", describes_a_test=False
        )
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
        test_is_async: bool,
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

    def get_fixture_shortcut(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        *,
        test_is_async: bool,
    ) -> Any:
        """Resolve ``fx.<name>`` — shortcut access, no package prefix (#1714).

        The bare-name peer of :meth:`get_fixture_in_namespace`, and deliberately
        *not* an extension of :meth:`get_fixture_by_name`. That one is the
        ``Fixture[T]`` injection route's resolver; injection reaches a sync test
        through resolved kwargs, where ``AsyncDepGuardMiddleware`` already
        catches an async fixture before the body runs. A proxy access has no
        kwarg to inspect — it happens lazily inside the body, after middleware
        — so it needs the guard here, exactly as the qualified route does. A
        shared signature would have put an async branch on injection's hot path
        for no benefit and some risk.

        Resolution reads the B1-filtered catalog, so a shortcut can never reach
        a fixture the qualified path could not. An invisible name raises
        ``FixtureNotFoundError`` rather than ``BoundaryError``: a bare name has
        no segment to attribute the boundary to (ADR-0009 Rule 5, as amended).

        One message covers every miss — typo, cross-boundary, and foreign
        inline alike — because the alternatives are not distinguishable
        *deterministically*. Telling them apart needs the unfiltered catalog,
        which contains inline declarations, and those register only in the
        worker that imported their module. Branching on it made the diagnostic
        vary with worker assignment, which is the scheduling-dependent message
        ADR-0009 Rule 5 rules out. So the wording states what is true in all
        three cases rather than guessing which one happened.
        """
        defn = self._registry.get_visible(name, module_path)
        if defn is None:
            msg = (
                f"cannot resolve fixture '{name}'.\n"
                f"  Hint: '{name}' is neither a package segment reachable from "
                f"this test nor a fixture visible to it. A shortcut "
                f"(fx.{name}) needs a fixture of that name declared in this "
                f"test's own package or an ancestor of it; a fixture declared "
                f"inline in another test module is never visible here. Check "
                f"the spelling, or use the qualified form fx.<package>.{name}."
            )
            raise FixtureNotFoundError(name, message=msg)
        ctx = _ResolutionContext(
            module_path, fn_teardowns, frozenset(), self._scope_for, module_path
        )
        if defn.is_async:
            # ADR-0006's illegal cell on the shortcut route. Namespace is empty
            # because there is none — AsyncFixtureAccessError renders `fx.<name>`
            # rather than `fx.<ns>.<name>` when it is.
            if not test_is_async:
                raise AsyncFixtureAccessError(name, "", defn.scope.value)
            return self._instantiator.resolve_async_in_namespace(defn, ctx)
        return self._instantiator.resolve_fixture_in_namespace(defn, name, ctx)

    def get_namespace_for_func(
        self,
        name: str,
        func: Callable[..., Any],
    ) -> str | None:
        return self._registry.get_namespace_for_func(name, func)
