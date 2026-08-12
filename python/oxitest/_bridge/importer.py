from __future__ import annotations

__all__ = [
    "collect_module",
    "register_module_source_fixtures_for_module",
    "register_plugin_source_fixtures_for_module",
]

import dataclasses
import hashlib
import importlib.util
import inspect
import itertools
import sys
from collections.abc import Callable, Iterable
from types import ModuleType
from typing import TYPE_CHECKING, Annotated, Any, cast, get_args, get_origin

from oxitest._bridge._boundary import safe_call, safe_type_hints
from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import CollectionError
from oxitest._bridge._fixture_registry import (
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._fixture_type import FixtureRef
from oxitest._bridge._fixtures import Fixtures
from oxitest._bridge._fn_metadata import _update, get_metadata
from oxitest._bridge._loader import _load_module, _LoadError, already_imported
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._metadata import get_marks
from oxitest._bridge._module_identity import dotted_name_for
from oxitest._bridge._module_source_registrar import (
    register_module_source_fixtures,
    register_plugin_source_fixtures,
)
from oxitest._bridge._test_kind import Parametrized, Solitary
from oxitest._bridge._violation_checkers import check_fn_violations
from oxitest._bridge.parametrize import ComposedCases, _as_composed
from oxitest._bridge.result import (
    CollectedItem,
    CollectedViolation,
    DiagnosticSeverity,
    ErrorResult,
    ViolationKind,
)

if TYPE_CHECKING:
    from oxitest._bridge.parametrize import ResolvedCases


def _get_fixture_deps(fn: object) -> tuple[tuple[str, str], ...]:
    """Extract (qualifier, type_name) pairs for all Fixture[T]-annotated params."""
    hints = safe_type_hints(fn, include_extras=True)
    if hints is None:
        return ()
    deps: list[tuple[str, str]] = []
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        if hint is Fixtures:
            continue
        is_fix, inner = _fixture_inner_type(hint)
        if is_fix:
            deps.append((param_name, inner.__name__))
    return tuple(deps)


def _propagate_class_metadata(fn: object, cls: object) -> None:
    """Copy marks AND arranged from a class onto a test method.

    Called at collection time when a test method is collected from a class.
    All marks on the class are propagated to each method. Class-level arranged
    entries are prepended to any method-level arranged entries so the class
    baseline comes first, then per-method additions.
    """
    for m in get_marks(cls):
        _append_mark(fn, m)

    cls_meta = get_metadata(cls)
    if cls_meta.arranged:
        method_meta = get_metadata(fn)
        merged_arranged = (*cls_meta.arranged, *method_meta.arranged)
        _update(fn, arranged=merged_arranged)


def _coerce_to_mark_info(entry: object) -> MarkInfo | None:
    """Convert a mark entry to MarkInfo, or return None if invalid.

    Accepts:
    - MarkInfo directly (from tests or explicit construction)
    - A mark factory/decorator (e.g. oxitest.mark.slow, oxitest.mark.timeout(5))
      by applying it to a sentinel function and reading back the MarkInfo.
    """
    if isinstance(entry, MarkInfo):
        return entry
    if callable(entry):

        def _sentinel() -> None:
            pass

        def _try_coerce() -> MarkInfo | None:
            cast(Callable[..., object], entry)(_sentinel)  # noqa: TC006 — ty can't narrow callable()
            marks = get_marks(_sentinel)
            return marks[-1] if marks else None

        return safe_call(_try_coerce, default=None)
    return None


def _extract_module_marks(
    module: ModuleType,
    path: str,
) -> tuple[list[MarkInfo], list[CollectedViolation]]:
    """Extract and validate the oxi_mark module variable.

    Returns (valid_marks, violations). Accepts a single MarkInfo, a mark
    factory/decorator (e.g. oxitest.mark.slow or oxitest.mark.timeout(5)),
    or a list/tuple of any mix. Non-mark entries produce INVALID_MODULE_MARK
    violations.
    """
    raw = getattr(module, "oxi_mark", None)
    if raw is None:
        return [], []
    # Single entry (not a list/tuple)
    if not isinstance(raw, (list, tuple)):
        if getattr(raw, "_oxitest_noop_mark", False):
            return [], []  # when=False skip — intentional no-op
        mark_info = _coerce_to_mark_info(raw)
        if mark_info is not None:
            return [mark_info], []
        return [], [
            CollectedViolation(
                node_id=path,
                kind=ViolationKind.INVALID_MODULE_MARK,
                detail=repr(raw),
            )
        ]
    marks: list[MarkInfo] = []
    violations: list[CollectedViolation] = []
    for entry in raw:
        if getattr(entry, "_oxitest_noop_mark", False):
            continue  # when=False skip — intentional no-op
        mark_info = _coerce_to_mark_info(entry)
        if mark_info is not None:
            marks.append(mark_info)
        else:
            violations.append(
                CollectedViolation(
                    node_id=path,
                    kind=ViolationKind.INVALID_MODULE_MARK,
                    detail=repr(entry),
                )
            )
    return marks, violations


def _apply_module_marks(
    members: Iterable[tuple[str, object]],
    module_marks: list[MarkInfo],
) -> None:
    """Append non-conflicting module marks onto each function's metadata.

    For each function, module marks whose name matches a per-test mark
    are skipped (per-test wins). Remaining module marks are appended
    after per-test marks in the list.
    """
    if not module_marks:
        return
    for _fn_name, fn in members:
        existing_names = {m.name for m in get_marks(fn)}
        for mark in module_marks:
            if mark.name not in existing_names:
                _append_mark(fn, mark)


def _validate_composition(layers: tuple[ResolvedCases, ...]) -> None:
    """Validate composition rules for ComposedCases layers.

    Raises TypeError if:
    - Only 1 partial layer (needs 2+)
    - Fields are incomplete (union doesn't cover all dataclass fields)

    All callers are guarded by ``isinstance(raw[0], ComposedCases)`` so every
    element is guaranteed to be ``ComposedCases`` at runtime.
    """
    if len(layers) == 1:
        msg = (
            "parametrize composition requires at least 2 stacked"
            " @parametrize layers with partial() values."
            " Use a full dataclass instance for single-layer parametrize."
        )
        raise TypeError(msg)
    composed_layers = _as_composed(layers)
    target_type = composed_layers[0].param_type
    all_provided = frozenset().union(
        *(layer.provided_fields for layer in composed_layers)
    )
    all_fields = {f.name for f in dataclasses.fields(target_type)}
    missing = all_fields - all_provided
    if missing:
        msg = (
            f"parametrize composition: missing field(s) {sorted(missing)!r}"
            f" on '{target_type.__name__}'."
            " The union of all layers must cover every field."
        )
        raise TypeError(msg)


@dataclasses.dataclass(frozen=True, slots=True)
class _ItemTemplate:
    """Shared fields for every CollectedItem produced from a single function."""

    fn_name: str
    lineno: int
    markers: tuple[str, ...]
    is_async: bool
    fixture_deps: tuple[tuple[str, str], ...]
    arranged: tuple[type | str, ...]


def _expand_composed(
    layers: tuple[ResolvedCases, ...],
    template: _ItemTemplate,
    fixref_deps: tuple[tuple[str, str], ...] = (),
) -> list[CollectedItem]:
    """Expand composed ResolvedCases layers via cartesian product."""
    layer_items = [layer.items() for layer in layers]
    items: list[CollectedItem] = []
    for combo in itertools.product(*layer_items):
        compound_id = "-".join(case_id for case_id, _ in combo)
        merged_pv: list[tuple[str, str]] = []
        for _, pv in combo:
            merged_pv.extend(pv)
        items.append(
            CollectedItem(
                fn_name=template.fn_name,
                lineno=template.lineno,
                markers=template.markers,
                kind=Parametrized(param_id=compound_id),
                param_values=tuple(merged_pv),
                is_async=template.is_async,
                fixture_deps=template.fixture_deps,
                fixref_deps=fixref_deps,
                arranged=template.arranged,
            )
        )
    return items


def _get_fixref_deps(layer: ResolvedCases) -> tuple[tuple[str, str], ...]:
    """Extract (qualifier, type_name) for FixtureRef fields.

    Inspects type hints on the layer's param_type to get the inner
    type T from FixtureRef[T].
    """
    fixref_fields = layer.fixref_fields
    if not fixref_fields:
        return ()
    param_type = getattr(layer, "param_type", None)
    if param_type is None:
        # DictCases has no param_type — should never have fixref_fields either
        return ()
    mod = sys.modules.get(param_type.__module__)
    globalns = dict(vars(mod)) if mod else {}
    globalns.setdefault("FixtureRef", FixtureRef)
    field_hints = safe_type_hints(param_type, globalns=globalns, include_extras=True)
    if field_hints is None:
        return ()
    deps: list[tuple[str, str]] = []
    for name in fixref_fields:
        hint = field_hints.get(name)
        if hint is None:
            continue
        if get_origin(hint) is not Annotated:
            continue
        inner = get_args(hint)[0]  # Callable[..., T]
        # Get T from Callable[..., T]
        callable_args = get_args(inner)
        if callable_args:
            fixture_type = callable_args[-1]  # last arg is return type T
            type_name = getattr(fixture_type, "__name__", str(fixture_type))
        else:
            type_name = str(inner)
        deps.append((name, type_name))
    return tuple(deps)


def _check_arrange_collisions(
    fn: object,
    arranged: tuple[type | str, ...],
) -> None:
    """Raise CollectionError if any arranged entry also appears as a parameter.

    Two collision variants:
    - Name collision: arranged string name matches a parameter name.
    - Type collision: arranged type matches a parameter annotation (bare or Fixture[T]).
    """
    if not arranged:
        return
    sig_params = inspect.signature(cast("Callable[..., Any]", fn)).parameters
    param_names = set(sig_params.keys())
    hints = safe_type_hints(fn, include_extras=True) or {}

    for entry in arranged:
        if isinstance(entry, str):
            # Name collision: arranged fixture name == parameter name
            if entry in param_names:
                qualname = getattr(fn, "__qualname__", repr(fn))
                msg = (
                    f"@oxi.arrange in {qualname}: arranged {entry!r} also declared "
                    f"as parameter. Choose one — arrange runs side effects only; "
                    f"parameter injection binds the value."
                )
                raise CollectionError(msg)
        else:
            # Type collision: arranged type matches a parameter annotation
            for pname, hint in hints.items():
                if pname == "return":
                    continue
                is_fx, inner = _fixture_inner_type(hint)
                if (is_fx and inner is entry) or hint is entry:
                    qualname = getattr(fn, "__qualname__", repr(fn))
                    msg = (
                        f"@oxi.arrange in {qualname}: arranged {entry.__name__} "
                        f"also declared as parameter {pname!r}. Choose one — "
                        f"arrange runs side effects only; parameter injection binds "
                        f"the value."
                    )
                    raise CollectionError(msg)


def _augment_fixture_deps(
    fixture_deps: tuple[tuple[str, str], ...],
    arranged: tuple[type | str, ...],
) -> tuple[tuple[str, str], ...]:
    """Append arranged canonical names to fixture_deps so the validator sees them.

    FixtureValidator.find_unused_fixtures() reads fixture_names, which Rust
    builds from fixture_deps qualifiers.  Arranged fixtures never appear in
    regular fixture_deps (they have no parameter annotation), so without this
    augmentation strict-mode would flag every arranged fixture as unused.

    Entries are deduplicated (dict.fromkeys preserves first-occurrence order).
    For type entries the qualifier and type_name are both set to ``type.__name__``,
    so validate_fixture_names' builtin-qualifier logic correctly skips built-ins.
    For string entries the type_name is left empty — the validator will check the
    registry, which is the right behaviour (arranged conftest fixtures must exist).
    """
    existing_qualifiers = {q for q, _ in fixture_deps}
    extra: list[tuple[str, str]] = []
    for entry in arranged:
        if isinstance(entry, str):
            name, type_name = entry, ""
        else:
            name = entry.__name__
            type_name = entry.__name__
        if name not in existing_qualifiers:
            extra.append((name, type_name))
            existing_qualifiers.add(name)
    return (*fixture_deps, *extra)


def _expand_item(
    fn_name: str,
    lineno: int,
    marker_names: list[str],
    fn: object,
) -> list[CollectedItem]:
    """Return one CollectedItem per parametrize case, or a single item if no cases."""
    fn_meta = get_metadata(fn)
    arranged = tuple(dict.fromkeys(fn_meta.arranged))
    _check_arrange_collisions(fn, arranged)
    augmented_fixture_deps = _augment_fixture_deps(_get_fixture_deps(fn), arranged)
    template = _ItemTemplate(
        fn_name=fn_name,
        lineno=lineno,
        markers=tuple(marker_names),
        is_async=inspect.iscoroutinefunction(fn),
        fixture_deps=augmented_fixture_deps,
        arranged=arranged,
    )
    raw = fn_meta.param_cases
    if not raw:
        return [
            CollectedItem(
                fn_name=template.fn_name,
                lineno=template.lineno,
                markers=template.markers,
                kind=Solitary(),
                param_values=(),
                is_async=template.is_async,
                fixture_deps=template.fixture_deps,
                arranged=template.arranged,
            )
        ]
    # Composition: all layers are ComposedCases (partial)
    if len(raw) > 1 or isinstance(raw[0], ComposedCases):
        # Merge fixref_deps from all composition layers
        all_fixref_deps: list[tuple[str, str]] = []
        seen: set[str] = set()
        for layer in raw:
            for dep in _get_fixref_deps(layer):
                if dep[0] not in seen:
                    all_fixref_deps.append(dep)
                    seen.add(dep[0])
        _validate_composition(raw)
        return _expand_composed(
            raw,
            template,
            fixref_deps=tuple(sorted(all_fixref_deps)),
        )
    # Single layer: existing behavior
    fixref_deps = _get_fixref_deps(raw[0])
    return [
        CollectedItem(
            fn_name=template.fn_name,
            lineno=template.lineno,
            markers=template.markers,
            kind=Parametrized(param_id=case_id),
            param_values=tuple(pv),
            is_async=template.is_async,
            fixture_deps=template.fixture_deps,
            fixref_deps=fixref_deps,
            arranged=template.arranged,
        )
        for case_id, pv in raw[0].items()
    ]


def _import_test_module(
    path: str,
    unique_name: str,
    session: Any | None,
) -> ModuleType:
    """Import the module and store it in the session cache if available.

    Raises ImportError on load failure.
    """
    # A source module collected for its doctests is normally already imported
    # under its real dotted name. Executing it again runs every class body a
    # second time, re-firing registration hooks into registries that are never
    # evicted (#2014).
    #
    # Reuse is scoped to this package because _load_module also AST-rewrites
    # asserts and injects _OxitestAssertionError. Skipping that is right for a
    # source module, whose asserts are not test assertions, and wrong for a
    # test file. Test infrastructure is canonically importable too —
    # `tests.helpers` is — so an unscoped rule would silently strip operand
    # detail from its assertions the moment it entered doctest scope.
    canonical = already_imported(path)
    if canonical is not None and canonical.__name__.startswith("oxitest."):
        module = canonical
    else:
        spec_name = dotted_name_for(path, getattr(session, "rootdir", None))
        try:
            module = _load_module(path, unique_name, spec_name=spec_name)
        except _LoadError as e:
            result = e.result
            if not isinstance(result, ErrorResult):
                # _load_module only raises _LoadError with _error_result(),
                # which always produces an ErrorResult — this branch is unreachable.
                msg = (  # pragma: no cover
                    f"_LoadError.result expected ErrorResult,"
                    f" got {type(result).__name__}"
                )
                raise TypeError(msg) from None  # pragma: no cover
            raise ImportError(result.message) from None

    # Store in session module cache if available — executor will reuse this module.
    if session is not None:
        cache = getattr(session, "module_cache", None)
        if cache is not None:
            cache.set(path, module, kind="test")

    return module


def _generator_test_message(path: str, fn_name: str, lineno: int) -> str:
    """Why a generator test cannot run, and the two ways out.

    Mirrors ``_inline_cap_message``: what is wrong, why it is wrong, then the
    remedy. Naming the fixture route is load-bearing — ``yield`` is correct
    Python in a fixture, so a message that only says "remove it" reads as a ban
    on a construct the framework elsewhere requires.
    """
    return (
        f"{fn_name} in {path} line {lineno} contains yield, so calling it "
        f"returns a generator instead of running.\n"
        f"None of the test body executes, and the test is reported as passed. "
        f"A test function returns None.\n"
        f"Hint: remove the yield, or move the generator into a fixture, where "
        f"yield is how teardown is expressed."
    )


def _refuse_generator_test(path: str, fn_name: str, fn: object, lineno: int) -> None:
    """Refuse a test function whose call returns a generator (#2067).

    Both predicates, not one: ``isasyncgenfunction`` is a separate answer, and
    an async generator is the worse case because ``iscoroutinefunction`` is
    False for it — ``_expand_item`` sets ``is_async`` from that — so it runs
    down the *sync* path and its ``await``-less body is discarded silently.

    Function-side, so it cannot see through ``functools.wraps``. That case
    belongs to the runtime guard in ``_runners.py``, which classifies the
    returned value instead.
    """
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        raise CollectionError(_generator_test_message(path, fn_name, lineno))


def _collect_items(
    members: Iterable[tuple[str, object]],
    path: str,
    *,
    collect_violations: bool,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Shared collection loop: expand items + check violations for each member."""
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = []
    for fn_name, fn in members:
        lineno = getattr(getattr(fn, "__code__", None), "co_firstlineno", 0)
        # Before the mark read, so `@oxi.mark.skip` cannot route a malformed
        # declaration past the guard: a skip is a decision about a test that
        # could have run, and this one could not.
        _refuse_generator_test(path, fn_name, fn, lineno)
        marker_names = [m.name for m in get_marks(fn)]
        items.extend(_expand_item(fn_name, lineno, marker_names, fn))
        if collect_violations:
            violations.extend(check_fn_violations(path, fn_name, fn))
    return items, violations


def _module_members(module: ModuleType) -> Iterable[tuple[str, object]]:
    """Yield (fn_name, fn) for module-level test functions."""
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("test_"):
            yield name, obj


def _class_members(module: ModuleType) -> Iterable[tuple[str, object]]:
    """Yield (fn_name, fn) for test methods in Test* classes."""
    for cls_name, cls in inspect.getmembers(module, inspect.isclass):
        if not cls_name.startswith("Test"):
            continue
        for method_name, method in inspect.getmembers(cls, inspect.isfunction):
            if not method_name.startswith("test_"):
                continue
            _propagate_class_metadata(method, cls)
            yield f"{cls_name}::{method_name}", method


def _register_inline_fixtures(module: Any, path: str, session: Any | None) -> None:
    """Register a test module's own ``@oxi.fixture`` declarations (ADR-0009 slice 5).

    The anchor is the test file itself, not its directory. Two consequences, both
    deliberate:

    * the namespace becomes the module stem, so a fixture in ``test_orders.py``
      is reached as ``fx.test_orders.conn`` — distinct from the directory's
      ``fx.api.conn``, which avoids a ``(namespace, name)`` collision the
      registrar treats as a hard error;
    * ``anchor_package_path == defining_module_path`` holds for inline fixtures
      and only for them, since a package-level anchor is a directory. That
      equality is how resolution recognises an inline fixture without a new
      field on ``ModuleSource``.

    Registration happens here rather than via the Rust bridge's fixture-module
    entry point because the module object already exists — re-importing it to
    find its fixtures would execute every module-level statement a second time.
    """
    # One guard, not two: getattr(None, "registry", None) is already None, so a
    # separate `session is None` check would be dead.
    registry = getattr(session, "registry", None)
    if registry is None:
        return
    register_module_source_fixtures(registry, module, anchor_package_path=path)


def collect_module(
    path: str,
    session: Any | None = None,
    *,
    collect_violations: bool = False,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Import a Python file with AST rewriting and return items and violations.

    If session is provided and has a _module_cache, the loaded module is stored
    in the cache so run_test can reuse it without reloading.

    If collect_violations is True, also detect strict-mode violations and return
    them as CollectedViolation objects alongside the items.
    """
    digest = hashlib.md5(path.encode(), usedforsecurity=False)
    unique_name = f"_oxitest_collect_{digest.hexdigest()[:12]}"
    module = _import_test_module(path, unique_name, session)
    _register_inline_fixtures(module, path, session)
    module_marks, mark_violations = _extract_module_marks(module, path)
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = list(mark_violations)
    for discover in (_module_members, _class_members):
        members = list(discover(module))
        _apply_module_marks(members, module_marks)
        found_items, found_viols = _collect_items(
            members, path, collect_violations=collect_violations
        )
        items.extend(found_items)
        violations.extend(found_viols)

    # Plugin collectors — discover additional test items
    _plugin_registry = (
        getattr(session, "plugin_registry", None) if session is not None else None
    )
    if _plugin_registry is not None:
        for collector in _plugin_registry.collectors:  # pragma: no cover
            collector_name = type(collector).__qualname__
            try:
                plugin_items = collector.collect(path=path, module=module)
                for item in plugin_items:
                    if isinstance(item, CollectedItem):
                        items.append(item)
                    else:
                        emit_diagnostic(
                            DiagnosticSeverity.WARNING,
                            "plugin collector",
                            f"Plugin collector {collector_name!r} returned "
                            f"{type(item).__name__!r} instead of CollectedItem "
                            f"while collecting {path!r} — item dropped",
                        )
            except Exception as exc:  # noqa: BLE001 — plugin collector is external code
                emit_diagnostic(
                    DiagnosticSeverity.WARNING,
                    "plugin collector",
                    f"Plugin collector {collector_name!r} raised "
                    f"{type(exc).__name__}: {exc} while collecting {path!r}",
                )

    # Bare-assert detection is now handled in Rust (bare_asserts.rs).
    items.sort(key=lambda x: x.lineno)
    return items, violations


def register_module_source_fixtures_for_module(
    *,
    registry: FixtureRegistry,
    fixture_module_path: str,
    anchor_package_path: str,
) -> None:
    """Bridge entry point: import a __fixtures__.py and register its fixtures.

    Called by the Rust bridge after prescan when a test module has a sibling
    __fixtures__.py. Uses standard importlib machinery so Python's cache
    handles repeat calls efficiently.

    Parameters
    ----------
    registry:
        The live FixtureRegistry for this run. Fixtures are registered into
        it using the ModuleSource variant (ADR-0009 slice 1).
    fixture_module_path:
        Absolute path to the ``__fixtures__.py`` file to import.
    anchor_package_path:
        Absolute path to the directory containing the fixture module. Used
        to derive the namespace name (the final path segment).
    """
    module = _load_declaration_module(fixture_module_path)
    if module is None:
        return
    register_module_source_fixtures(
        registry, module, anchor_package_path=anchor_package_path
    )


def register_plugin_source_fixtures_for_module(  # noqa: PLR0913 — mirrors the registrar it forwards to
    *,
    registry: FixtureRegistry,
    fixture_module_path: str,
    plugin_module: str,
    namespace: str,
    autouse_names: list[str],
    emit_notices: bool = True,
) -> None:
    """Bridge entry point: import an activated plugin's __fixtures__.py (#1717).

    The plugin counterpart of
    :func:`register_module_source_fixtures_for_module`. Shares the import
    machinery and differs only in which registrar it hands the module to —
    plugin declarations become ambient ``PluginModuleSource`` defs rather than
    anchored ``ModuleSource`` ones.

    Parameters
    ----------
    registry:
        The live FixtureRegistry for this run.
    fixture_module_path:
        Absolute path to the plugin package's ``__fixtures__.py``.
    plugin_module:
        The plugin's module path, carried on each def for diagnostics.
    namespace:
        The plugin's namespace — its module name unless overridden in
        ``[tool.oxitest.plugin_settings.<module>]``.
    autouse_names:
        Fixtures the user enabled for autouse. Arrives as a list because it
        crosses the PyO3 boundary.
    emit_notices:
        Whether to emit user-facing notices about the declarations. Workers
        pass ``False``: the coordinator has already emitted them and runs
        before any worker spawns, so repeating them multiplies each notice by
        the worker count.
    """
    module = _load_declaration_module(fixture_module_path)
    if module is None:
        return
    register_plugin_source_fixtures(
        registry,
        module,
        plugin_module=plugin_module,
        namespace=namespace,
        autouse_names=tuple(autouse_names),
        emit_notices=emit_notices,
    )


def _load_declaration_module(fixture_module_path: str) -> ModuleType | None:
    """Import a declaration home under a private module name.

    Shared by both bridge entry points rather than copied: the
    ``sys.modules.pop`` on failure is a recorded fix (HIGH-2) and a second
    copy would drift away from it.

    Returns ``None`` when no spec can be created — prescan has already flagged
    such a file, so the usual import error is left to surface at test time.
    """
    # Unsigned 64-bit hash — force positive to avoid a leading hyphen in the
    # internal module name. Python's built-in hash() may return a negative
    # integer; `:x` would format that as e.g. `-7f3a…` which contains a
    # hyphen and is therefore not a valid Python identifier (LOW-1 fix).
    key = hash(fixture_module_path) & 0xFFFF_FFFF_FFFF_FFFF
    module_name = f"_oxitest_fixture_module_{key:x}"
    spec = importlib.util.spec_from_file_location(module_name, fixture_module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except BaseException:
        # Clean up the partial module so a later re-import or introspection
        # does not see a poisoned entry (HIGH-2 fix). Re-raise so the bridge
        # sees the error and surfaces it to the user.
        sys.modules.pop(spec.name, None)
        raise
    return module
