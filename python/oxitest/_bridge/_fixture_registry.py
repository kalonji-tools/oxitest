from __future__ import annotations

__all__ = [
    "LIFETIME_SCOPES",
    "BuiltinSource",
    "ConftestSource",
    "FixtureDef",
    "FixtureRegistry",
    "FixtureScope",
    "FixtureSource",
    "ModuleSource",
    "PluginSource",
    "_fixture_inner_type",
]
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from types import MappingProxyType
from typing import (
    Annotated,
    Any,
    Final,
    Generic,
    TypeAlias,
    TypeVar,
    get_args,
    get_origin,
)

from oxitest._bridge._boundary import safe_type_hints
from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import AmbiguousFixtureError, FixtureNotFoundError
from oxitest._bridge._fixture_type import _FixtureMarker
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._visibility import anchor_depth, anchors_overlap, is_visible
from oxitest._bridge.result import CollectedViolation, DiagnosticSeverity, ViolationKind

T = TypeVar("T")

ConftestFunc: TypeAlias = Callable[..., Any]


class FixtureScope(StrEnum):
    EACH = auto()
    MODULE = auto()
    PACKAGE = auto()
    SHARED = auto()
    SESSION = auto()
    PROCESS = auto()


#: Declared tier → caching vocabulary. ``Lifetime`` is what users write;
#: ``FixtureScope`` is what the caching machinery reads. The two stay separate
#: until slice 13 retires the old ``Fixtures()`` API, so this is the single
#: translation point. Membership doubles as "is this tier implemented yet",
#: which is how ``@oxi.fixture`` gates declarations.
#:
#: ``PACKAGE`` is a member of its own rather than a reuse of ``SHARED``:
#: ``SHARED`` belongs to the legacy ``Fixtures(shared=True)`` API and is still
#: live (see :attr:`FixtureDef.shared`, ``_fixtures.py``, ``fixture_lister.py``).
#: Reusing it would make every legacy shared fixture look package-scoped to the
#: scheduler, collapsing parallelism for suites that never asked for the tier.
#:
#: ``PROCESS`` is a member of its own for the same reason, one tier up, and the
#: bucket it is *not* reusing is ``SESSION`` (#1777). ``SESSION`` is where the
#: builtins cache (``_TempDirFactoryFixture``), and the two tiers now end at
#: different boundaries: ``SESSION`` drains at ``end_task``, ``PROCESS`` at
#: ``end_process``. Keeping the builtins on the narrower rung is deliberate —
#: hoisting ``TempDirFactory`` to process lifetime would accumulate every temp
#: dir a worker ever created until the process exits.
#:
#: The user-facing tier maps here rather than to ``SESSION`` because that is
#: what makes it genuinely per-process. Before #1777 it shared the builtins'
#: bucket, and so inherited their boundary: once per **task group** — one module
#: unless a ``package`` declaration merged the subtree — which is not what
#: "session" promised anyone. Work that must happen exactly once per *run*
#: still belongs at rootdir ``package``; ``process`` is once per process, and
#: the user sets that count with ``-n``.
LIFETIME_SCOPES: Final = MappingProxyType(
    {
        Lifetime.FUNCTION: FixtureScope.EACH,
        Lifetime.MODULE: FixtureScope.MODULE,
        Lifetime.PACKAGE: FixtureScope.PACKAGE,
        Lifetime.PROCESS: FixtureScope.PROCESS,
    }
)


@dataclass(frozen=True, slots=True)
class ConftestSource:
    func: ConftestFunc
    conftest_path: str


@dataclass(frozen=True, slots=True)
class PluginSource:
    provider: Any  # FixtureProvider — use Any to avoid circular import
    plugin_module: str


@dataclass(frozen=True, slots=True)
class BuiltinSource:
    impl_cls: type  # type[BuiltinFixture] — use type to avoid circular import


@dataclass(frozen=True, slots=True)
class ModuleSource:
    """A fixture declared via @oxi.fixture at module level (ADR-0009)."""

    func: ConftestFunc
    defining_module_path: str
    anchor_package_path: str
    lifetime: Lifetime


FixtureSource = ConftestSource | PluginSource | BuiltinSource | ModuleSource


@dataclass(frozen=True, slots=True)
class FixtureDef(Generic[T]):
    name: str
    fixture_type: type  # binding type for type-based resolve
    scope: FixtureScope  # each, shared, or session
    source: FixtureSource  # where this fixture comes from
    autouse: bool = False
    namespace: str = ""  # Fixtures() instance name; empty = no namespace
    is_async: bool = False  # True = async def or async generator fixture
    depends_on: tuple[tuple[str, type], ...] = ()  # (qualifier, binding_type) pairs

    @property
    def func(self) -> Callable[..., T]:
        """Backward-compat: user-fixture callable (ConftestSource + ModuleSource)."""
        if isinstance(self.source, (ConftestSource, ModuleSource)):
            return self.source.func
        msg = (
            f"FixtureDef '{self.name}' has no func "
            f"(source: {type(self.source).__name__})"
        )
        raise AttributeError(msg)

    @property
    def conftest_path(self) -> str:
        """Backward-compat: return a path-like string for any source variant."""
        match self.source:
            case ConftestSource(conftest_path=p):
                return p
            case PluginSource(plugin_module=m):
                return f"<plugin:{m}>"
            case BuiltinSource():
                return "<builtin>"
            case ModuleSource(defining_module_path=p):
                return p
            case _:
                msg = f"unhandled FixtureSource variant: {self.source!r}"
                raise AssertionError(msg)

    @property
    def shared(self) -> bool:
        """Backward-compat: True when scope is SHARED."""
        return self.scope == FixtureScope.SHARED

    @property
    def anchor(self) -> str | None:
        """The B1 anchor path, or ``None`` for sources exempt from B1.

        Only ``ModuleSource`` is anchored. Conftest, plugin, and builtin
        fixtures are ambient by design (ADR-0009 Rules 6 and 7), and ``None``
        is what tells the registry's ordering rule to leave their existing
        locality semantics untouched.
        """
        return (
            self.source.anchor_package_path
            if isinstance(self.source, ModuleSource)
            else None
        )

    def is_visible_from(self, module_path: str) -> bool:
        """Whether code at *module_path* may resolve this fixture (ADR-0009 B1).

        Full ancestor-chain enforcement: a package fixture reaches its own
        package and every descendant, an inline fixture reaches only its own
        module, and everything unanchored is ambient.

        Lives on ``FixtureDef`` rather than beside one caller because there are
        **two** resolution routes — the ``fx.<ns>.<name>`` proxy and
        ``Fixture[T]`` parameter injection, which looks up by bare name and
        never sees a namespace. Filtering only one leaves the leak open on the
        other, so both get B1 from this single method.

        *module_path* is the calling test's module at the top of a resolution
        chain, and the resolving fixture's own anchor once the chain descends
        into that fixture's dependencies — see
        ``_ResolutionContext.boundary_path``. Passing the test's path all the
        way down would let a ``tests/api`` fixture acquire a ``tests/api/v1``
        dependency it could never legally declare.
        """
        match self.source:
            case ModuleSource(
                anchor_package_path=anchor, defining_module_path=defining
            ):
                return is_visible(
                    anchor=anchor, defining=defining, module_path=module_path
                )
            case _:
                return True


def _build_dependency_graph(registry: FixtureRegistry) -> dict[str, set[str]]:
    """Build adjacency dict: fixture_name -> set of dependency names."""
    graph: dict[str, set[str]] = {}
    for defn in registry.all():
        deps: set[str] = set()
        for _qualifier, dep_type in defn.depends_on:
            dep_defs = registry.get_by_type(dep_type)
            for dep in dep_defs:
                if dep.name != defn.name:
                    deps.add(dep.name)
        graph[defn.name] = deps
    return graph


def _compute_shared_ancestors(
    start: str,
    graph: dict[str, set[str]],
    by_name: dict[str, list[FixtureDef[Any]]],
    computed: dict[str, frozenset[str]],
) -> frozenset[str]:
    """Collect transitively reachable shared fixtures via iterative DFS from *start*.

    Results are memoised in *computed* to avoid redundant traversals.
    """
    if start in computed:
        return computed[start]
    result: set[str] = set()
    stack = [start]
    visited: set[str] = set()
    while stack:
        name = stack.pop()
        if name in visited:
            continue
        visited.add(name)
        defs = by_name.get(name)
        if defs and defs[-1].shared:
            result.add(name)
        for dep in graph.get(name, ()):
            if dep in visited:
                continue
            if dep in computed:
                result |= computed[dep]
            else:
                stack.append(dep)
    frozen = frozenset(result)
    computed[start] = frozen
    return frozen


def _transitive_shared(
    graph: dict[str, set[str]],
    by_name: dict[str, list[FixtureDef[Any]]],
) -> dict[str, frozenset[str]]:
    """For each fixture, find all transitively reachable shared fixtures.

    Uses iterative DFS with a results cache. Returns only entries with
    non-empty shared ancestor sets.
    """
    computed: dict[str, frozenset[str]] = {}
    shared_ancestors: dict[str, frozenset[str]] = {}
    for name in by_name:
        ancestors = _compute_shared_ancestors(name, graph, by_name, computed)
        if ancestors:
            shared_ancestors[name] = ancestors
    return shared_ancestors


def _merge_components(
    shared_ancestors: dict[str, frozenset[str]],
) -> tuple[tuple[str, ...], ...]:
    """Merge overlapping shared-ancestor sets into connected components via union-find.

    Returns sorted list of sorted groups.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for name, ancestors in shared_ancestors.items():
        for ancestor in ancestors:
            union(name, ancestor)

    groups: dict[str, set[str]] = {}
    for name in shared_ancestors:
        groups.setdefault(find(name), set()).add(name)

    components = [tuple(sorted(comp)) for comp in groups.values()]
    return tuple(sorted(components))


def _shadow_order(defn: FixtureDef[Any], index: int) -> tuple[int, int]:
    """Which of two mutually visible defs wins; higher is nearer.

    The one key both resolution and the shadow notice sort by. Resolution picks
    a winner and the notice names one, so a second copy of this rule would let
    the message describe a run that did not happen — ``_deepest_visible`` and
    ``_shadowing_pairs`` share this function rather than each computing depth
    for themselves.

    *index* is the registration position, which breaks equal-depth ties toward
    the later declaration. Unanchored sources score 0, so they lose to any
    anchored declaration that can see them, and a list with no ``ModuleSource``
    in it reduces to last-registered-wins unchanged.
    """
    anchor = defn.anchor
    return (anchor_depth(anchor) if anchor is not None else 0, index)


def _deepest_visible(
    defs: Sequence[FixtureDef[Any]], module_path: str
) -> FixtureDef[Any] | None:
    """The visible def with the deepest anchor; ties break by registration order.

    Deepest-wins is what makes flat namespaces safe in a tree. A namespace is a
    directory basename, so a ``(namespace, name)`` pair can name two different
    fixtures — ``tests/api/v1`` and ``tests/admin/v1`` both derive ``v1``. At
    most one of those is visible to a given test, so filtering resolves the
    ambiguity on its own; the depth order only matters for the remaining case
    where one anchor is an ancestor of the other, and there the nearer
    declaration should override, exactly as conftest locality already does.

    The ordering itself lives in ``_shadow_order`` — see there for how
    unanchored sources and equal-depth ties are scored.
    """
    best: FixtureDef[Any] | None = None
    best_order = (-1, -1)
    for index, defn in enumerate(defs):
        if not defn.is_visible_from(module_path):
            continue
        order = _shadow_order(defn, index)
        if order > best_order:
            best, best_order = defn, order
    return best


def _can_see_both(first: FixtureDef[Any], second: FixtureDef[Any]) -> bool:
    """Whether one module could resolve both defs — the shadowing precondition.

    Two declarations of a name clash only where a single test sees both, and
    disjoint subtrees never can: a namespace is a directory basename, so
    ``tests/api/v1`` and ``tests/admin/v1`` both derive ``v1`` while being
    mutually invisible. An inline anchor is a file, so two test modules are
    disjoint for the same reason.

    ``anchor is None`` is not a missing value — it marks a source exempt from
    B1. Conftest, plugin and builtin defs are ambient (ADR-0009 Rules 6 and 7)
    and reach every module in the run, so they overlap with everything,
    including each other. That arm is what keeps the one *true* shadow warning
    alive: a conftest fixture really is overridden inside a package that
    redeclares its name, and gating on ``anchors_overlap`` alone — which cannot
    be called without two anchors — would silently delete it.
    """
    first_anchor, second_anchor = first.anchor, second.anchor
    if first_anchor is None or second_anchor is None:
        return True
    return anchors_overlap(first_anchor, second_anchor)


def _shadowing_pairs(
    existing: Sequence[FixtureDef[Any]], incoming: FixtureDef[Any]
) -> list[tuple[FixtureDef[Any], FixtureDef[Any]]]:
    """``(shadower, shadowed)`` for each real clash *incoming* introduces.

    Scanning every prior rather than only the last is what suppression forces:
    while all pairs emitted, a chain covered the whole set incidentally; once
    disjoint pairs go quiet, a def spanning two mutually invisible subtrees
    would be compared against exactly one of them.

    Only **maximal** priors are reported. A prior that another overlapping
    prior already shadows is an interior link, and naming it too would turn a
    three-conftest chain's two notices into three. Reporting only the single
    winner instead fails the other way: two disjoint priors shadow each other
    not at all, so both are maximal and both genuinely clash.
    """
    overlapping = [
        (_shadow_order(prior, index), prior)
        for index, prior in enumerate(existing)
        if prior.conftest_path != incoming.conftest_path
        and _can_see_both(prior, incoming)
    ]
    incoming_order = _shadow_order(incoming, len(existing))
    return [
        (incoming, prior) if incoming_order > order else (prior, incoming)
        for order, prior in overlapping
        # No self-comparison guard: orders carry a distinct index each, so a
        # def is never strictly greater than itself.
        if not any(
            rival_order > order and _can_see_both(rival, prior)
            for rival_order, rival in overlapping
        )
    ]


class FixtureRegistry:
    """Registry of all fixture definitions collected from conftest files.

    Each fixture name maps to an ordered list of `FixtureDef` entries, one per
    conftest that defines it, from the root conftest to the most-local leaf.
    Resolution always picks the last (most-local) entry, implementing pytest's
    locality-wins override semantics.

    Registry pattern: instance-based with dual-index dicts (by-name and
    by-type). Appropriate when entries arrive at runtime (conftest loading),
    need multiple lookup strategies, and the registry lifecycle is tied to
    a session instance. Compare with ``BuiltinFixture._registry``
    (auto-registration), ``_MARK_REGISTRY`` (module-level dict), and
    ``PluginRegistry`` (dataclass with lazy cached_property).
    """

    def __init__(self) -> None:
        # name -> list of FixtureDef, ordered from root conftest to leaf conftest
        self._by_name: dict[str, list[FixtureDef[Any]]] = {}
        # type -> list of FixtureDef, indexed by fixture_type for type-based resolve
        self._by_type: dict[type, list[FixtureDef[Any]]] = {}
        # namespace -> defs declared into it. Replaces a bare set[str]: the keys
        # still answer "does this namespace exist", and the values turn the
        # per-test visibility question into a scan of one namespace rather than
        # of every fixture in the run.
        self._namespace_defs: dict[str, list[FixtureDef[Any]]] = {}

    def register(self, defn: FixtureDef[Any]) -> list[CollectedViolation]:
        for shadower, shadowed in _shadowing_pairs(
            self._by_name.get(defn.name, ()), defn
        ):
            scope = f" within {shadower.anchor}" if shadower.anchor is not None else ""
            emit_diagnostic(
                DiagnosticSeverity.NOTICE,
                "fixture registration",
                f"fixture '{defn.name}' in {shadower.conftest_path} "
                f"shadows definition in {shadowed.conftest_path}{scope}",
            )
        self._by_name.setdefault(defn.name, []).append(defn)
        self._by_type.setdefault(defn.fixture_type, []).append(defn)
        if defn.namespace:
            self._namespace_defs.setdefault(defn.namespace, []).append(defn)

        # Only check return annotation for conftest fixtures with real paths
        if not isinstance(defn.source, ConftestSource):
            return []
        if defn.conftest_path.startswith("<"):
            return []

        violations: list[CollectedViolation] = []
        hints = safe_type_hints(defn.source.func) or {}
        if "return" not in hints:
            violations.append(
                CollectedViolation(
                    node_id=defn.conftest_path,
                    kind=ViolationKind.MISSING_RETURN_ANNOTATION,
                    detail=defn.name,
                )
            )
        return violations

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def all(self) -> tuple[FixtureDef[Any], ...]:
        """Return all effective (most-local) fixture defs."""
        return tuple(defs[-1] for defs in self._by_name.values() if defs)

    def all_defs(self, name: str) -> tuple[FixtureDef[Any], ...]:
        defs = self._by_name.get(name)
        return tuple(defs) if defs else ()

    def get(self, name: str) -> FixtureDef[Any] | None:
        """Return the most-local (last-registered) FixtureDef for name."""
        defs = self._by_name.get(name)
        return defs[-1] if defs else None

    def get_by_type(self, t: type) -> tuple[FixtureDef[Any], ...]:
        """Return fixture definitions registered for the given type."""
        return tuple(self._by_type.get(t, ()))

    def get_autouse(self, module_path: str | None) -> Iterator[FixtureDef[Any]]:
        """Yield the effective autouse def for each name.

        *module_path* selects the query mode, mirroring :meth:`get` vs
        :meth:`get_visible` — and it has no default so a call site cannot
        silently stay unfiltered:

        - a path — the resolution query (#1774). Each name's def-list goes
          through ``_deepest_visible``, the same predicate :meth:`get_visible`
          uses, and the winner is yielded iff it is autouse. One predicate
          picks both the candidate and the resolution target, so the def whose
          ``autouse=True`` queues a name *is* the def resolution returns, and
          out-of-boundary anchored defs are never yielded — which is what
          keeps them from reaching the raise-on-missing filtered lookup.
          Unanchored sources are ambient (ADR-0009 Rules 6 and 7) and keep
          firing run-wide.
        - ``None`` — the full-catalog query: last-registered wins, no
          filtering. For introspection/validation (``find_unused_fixtures``),
          where an autouse fixture anywhere in the run counts as used.
        """
        for defs in self._by_name.values():
            if not defs:
                continue
            effective = (
                defs[-1] if module_path is None else _deepest_visible(defs, module_path)
            )
            if effective is not None and effective.autouse:
                yield effective

    def defs_in_namespace(
        self, name: str, namespace: str
    ) -> tuple[FixtureDef[Any], ...]:
        """Every def registered as ``(namespace, name)``, in registration order.

        Full-catalog query. The registrar's collision check needs all of them,
        not just the winner, because whether two declarations clash depends on
        their anchors rather than on which registered last.
        """
        defs = self._by_name.get(name)
        if not defs:
            return ()
        return tuple(defn for defn in defs if defn.namespace == namespace)

    def get_in_namespace(self, name: str, namespace: str) -> FixtureDef[Any] | None:
        """Most-local def for ``(namespace, name)``, ignoring anchors.

        Full-catalog query — "does this fixture exist anywhere in the run?".
        Resolution must use :meth:`get_visible_in_namespace` instead; this one
        stays for the read/introspection APIs (``_read_fixtures.py``) and for
        the diagnostic that has to distinguish "exists, elsewhere" from "no
        such fixture".
        """
        defs = self.defs_in_namespace(name, namespace)
        return defs[-1] if defs else None

    def get_visible_in_namespace(
        self, name: str, namespace: str, module_path: str
    ) -> FixtureDef[Any] | None:
        """Filtered counterpart of :meth:`get_in_namespace` — the resolution query."""
        return _deepest_visible(self.defs_in_namespace(name, namespace), module_path)

    def get_visible(self, name: str, module_path: str) -> FixtureDef[Any] | None:
        """Filtered counterpart of :meth:`get` — the bare-name resolution query."""
        return _deepest_visible(self._by_name.get(name, ()), module_path)

    def get_namespace_for_func(self, name: str, func: Callable[..., Any]) -> str | None:
        """Return the namespace of the FixtureDef whose func is *func*, or None.

        Also handles FixtureAccessor objects by unwrapping via `_fa_func`.
        """
        defs = self._by_name.get(name)
        if not defs:
            return None
        # Unwrap FixtureAccessor (duck-typed to avoid circular import)
        raw = getattr(func, "_fa_func", func)
        for defn in defs:
            if isinstance(defn.source, ConftestSource) and defn.source.func is raw:
                return defn.namespace or None
        return None

    def has_namespace(self, namespace: str) -> bool:
        """Whether *namespace* exists anywhere in the run. Full-catalog query."""
        return namespace in self._namespace_defs

    def has_visible_anchor(self, namespace: str, module_path: str) -> bool:
        """Whether any **anchored** def in *namespace* reaches *module_path*.

        The filtered counterpart of :meth:`has_namespace`, and the question the
        ``BoundaryError`` decision actually needs: *does B1 let this test
        through?*

        Anchored is the load-bearing word. Conftest, plugin and builtin defs are
        exempt from B1, so ``is_visible_from`` reports them visible everywhere.
        A namespace may hold both kinds at once — the registrar only rejects a
        repeated ``(namespace, name)`` pair, so a conftest ``api.conn`` and a
        ``tests/api/__fixtures__.py`` declaring ``api.other`` coexist happily.
        Counting the exempt def as evidence of reachability would make that
        namespace look reachable from every test in the run and strand its
        genuine cross-boundary accesses on ``FixtureNotFoundError``.
        """
        return any(
            defn.anchor is not None and defn.is_visible_from(module_path)
            for defn in self._namespace_defs.get(namespace, ())
        )

    def namespace_anchors(self, namespace: str) -> tuple[str, ...]:
        """Distinct anchors declaring into *namespace*, deepest first.

        Full-catalog query behind the ``BoundaryError`` message: it answers
        "where does this namespace actually live?" for a test that cannot see
        it. Unanchored sources contribute nothing, so a namespace made only of
        conftest fixtures reports ``()`` — which is what keeps the legacy API
        out of B1 diagnostics.
        """
        anchors = {
            anchor
            for defn in self._namespace_defs.get(namespace, ())
            if (anchor := defn.anchor) is not None
        }
        return tuple(sorted(anchors, key=anchor_depth, reverse=True))

    def shared_fixture_groups(self) -> tuple[tuple[str, ...], ...]:
        """Compute connected components of shared fixture dependencies.

        Uses the depends_on field of each FixtureDef to build a dependency
        graph, then computes transitive closure to find groups of fixtures
        linked by shared fixture dependencies. Returns sorted tuple of sorted
        groups.
        """
        graph = _build_dependency_graph(self)
        shared_ancestors = _transitive_shared(graph, self._by_name)
        if not shared_ancestors:
            return ()
        return _merge_components(shared_ancestors)

    def shared_names(self) -> tuple[str, ...]:
        """Return sorted names of fixtures with effective (most-local) shared=True."""
        return tuple(
            sorted(
                name for name, defs in self._by_name.items() if defs and defs[-1].shared
            )
        )

    def resolve(
        self, fixture_type: type, qualifier: str | None = None
    ) -> FixtureDef[Any]:
        """Resolve a fixture by its binding type.

        When exactly one fixture provides *fixture_type*, return it (qualifier
        is ignored).  When multiple fixtures match, *qualifier* (the parameter
        name) is used to disambiguate.  Raises ``FixtureNotFoundError`` if no
        fixture matches, ``AmbiguousFixtureError`` if disambiguation fails.

        **Unfiltered.** ``_by_type`` has no B1 counterpart the way ``get`` has
        ``get_visible``, so this answers "anywhere in the run", not "reachable
        from here". Both callers hand the result straight on to a name-based,
        B1-filtered step rather than letting it stand — ``resolve_param``'s name
        branch, and ``get_fixture_by_type``'s ``resolve_by_source``, which routes
        user-source defs back through ``resolve_fixture``. Read #1768 before
        changing either: the filtering lives downstream of this method, not in it.
        """
        candidates = self._by_type.get(fixture_type, [])
        if not candidates:
            raise FixtureNotFoundError(fixture_type.__name__)
        # Deduplicate by name — keep only the most-local (last) entry per name
        by_name: dict[str, FixtureDef[Any]] = {}
        for d in candidates:
            by_name[d.name] = d  # later entries override earlier ones
        unique = list(by_name.values())
        if len(unique) == 1:
            return unique[0]
        # Multiple matches — try qualifier
        if qualifier:
            named = self._by_name.get(qualifier, [])
            matched = [d for d in named if d.fixture_type == fixture_type]
            if len(matched) == 1:
                return matched[0]
        raise AmbiguousFixtureError(
            fixture_type.__name__,
            [d.name for d in unique],
        )


def _extract_annotated_type(hint: Any, marker_type: type) -> tuple[bool, Any]:
    """Return (has_marker, inner_type) for an Annotated[T, marker, ...] hint."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, marker_type) for m in meta):
        return False, None
    return True, inner


def _fixture_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture, inner_type).

    is_fixture is True iff hint is Fixture[T] or @injectable.
    """
    found, inner = _extract_annotated_type(hint, _FixtureMarker)
    if found:
        return True, inner
    if isinstance(hint, type) and getattr(hint, "__oxitest_injectable__", False):
        return True, hint
    return False, None
