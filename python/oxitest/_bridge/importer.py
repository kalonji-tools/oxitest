from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import pathlib
from collections.abc import Callable
from types import ModuleType
from typing import Any, cast

from oxitest._bridge._loader import _load_module, _LoadError
from oxitest._bridge._mark_api import _append_mark
from oxitest._bridge._metadata import get_marks
from oxitest._bridge.fixtures import Fixtures
from oxitest._bridge.parametrize import _DataclassCases, _DictCases
from oxitest._bridge.result import CollectedItem, CollectedViolation, ViolationKind


def _propagate_class_marks(fn: object, cls: object) -> None:
    """Copy usefixtures marks from a class onto a test method.

    Called at collection time when a test method is collected from a class
    that carries @oxitest.mark.usefixtures. skip/skipif/xfail are NOT
    propagated — those are function-level concerns only.
    """
    for m in get_marks(cls):
        if m.name == "usefixtures":
            _append_mark(cast(Any, fn), m)


def _expand_item(
    fn_name: str,
    lineno: int,
    marker_names: list[str],
    fn: object,
) -> list[CollectedItem]:
    """Return one CollectedItem per parametrize case, or a single item if no cases."""
    param_cases: _DictCases | _DataclassCases | None = getattr(
        fn, "_oxitest_param_cases", None
    )
    if param_cases is None:
        return [
            CollectedItem(
                fn_name=fn_name,
                lineno=lineno,
                markers=marker_names,
                param_id=None,
                param_values=[],
            )
        ]
    return [
        CollectedItem(
            fn_name=fn_name,
            lineno=lineno,
            markers=marker_names,
            param_id=case_id,
            param_values=list(pv),
        )
        for case_id, pv in param_cases.items()
    ]


def _check_dict_parametrize(
    path: str,
    fn_name: str,
    fn: object,
) -> list[CollectedViolation]:
    """Return a DICT_PARAMETRIZE violation if the function uses dict-mode parametrize.

    Dict-parametrize: _oxitest_param_cases is a _DictCases instance.
    """
    param_cases = getattr(fn, "_oxitest_param_cases", None)
    if isinstance(param_cases, _DictCases):
        return [
            CollectedViolation(
                node_id=f"{path}::{fn_name}",
                kind=ViolationKind.DICT_PARAMETRIZE,
                detail="",
            )
        ]
    return []


def _check_missing_mark_reason(
    path: str,
    fn_name: str,
    fn: object,
) -> list[CollectedViolation]:
    """Return MISSING_MARK_REASON violations for marks without reason=.

    Applies to skip, skipif, and xfail marks.
    """
    node_id = f"{path}::{fn_name}"
    return [
        CollectedViolation(
            node_id=node_id,
            kind=ViolationKind.MISSING_MARK_REASON,
            detail=mark.name,
        )
        for mark in get_marks(fn)
        if mark.name in ("skip", "skipif", "xfail") and "reason" not in mark.kwargs
    ]


_FN_VIOLATION_CHECKERS: list[Callable[[str, str, Any], list[CollectedViolation]]] = [
    _check_dict_parametrize,
    _check_missing_mark_reason,
]


def _check_fn_violations(
    path: str,
    fn_name: str,
    fn: object,
) -> list[CollectedViolation]:
    """Return strict violations for a single test function.

    Checks dict-parametrize and missing-mark-reason violations.
    Bare-assert violations are detected separately via AST (_collect_bare_asserts).
    """
    return [v for checker in _FN_VIOLATION_CHECKERS for v in checker(path, fn_name, fn)]


def _shallow_walk_asserts(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[int]:
    """Return line numbers of bare `assert` statements in func_node.

    Does NOT descend into nested function definitions, so inner helpers
    whose bare asserts should not be attributed to the enclosing test are
    correctly excluded.
    """
    lines: list[int] = []
    queue = list(ast.iter_child_nodes(func_node))
    while queue:
        node = queue.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # prune: do not recurse into nested functions
        if isinstance(node, ast.Assert) and node.msg is None:
            lines.append(node.lineno)
        queue.extend(ast.iter_child_nodes(node))
    return sorted(lines)


def _collect_bare_asserts(path: str) -> list[CollectedViolation]:
    """Parse the source file and return bare-assert violations for test functions."""
    try:
        source = pathlib.Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
    except (SyntaxError, OSError):
        return []

    violations: list[CollectedViolation] = []

    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            lines = _shallow_walk_asserts(node)
            if lines:
                violations.append(
                    CollectedViolation(
                        node_id=f"{path}::{node.name}",
                        kind=ViolationKind.BARE_ASSERT,
                        detail=" ".join(str(ln) for ln in lines),
                    )
                )
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and item.name.startswith("test_"):
                    lines = _shallow_walk_asserts(item)
                    if lines:
                        violations.append(
                            CollectedViolation(
                                node_id=f"{path}::{node.name}::{item.name}",
                                kind=ViolationKind.BARE_ASSERT,
                                detail=" ".join(str(ln) for ln in lines),
                            )
                        )

    return violations


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
) -> None:
    """Scan module for Fixtures() instances and register them with the session."""
    if session is None:
        return
    registry = getattr(session, "_registry", None)
    if registry is None:
        return

    for attr_name in vars(module):
        obj = getattr(module, attr_name)
        if isinstance(obj, Fixtures):
            for defn in obj._defs:
                registry.register(dataclasses.replace(defn, conftest_path=path))


def _discover_module_items(
    module: ModuleType,
    path: str,
    collect_violations: bool,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Discover test functions at module level."""
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        lineno = getattr(getattr(obj, "__code__", None), "co_firstlineno", 0)
        marker_names = [m.name for m in get_marks(obj)]
        items.extend(_expand_item(name, lineno, marker_names, obj))
        if collect_violations:
            violations.extend(_check_fn_violations(path, name, obj))

    return items, violations


def _discover_class_items(
    module: ModuleType,
    path: str,
    collect_violations: bool,
) -> tuple[list[CollectedItem], list[CollectedViolation]]:
    """Discover test methods inside Test* classes."""
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = []

    for cls_name, cls in inspect.getmembers(module, inspect.isclass):
        if not cls_name.startswith("Test"):
            continue
        for method_name, method in inspect.getmembers(cls, inspect.isfunction):
            if not method_name.startswith("test_"):
                continue
            _propagate_class_marks(method, cls)
            lineno = getattr(getattr(method, "__code__", None), "co_firstlineno", 0)
            fn_name = f"{cls_name}::{method_name}"
            marker_names = [m.name for m in get_marks(method)]
            items.extend(_expand_item(fn_name, lineno, marker_names, method))
            if collect_violations:
                violations.extend(_check_fn_violations(path, fn_name, method))

    return items, violations


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
    _register_module_fixtures(module, path, session)
    items: list[CollectedItem] = []
    violations: list[CollectedViolation] = []
    for discover in (_discover_module_items, _discover_class_items):
        found_items, found_viols = discover(module, path, collect_violations)
        items.extend(found_items)
        violations.extend(found_viols)

    # Plugin collectors — discover additional test items
    from oxitest._bridge.plugin_loader import get_registry

    for collector in get_registry().collectors:  # pragma: no cover
        try:
            plugin_items = collector.collect(path, module)
            for item in plugin_items:
                if isinstance(item, CollectedItem):
                    items.append(item)
        except Exception:
            import traceback

            traceback.print_exc()

    if collect_violations:
        violations.extend(_collect_bare_asserts(path))
    items.sort(key=lambda x: x.lineno)
    return items, violations
