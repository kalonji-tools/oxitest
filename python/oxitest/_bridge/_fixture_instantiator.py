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
    PROCESS_BOUNDARY,
    AsyncPolicy,
    _check_async_dep,
    _reject_async_in_sync,
    _reject_nonshared_async,
)
from oxitest._bridge._boundary import (
    advance_async_gen,
    safe_teardown,
    setup_completed,
)
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
    FixtureScope,
    FrameworkSource,
    ModuleSource,
    PluginModuleSource,
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
    #: True when this scope lives exactly one test — the function tier's
    #: per-test cache (#1775). ``scope_refs is not None`` used to imply
    #: "outlives the test"; four behaviours hang off that reading
    #: (``FrozenProxy`` freezing, eager session-loop async resolution,
    #: session-manager teardown routing, and the shorter-lived-async-dep
    #: rejection) and none of them may apply to a scope that dies with the
    #: test. This flag is what lets the function tier share the cache gate
    #: without inheriting any of them.
    per_test: bool = False


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Context threaded through source-based fixture dispatch.

    Bundled args for FixtureInstantiator.resolve_by_source. Separate from
    _ResolutionContext (which threads by-name resolution state including
    cycle detection).

    Fields:
        meta: forwarded to BuiltinSource injection
        fn_teardowns: accumulator for PluginSource teardown lambdas
        resolve_user_fixture: cycle-safe resolver for FrameworkSource
        owner_scope: tier of the fixture whose dependencies are being
            resolved, or None at test level. Only ``PROCESS`` changes
            anything — see ``resolve_by_source`` (#1777).
        owner_teardowns: teardown list of that same fixture, or None at test
            level. ``owner_scope`` named the tier but nothing carried the
            list, so the cache followed the owner while the teardown stayed
            on the constructing test (#1958).
    """

    meta: TestMeta
    fn_teardowns: list[Callable[[], None]]
    resolve_user_fixture: Callable[[str], Any]
    owner_scope: FixtureScope | None = None
    owner_teardowns: list[Callable[[], None]] | None = None

    @property
    def teardown_target(self) -> list[Callable[[], None]]:
        """Where a dependency resolved under this context registers cleanup.

        ``is None``, never ``or``: a freshly created ``_Scope`` has an empty
        teardown list, which is falsy, so ``owner_teardowns or fn_teardowns``
        would bind the *test's* list for exactly the fixture that has
        registered nothing yet — which is every fixture at the moment its first
        dependency resolves. That spelling reintroduces #1958 in the common
        case while passing any test whose fixture registers a teardown before
        resolving a dependency.
        """
        if self.owner_teardowns is None:
            return self.fn_teardowns
        return self.owner_teardowns


@dataclass(frozen=True, slots=True)
class _ResolutionContext:
    """Shared context threaded through fixture resolution.

    ``module_path`` and ``boundary_path`` are deliberately separate. The first
    is *where execution is* — it selects the module-lifetime scope bucket and
    fills ``TestMeta`` — and must keep pointing at the running test all the way
    down a dependency chain. The second is *what the B1 rules are being read
    against*, and switches to a fixture's own anchor the moment resolution
    descends into that fixture's dependencies.
    """

    module_path: str
    fn_teardowns: list[Callable[[], None]]
    resolving: frozenset[str]
    scope_callback: Callable[[FixtureDef[Any], str], ScopeRefs | None]
    boundary_path: str
    #: Tier of the fixture currently being instantiated, so a builtin resolved
    #: underneath it can follow that tier's teardown boundary (#1777). None at
    #: test level, where the builtin keeps its own scope.
    owner_scope: FixtureScope | None = None
    #: Teardown list of the fixture currently being instantiated, so a
    #: dependency resolved underneath it disposes at that fixture's boundary
    #: rather than the constructing test's (#1958). ``owner_scope`` named the
    #: tier but nothing carried the list, so the cache followed the owner while
    #: the teardown did not. None at test level and at function tier, where the
    #: constructing test's list already *is* the right boundary.
    owner_teardowns: list[Callable[[], None]] | None = None
    #: True once resolution has descended beneath ANY wider-than-function
    #: owner. Unlike ``owner_scope`` this ACCUMULATES: a module-lifetime
    #: fixture may depend on a function-lifetime one, and the inner fixture is
    #: then built once under the first test and cached for the module, so it
    #: stops being per-test while still declaring ``lifetime="function"``
    #: (#1879). ``owner_scope`` is overwritten at each descent and cannot
    #: express ancestry.
    under_wider_owner: bool = False


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


def _per_test_key(defn: FixtureDef[Any]) -> str:
    """Key a function-lifetime fixture within the per-test cache (#1775).

    Namespace-qualified whenever a namespace exists: two function-lifetime
    fixtures sharing a short name in different packages can both be touched by
    one test, and a bare-name key would silently hand the second access the
    first fixture's instance. Deliberately NOT folded into ``_cache_key``:
    that key also names entries in the fixture-timing report, and the function
    tier's timing names must not change just because caching arrived.
    """
    if defn.namespace:
        return f"{defn.namespace}.{defn.name}"
    return defn.name


def _boundary_for(defn: FixtureDef[Any], ctx: _ResolutionContext) -> str:
    """The B1 boundary in force while resolving *defn*'s own dependencies.

    A fixture's dependencies are its own declarations, so they are governed by
    its anchor — not by wherever the test that happened to trigger resolution
    lives. Threading the test's path unchanged down the chain let a
    ``tests/api`` fixture pick up a ``tests/api/v1`` dependency it could never
    legally declare, whenever a test living in ``v1`` resolved it. At
    ``lifetime="package"`` the resulting cache entry then embedded a value from
    the narrower boundary and handed it to every other package member.

    Unanchored sources — conftest, plugin, builtin — are exempt from B1 and
    leave the boundary where it was.
    """
    anchor = defn.anchor
    return anchor if anchor is not None else ctx.boundary_path


def _async_teardown_boundary(
    defn: FixtureDef[Any], ctx: _ResolutionContext
) -> str | None:
    """The boundary whose exit disposes *defn*'s async teardown.

    ``None`` means "no boundary of its own" and lands the teardown on
    ``SESSION_BOUNDARY``, drained at ``end_task`` — the end of the task group,
    which is the whole run only on the serial path. Three scopes take it:
    ``shared=True`` and the builtins' session tier, neither of which has a
    narrower boundary to wait for, and the function tier when no per-test sink
    is active, where it is the backstop rather than the normal route.

    One function for two registration sites, and that is the point. They
    disagreed before #1839: the lazy ``fx.`` route named a module boundary
    while the eager ``Fixture[T]`` route sent everything to task end, so the
    same ``lifetime="module"`` fixture was disposed per module through one
    access spelling and at the end of the task group through the other. The
    mismatch was invisible because each route had its own copy of the mapping.

    Every arm is pinned by ``test_async_teardown_boundary_covers_every_scope``;
    the end-to-end suites reach only ``package``.
    """
    if defn.scope is FixtureScope.MODULE:
        return ctx.module_path
    if defn.scope is FixtureScope.PACKAGE:
        # The anchor directory, matching what `end_package` drains and what
        # `_package_scopes` is keyed by. `defn.anchor` is None only for
        # unanchored sources, which cannot reach the package tier.
        return defn.anchor
    if defn.scope is FixtureScope.PROCESS:
        # Its own key so it survives `end_task` — everything above drains
        # with the task group (#1777).
        return PROCESS_BOUNDARY
    return None


def _resolve_deps(  # noqa: PLR0913 — five resolve the dependencies, the sixth is the owner's tier, which this site cannot infer
    instantiator: FixtureInstantiator,
    fn: Callable[..., Any],
    ctx: _ResolutionContext,
    fn_name: str,
    resolve_user: Callable[[str], Any],
    owner_tier: FixtureScope | None = None,
) -> dict[str, Any]:
    """Resolve fixture dependencies from type hints."""
    # A minimal TestMeta for fixture-to-fixture resolution. module_path and
    # fn_name are real — the first selects the scope bucket, the second
    # prefixes TempDir — but nothing here describes a test: _ResolutionContext
    # carries no node_id and no markers, and above `function` lifetime the
    # fixture is built once for whichever test arrives first, so there is no
    # test to describe. `describes_a_test=False` is what makes TestContext say
    # so instead of reporting fn_name as the test's name (#1874).
    # ``identity_available`` records whether a ``TestIdentity`` resolved under
    # this bundle may answer: true for a `function`-lifetime fixture that no
    # wider consumer caches (#1879). The identity itself is read ambiently.
    dep_meta = TestMeta(
        module_path=ctx.module_path,
        fn_name=fn_name,
        node_id="",
        describes_a_test=False,
        identity_available=(
            owner_tier is FixtureScope.EACH and not ctx.under_wider_owner
        ),
    )
    hints = _get_hints(fn)
    deps: dict[str, Any] = {}
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        resolved, value = instantiator.resolve_param(
            param_name,
            hint,
            DispatchContext(
                meta=dep_meta,
                fn_teardowns=ctx.fn_teardowns,
                resolve_user_fixture=resolve_user,
                owner_scope=ctx.owner_scope,
                owner_teardowns=ctx.owner_teardowns,
            ),
        )
        if resolved:
            deps[param_name] = value
    return deps


@dataclass(frozen=True, slots=True)
class HasTeardown:
    """A generator fixture, not yet started, plus the teardown that disposes it.

    Carries the **generator** rather than a value because at this point no
    value exists: the caller registers ``teardown`` and only then calls
    :meth:`start`. Doing it the other way round leaves a window in which an
    interrupt strands a set-up fixture with nothing registered to dispose it
    (#1962).
    """

    generator: Any
    teardown: Callable[[], None]

    def start(self, register: Callable[[Callable[[], None]], None]) -> Any:
        """Register the teardown, then run the body to its first ``yield``.

        *register* is taken as an argument rather than left to the caller so
        the ordering cannot be got wrong: there is no way to reach the advance
        that does not pass through the registration first.

        An earlier version of this fix documented the ordering and left the two
        statements adjacent at the call site. Mutation testing then swapped
        them and **no test failed** — the two orders are indistinguishable
        except under an interrupt, which no test can inject at that point. The
        invariant has to be carried by the type rather than by a comment
        (ADR-0011, #1962).
        """
        register(self.teardown)
        return next(self.generator)


@dataclass(frozen=True, slots=True)
class NoTeardown:
    """Fixture unpacking produced a plain value; no teardown to run."""

    value: Any


FixtureOutcome = HasTeardown | NoTeardown


def _sync_teardown(gen: Any, name: str) -> Callable[[], None]:
    """Build a generator's teardown closure **without advancing it** (#1962).

    Separated from the advance so a caller can register the teardown first.
    The guard is what makes that safe: a generator registered before it is
    started may be drained having never reached its ``yield``, and ``next()``
    on an unstarted generator would *run the setup* during teardown.
    """

    def teardown() -> None:
        def _drain() -> None:
            if not setup_completed(gen):
                return
            with contextlib.suppress(StopIteration):
                next(gen)

        safe_teardown(_drain, name, warn=_warn_teardown)

    return teardown


def _unpack_sync(result: Any, name: str) -> FixtureOutcome:
    """Classify a sync fixture call **without advancing it**.

    Returns :class:`HasTeardown` carrying the un-started generator and its
    teardown, or :class:`NoTeardown` carrying a plain value. The caller
    registers the teardown and then calls ``start()`` — this function
    deliberately does neither, because the ordering is the whole point (#1962).

    Coroutines and async generators are passed through as-is: the async
    execution middleware (`_unpack_async_fixtures`) awaits/advances them
    inside the test's event loop for parameter-injected async fixtures,
    and `executor._drive_arrange_async_each` handles the arrange path.
    """
    if inspect.isgenerator(result):
        return HasTeardown(generator=result, teardown=_sync_teardown(result, name))
    return NoTeardown(value=result)


# ── FixtureInstantiator ──────────────────────────────────────────────────────


class FixtureInstantiator:
    """Resolves and creates fixture values. Stateless — scope refs passed in.

    The Instantiator never owns fixture scopes; callers supply scope information
    via a ``scope_callback`` that maps a ``(FixtureDef, module_path)`` pair to
    ``ScopeRefs | None``. ``None`` means no scope is active (no caching); a
    ``ScopeRefs`` means a cached scope — the per-test function scope
    (``per_test=True``), shared, or a wider bucket selected by *module_path*.
    """

    def __init__(
        self,
        registry: FixtureRegistry,
        plugin_registry: PluginRegistry,
        async_mgr: Any = None,  # SharedAsyncManager, optional to avoid import
        session_scope: _Scope | None = None,
        process_scope: _Scope | None = None,
    ) -> None:
        self._registry = registry
        self._plugin_registry = plugin_registry
        self._async_mgr = async_mgr
        self._session_scope = session_scope
        # Where a session-scoped builtin caches when the fixture asking for it
        # is itself process-lifetime (#1777). See `resolve_by_source`.
        self._process_scope = process_scope
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
        ctx: DispatchContext,
    ) -> tuple[bool, Any]:
        """Resolve a single parameter by its type hint.

        Returns (resolved, value) where resolved=True means the value should be
        injected for this parameter. Returns (False, None) if the hint is not
        injectable (not Fixture[T]).

        The caller supplies *ctx*, which carries ``owner_scope`` — the tier of
        the fixture these parameters belong to, or None when resolving a test's
        own parameters.

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
            return True, self.resolve_by_source(defn, ctx)

        # Unified type-based resolution — try type first.
        #
        # `resolve` reads the registry's `_by_type` index, which applies no B1
        # visibility filtering (#1768). It is harmless only because collection
        # already refused every parameter whose *name* is unregistered — see
        # `FixtureValidator.validate_fixture_names` — so for a user-source
        # fixture the name branch below always wins and the type hit is
        # discarded. Anything that makes a non-matching parameter name resolve
        # by type alone turns this into a real bypass: a test could inject a
        # fixture anchored in a package it cannot see, purely by naming its type.
        try:
            defn = self._registry.resolve(inner, qualifier=param_name)
        except FixtureNotFoundError:
            # No type-based match. Fall back to name-based lookup.
            if self._registry.get(param_name) is not None:
                return True, ctx.resolve_user_fixture(param_name)
            raise FixtureNotFoundError(param_name) from None

        # For Builtin/Plugin sources found by type, use direct instantiation.
        # PluginModuleSource is deliberately on the *user* side: it carries a
        # real callable declared with @oxi.fixture, so it needs cycle detection
        # and scope caching, which resolve_by_source bypasses (#1717).
        if not isinstance(
            defn.source, (FrameworkSource, ModuleSource, PluginModuleSource)
        ):
            return True, self.resolve_by_source(defn, ctx)

        # For FrameworkSource/ModuleSource: prefer name-based (preserves cycle
        # detection), fall back to type-resolved name.
        resolve_name = (
            param_name if self._registry.get(param_name) is not None else defn.name
        )
        return True, ctx.resolve_user_fixture(resolve_name)

    def resolve_by_source(
        self,
        defn: FixtureDef[Any],
        ctx: DispatchContext,
    ) -> Any:
        """Instantiate a fixture from its FixtureDef via source-based dispatch.

        Dispatches per ``FixtureSource`` variant:

        - ``FrameworkSource`` / ``ModuleSource``: routes through
          ``ctx.resolve_user_fixture`` to preserve cycle detection and scope
          caching.  Slice 1: both variants behave identically here; divergence
          appears in slices 2 (module-lifetime scope cache), 6 (B1 boundary
          enforcement), and 9 (autouse).
        - ``PluginSource``: invokes ``provider.create(ctx=None)`` and appends the
          provider's teardown to ``ctx.fn_teardowns``.
        - ``BuiltinSource``: delegates to ``inject_builtin`` with function scope.
        """
        match defn.source:
            case FrameworkSource() | ModuleSource() | PluginModuleSource():
                return ctx.resolve_user_fixture(defn.name)
            case PluginSource(provider=provider):
                value = provider.create(ctx=None)
                ctx.teardown_target.append(
                    lambda v=value, p=provider: p.teardown(value=v)
                )
                return value
            case BuiltinSource(impl_cls=impl_cls):
                # A session-scoped builtin normally caches in the session
                # scope, which drains at end_task. A process-lifetime fixture
                # outlives that, so depending on one would hand it a value
                # whose owner was disposed at the task boundary — silently,
                # since TempDirFactory.close() uses ignore_errors (#1777).
                #
                # Give it a process-scoped instance instead of rejecting the
                # dependency: the fixture asked for process lifetime, and this
                # is what that means for the resources it builds on. Ordinary
                # tests are untouched — they keep the per-task instance, so no
                # suite accumulates temp dirs without opting in.
                owner_is_process = ctx.owner_scope is FixtureScope.PROCESS
                return self.inject_builtin(
                    impl_cls,
                    ctx.meta,
                    "function",
                    ctx.teardown_target,
                    session_scope=self._process_scope if owner_is_process else None,
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
        # Bare-name lookup, so this route never sees a namespace — the inline
        # module restriction has to be applied here too. Filtering only the
        # proxy path (`get_fixture_in_namespace`) left an inline fixture
        # injectable into a sibling file by `Fixture[T]` annotation, which is the
        # route a user reaches for first. The boundary is ctx.boundary_path, not
        # ctx.module_path — see _boundary_for.
        defn = self._registry.get_visible(name, ctx.boundary_path)
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
        # From here down we are inside *defn*'s own declarations, so B1 is read
        # against its anchor. Covers _instantiate and _resolve_shared_async,
        # both of which reach dependencies through this ctx.
        #
        # owner_scope rides along for the same reason, one concern over: a
        # builtin resolved as one of *defn*'s dependencies must follow *defn*'s
        # teardown boundary, not its own (#1777).
        ctx = replace(
            ctx,
            boundary_path=_boundary_for(defn, ctx),
            owner_scope=defn.scope,
            under_wider_owner=(
                ctx.under_wider_owner or defn.scope is not FixtureScope.EACH
            ),
        )
        scope_refs = ctx.scope_callback(defn, ctx.module_path)
        # Placed before every branch below, so the async route inherits it too:
        # _resolve_shared_async reaches _resolve_deps without passing through
        # _instantiate, and a second assignment there would be a second thing
        # to keep in step (#1958). The per_test tier is excluded deliberately
        # rather than incidentally — its teardowns list *is* fn_teardowns, so
        # binding it would be a no-op that reads like a behaviour change.
        if scope_refs is not None and not scope_refs.per_test:
            ctx = replace(ctx, owner_teardowns=scope_refs.teardowns)

        if scope_refs is not None and scope_refs.per_test:
            return self._resolve_per_test(defn, ctx, scope_refs)

        if scope_refs is not None:
            # Cached fixture (shared or wider lifetime) — check cache first
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

        # Function scope outside a test (no per-test scope active) — no caching
        return self._instantiate(defn, ctx, ctx.fn_teardowns)

    def _resolve_per_test(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs,
    ) -> Any:
        """Resolve a function-lifetime fixture through the per-test cache.

        One build per test regardless of access route — the autouse pass,
        ``Fixture[T]`` injection, and ``fx.`` proxy access all land here
        (#1775, ADR-0009's lifetime table). Three deliberate differences from
        the wider-tier branch above:

        - the value is cached **raw**, never ``FrozenProxy``-wrapped —
          function lifetime is the tier whose values a test may freely
          mutate; ADR-0005 freezes only what outlives a test;
        - an async def is neither resolved eagerly on the session loop nor
          cached: the sync route hands its coroutine to the execution
          middleware exactly as before, and a coroutine object can only be
          awaited once, so caching it would hand a dead coroutine to the
          next route. The proxy route caches the *awaited value* in
          ``_build_async`` instead;
        - teardowns go to ``scope_refs.teardowns``, which **is** the per-test
          ``fn_teardowns`` list, so the executor keeps draining them exactly
          once per test with unchanged ordering — a cache hit skips
          ``_instantiate`` entirely and therefore cannot double-register.
        """
        if defn.is_async:
            return self._instantiate(defn, ctx, ctx.fn_teardowns)
        key = _per_test_key(defn)
        if key in scope_refs.cache:
            scope_refs.hits[key] = scope_refs.hits.get(key, 0) + 1
            return scope_refs.cache[key]
        scope_refs.misses[key] = scope_refs.misses.get(key, 0) + 1
        value = self._instantiate(defn, ctx, scope_refs.teardowns)
        scope_refs.cache[key] = value
        return value

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
            owner_tier=defn.scope,
        )
        for dep_name, dep_val in deps.items():
            _reject_nonshared_async(dep_name, dep_val, defn.name)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            _start = time.perf_counter()
            value = self._async_mgr.resolve(
                defn.func,
                deps,
                boundary=_async_teardown_boundary(defn, ctx),
            )
            self._setup_times[_cache_key(defn)].append(
                (time.perf_counter() - _start) * 1000.0
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
        lifetime outside a test, the per-test scope during one (#1775 — two
        distinct handles for the same fixture, e.g. the shortcut and the
        qualified spelling, must converge on one build), and the tier's scope
        for wider lifetimes.
        """
        ctx = replace(ctx, boundary_path=_boundary_for(defn, ctx))
        timing_key = _cache_key(defn)
        per_test = scope_refs is not None and scope_refs.per_test
        key = _per_test_key(defn) if per_test else timing_key
        if scope_refs is not None:
            if key in scope_refs.cache:
                scope_refs.hits[key] = scope_refs.hits.get(key, 0) + 1
                return scope_refs.cache[key]
            scope_refs.misses[key] = scope_refs.misses.get(key, 0) + 1

        deps = await self._resolve_async_deps(defn, ctx, scope_refs)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            _start = time.perf_counter()
            try:
                raw = defn.func(**deps)
                if inspect.isasyncgen(raw):
                    # Queued BEFORE the advance (#1962). The advance suspends to
                    # the event loop, which is exactly where a pending signal
                    # is delivered, so the reverse order loses the teardown far
                    # more often here than on the sync path.
                    self._queue_async_teardown(defn, ctx, scope_refs, raw)
                    value = await advance_async_gen(raw)
                elif inspect.iscoroutine(raw):
                    value = await raw
                else:
                    value = raw
            except Exception as exc:
                raise FixtureSetupError(defn.name, exc) from exc
            self._setup_times[timing_key].append(
                (time.perf_counter() - _start) * 1000.0
            )

        if scope_refs is not None:
            # The per-test cache stores the value raw: function-lifetime
            # values die with the test and stay mutable (ADR-0005 freezes
            # only what outlives one).
            if not per_test:
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
            owner_tier=defn.scope,
        )
        for dep_name, dep_val in deps.items():
            if scope_refs is not None and not scope_refs.per_test:
                # This fixture outlives the test; a dependency that does not
                # would be captured into the wider cache and handed to every
                # later test — and, being loop-bound, to tests whose loop it
                # no longer belongs to. The eager path rejects the same shape.
                # The per-test scope is exempt: a function-lifetime fixture
                # depending on another function-lifetime async fixture dies
                # with the same test and has always been legal.
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
                # Queued before the advance, as above (#1962).
                self._queue_async_teardown(defn, ctx, scope_refs, dep_val)
                deps[dep_name] = await advance_async_gen(dep_val)
        return deps

    def _queue_async_teardown(
        self,
        defn: FixtureDef[Any],
        ctx: _ResolutionContext,
        scope_refs: ScopeRefs | None,
        agen: Any,
    ) -> None:
        """Queue an async generator's post-``yield`` half at the right boundary.

        Function lifetime (*scope_refs* is ``None`` outside a test, or the
        per-test scope during one) drains inside the test body, while the
        loop that created the generator is still open — an async generator
        can only be resumed on the loop it was started on, so anything later
        is a teardown on a dead loop.

        Wider lifetimes outlive that loop by definition, so they go to the
        session-lifetime manager, tagged with the boundary whose exit should
        dispose them — see :func:`_async_teardown_boundary`, which the eager
        ``Fixture[T]`` route shares.
        """
        dies_with_test = scope_refs is None or scope_refs.per_test
        if dies_with_test and register_async_teardown(defn.name, agen):
            return
        if self._async_mgr is None:
            return
        self._async_mgr.register_teardown(
            defn.name, agen, boundary=_async_teardown_boundary(defn, ctx)
        )

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
            owner_tier=defn.scope,
        )
        # Async fixtures may depend on other async fixtures; only reject in sync context
        if not defn.is_async:
            for dep_name, dep_val in deps.items():
                _reject_async_in_sync(dep_name, dep_val, defn.name)

        with _fixture_scope(self, ctx.module_path, ctx.fn_teardowns):
            try:
                _start = time.perf_counter()
                result = defn.func(**deps)
                outcome = _unpack_sync(result, defn.name)
                match outcome:
                    case HasTeardown(generator=generator, teardown=teardown_fn):

                        def _timed_teardown(
                            _orig: Callable[[], None] = teardown_fn,
                            _gen: Any = generator,
                            _name: str = _cache_key(defn),
                        ) -> None:
                            # Reachable for a fixture whose setup never
                            # completed, because registration now precedes the
                            # advance. Nothing ran, so nothing is timed — a
                            # 0 ms entry would read as "torn down instantly"
                            # in the timing report (#1962).
                            if not setup_completed(_gen):
                                return
                            _td_start = time.perf_counter()
                            _orig()
                            self._teardown_times[_name].append(
                                (time.perf_counter() - _td_start) * 1000.0
                            )

                        # `start` performs the registration itself, so the
                        # advance cannot be reached without it (#1962). The
                        # raw teardown is discarded: what gets registered is
                        # the timed wrapper around it.
                        value = outcome.start(
                            lambda _raw: scope_teardowns.append(_timed_teardown)
                        )
                    case NoTeardown(value=plain_value):
                        value = plain_value
                    case _:
                        assert_never(outcome)
                self._setup_times[_cache_key(defn)].append(
                    (time.perf_counter() - _start) * 1000.0
                )
            except Exception as exc:
                raise FixtureSetupError(defn.name, exc) from exc

        return value

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
