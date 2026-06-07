from __future__ import annotations

__all__ = ["collect_module"]

import dataclasses
import hashlib
import inspect
import itertools
from collections.abc import Iterable
from types import ModuleType
from typing import Any, cast, get_type_hints

from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_registry import _fixture_inner_type
from oxitest._bridge._fixtures import Fixtures
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge._loader import _load_module, _LoadError
from oxitest._bridge._mark_api import MarkInfo, _append_mark
from oxitest._bridge._metadata import get_marks
from oxitest._bridge._violation_checkers import check_fn_violations
from oxitest._bridge.parametrize import ResolvedCases
from oxitest._bridge.result import CollectedItem, CollectedViolation, ViolationKind


def _get_fixture_names(fn: object) -> tuple[str, ...]:
    """Extract parameter names annotated with Fixture[T] that need registry resolution.

    Excludes built-in fixtures (resolved by type, not name) and the bare
    ``Fixtures`` accessor type, since those never go through the registry.
    """
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001
        return ()
    names: list[str] = []
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        if hint is Fixtures:
            continue
        is_fix, inner = _fixture_inner_type(hint)
        if is_fix and BuiltinFixture.for_type(inner) is None:
            names.append(param_name)
    return tuple(names)


def _propagate_class_marks(fn: object, cls: object) -> None:
    """Copy usefixtures marks from a class onto a test method.

    Called at collection time when a test method is collected from a class
    that carries @oxitest.mark.usefixtures. skip/xfail are NOT
    propagated — those are function-level concerns only.
    """
    for m in get_marks(cls):
        if m.name == "usefixtures":
            _append_mark(cast(Any, fn), m)


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

        try:
            entry(_sentinel)  # ty: ignore[call-top-callable]
            marks = get_marks(_sentinel)
            return marks[-1] if marks else None
        except Exception:  # noqa: BLE001
            return None
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
                _append_mark(cast(Any, fn), mark)


def _validate_composition(layers: tuple[ResolvedCases, ...]) -> None:
    """Validate composition rules for composed ResolvedCases layers.

    Raises TypeError if:
    - Only 1 partial layer (needs 2+)
    - Fields are incomplete (union doesn't cover all dataclass fields)
    """
    if len(layers) == 1:
        raise TypeError(
            "parametrize composition requires at least 2 stacked"
            " @parametrize layers with partial() values."
            " Use a full dataclass instance for single-layer parametrize."
        )
    target_type = layers[0].param_type
    assert target_type is not None  # composed layers always have param_type
    all_provided = frozenset().union(*(layer.provided_fields for layer in layers))
    all_fields = {f.name for f in dataclasses.fields(target_type)}
    missing = all_fields - all_provided
    if missing:
        raise TypeError(
            f"parametrize composition: missing field(s) {sorted(missing)!r}"
            f" on '{target_type.__name__}'."
            " The union of all layers must cover every field."
        )


def _expand_composed(
    layers: tuple[ResolvedCases, ...],
    fn_name: str,
    lineno: int,
    marker_names: list[str],
    is_async: bool,
    fixture_names: tuple[str, ...],
    fixref_names: tuple[str, ...] = (),
) -> list[CollectedItem]:
    """Expand composed ResolvedCases layers via cartesian product."""
    _validate_composition(layers)
    layer_items = [layer.items() for layer in layers]
    items: list[CollectedItem] = []
    for combo in itertools.product(*layer_items):
        compound_id = "-".join(case_id for case_id, _ in combo)
        merged_pv: list[tuple[str, str]] = []
        for _, pv in combo:
            merged_pv.extend(pv)
        items.append(
            CollectedItem(
                fn_name=fn_name,
                lineno=lineno,
                markers=tuple(marker_names),
                param_id=compound_id,
                param_values=tuple(merged_pv),
                is_async=is_async,
                fixture_names=fixture_names,
                fixref_names=fixref_names,
            )
        )
    return items


def _get_fixref_names(layer: ResolvedCases) -> tuple[str, ...]:
    """Extract fixture-ref field names from a parametrize layer."""
    return layer.fixref_fields


def _expand_item(
    fn_name: str,
    lineno: int,
    marker_names: list[str],
    fn: object,
) -> list[CollectedItem]:
    """Return one CollectedItem per parametrize case, or a single item if no cases."""
    is_async = inspect.iscoroutinefunction(fn)
    fixture_names = _get_fixture_names(fn)
    raw = get_metadata(fn).param_cases
    if raw is None:
        return [
            CollectedItem(
                fn_name=fn_name,
                lineno=lineno,
                markers=tuple(marker_names),
                param_id=None,
                param_values=(),
                is_async=is_async,
                fixture_names=fixture_names,
            )
        ]
    layers = cast(tuple, raw)
    # Composition: all layers are composed (partial) ResolvedCases
    if len(layers) > 1 or layers[0].is_composed:
        # Merge fixref_fields from all composition layers
        all_fixrefs: set[str] = set()
        for layer in layers:
            all_fixrefs.update(_get_fixref_names(layer))
        return _expand_composed(
            layers,
            fn_name,
            lineno,
            marker_names,
            is_async,
            fixture_names,
            fixref_names=tuple(sorted(all_fixrefs)),
        )
    # Single layer: existing behavior
    fixref_names = _get_fixref_names(layers[0])
    return [
        CollectedItem(
            fn_name=fn_name,
            lineno=lineno,
            markers=tuple(marker_names),
            param_id=case_id,
            param_values=tuple(pv),
            is_async=is_async,
            fixture_names=fixture_names,
            fixref_names=fixref_names,
        )
        for case_id, pv in layers[0].items()
    ]


def _import_test_module(
    path: str,
    unique_name: str,
    session: Any | None,
) -> ModuleType:
    """Import the module and store it in the session cache if available.

    Raises ImportError on load failure.
    """
    try:
        module = _load_module(path, unique_name)
    except _LoadError as e:
        raise ImportError(e.result.message) from None

    # Store in session module cache if available — executor will reuse this module.
    if session is not None:
        cache = getattr(session, "_module_cache", None)
        if cache is not None:
            cache.set(path, module)

    return module


def _register_module_fixtures(
    module: ModuleType,
    path: str,
    session: Any | None,
) -> list[CollectedViolation]:
    """Scan module for Fixtures() instances and register them with the session."""
    if session is None:
        return []
    registry = getattr(session, "_registry", None)
    if registry is None:
        return []

    violations: list[CollectedViolation] = []
    for attr_name in vars(module):
        obj = getattr(module, attr_name)
        if isinstance(obj, Fixtures):
            for defn in obj._defs:
                violations.extend(
                    registry.register(dataclasses.replace(defn, conftest_path=path))
                )
    return violations


def _collect_items(
    members: Iterable[tuple[str, object]],
    path: str,
    collect_violations: bool,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Shared collection loop: expand items + check violations for each member."""
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = []
    for fn_name, fn in members:
        lineno = getattr(getattr(fn, "__code__", None), "co_firstlineno", 0)
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
            _propagate_class_marks(method, cls)
            yield f"{cls_name}::{method_name}", method


def collect_module(
    path: str,
    session: Any | None = None,
    collect_violations: bool = False,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Import a Python file with AST rewriting and return items and violations.

    If session is provided and has a _module_cache, the loaded module is stored
    in the cache so run_test can reuse it without reloading.

    If collect_violations is True, also detect strict-mode violations and return
    them as CollectedViolation objects alongside the items.
    """
    unique_name = f"_oxitest_collect_{hashlib.md5(path.encode()).hexdigest()[:12]}"  # noqa: S324
    module = _import_test_module(path, unique_name, session)
    fixture_violations = _register_module_fixtures(module, path, session)
    module_marks, mark_violations = _extract_module_marks(module, path)
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = list(mark_violations) + fixture_violations
    for discover in (_module_members, _class_members):
        members = list(discover(module))
        _apply_module_marks(members, module_marks)
        found_items, found_viols = _collect_items(members, path, collect_violations)
        items.extend(found_items)
        violations.extend(found_viols)

    # Plugin collectors — discover additional test items
    _plugin_registry = getattr(session, "_plugin_registry", None)

    if _plugin_registry is not None:
        for collector in _plugin_registry.collectors:  # pragma: no cover
            try:
                plugin_items = collector.collect(path, module)
                items.extend(
                    item for item in plugin_items if isinstance(item, CollectedItem)
                )
            except Exception:
                import traceback

                traceback.print_exc()

    # Bare-assert detection is now handled in Rust (bare_asserts.rs).
    items.sort(key=lambda x: x.lineno)
    return items, violations
