from __future__ import annotations

__all__ = ["check_fn_violations"]

from collections.abc import Callable, Iterator
from typing import Any

from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge._metadata import get_marks
from oxitest._bridge.parametrize import ResolvedCases
from oxitest._bridge.result import CollectedViolation, ViolationKind


def _check_dict_parametrize(
    path: str,
    fn_name: str,
    fn: object,
) -> list[CollectedViolation]:
    """Return a DICT_PARAMETRIZE violation if the function uses dict-mode parametrize.

    Dict-parametrize: _oxitest_param_cases has ``is_dict_mode`` set.
    """
    layers = get_metadata(fn).param_cases
    if isinstance(layers, tuple) and any(layer.is_dict_mode for layer in layers):
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

    Applies to skip and xfail marks.
    """
    node_id = f"{path}::{fn_name}"
    return [
        CollectedViolation(
            node_id=node_id,
            kind=ViolationKind.MISSING_MARK_REASON,
            detail=mark.name,
        )
        for mark in get_marks(fn)
        if mark.name in ("skip", "xfail") and not mark.kwargs.get("reason")
    ]


def _check_single_case_parametrize(
    path: str,
    fn_name: str,
    fn: object,
) -> list[CollectedViolation]:
    """Return a SINGLE_CASE_PARAMETRIZE violation if only one case is defined."""
    layers = get_metadata(fn).param_cases
    if layers is not None and len(layers) == 1 and len(layers[0].cases) == 1:
        return [
            CollectedViolation(
                node_id=f"{path}::{fn_name}",
                kind=ViolationKind.SINGLE_CASE_PARAMETRIZE,
                detail="",
            )
        ]
    return []


_FN_VIOLATION_CHECKERS: list[Callable[[str, str, Any], list[CollectedViolation]]] = [
    _check_dict_parametrize,
    _check_missing_mark_reason,
    _check_single_case_parametrize,
]


def check_fn_violations(
    path: str,
    fn_name: str,
    fn: object,
) -> Iterator[CollectedViolation]:
    """Yield strict violations for a single test function.

    Checks dict-parametrize and missing-mark-reason violations.
    Bare-assert violations are detected separately via AST (_collect_bare_asserts).
    """
    for checker in _FN_VIOLATION_CHECKERS:
        yield from checker(path, fn_name, fn)
