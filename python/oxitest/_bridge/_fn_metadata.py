"""Centralized metadata registry for decorated test functions.

Replaces scattered setattr/getattr calls with magic string keys
(_oxitest_marks, _oxitest_param_cases, _oxitest_fixture_name).

Metadata is stored as a single `_oxitest_meta` attribute on the
function object itself, so it is garbage-collected with the function
and there is no stale-id problem from `id()` reuse.
"""

from __future__ import annotations

__all__ = ["FunctionMetadata", "_update", "get_metadata", "get_or_create"]

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._mark_api import MarkInfo
    from oxitest._bridge.parametrize import ResolvedCases

_ATTR = "_oxitest_meta"


@dataclass(frozen=True, slots=True)
class FunctionMetadata:
    marks: tuple[MarkInfo, ...] = ()
    param_cases: tuple[ResolvedCases, ...] = ()
    fixture_name: str = ""


def get_metadata(fn: object) -> FunctionMetadata:
    """Return metadata for fn, or a default (empty) instance if none registered."""
    return getattr(fn, _ATTR, None) or FunctionMetadata()


def get_or_create(fn: object) -> FunctionMetadata:
    """Return metadata for fn, creating and registering it if needed."""
    meta = getattr(fn, _ATTR, None)
    if meta is None:
        meta = FunctionMetadata()
        setattr(fn, _ATTR, meta)
    return meta


def _update(fn: object, **changes: Any) -> FunctionMetadata:
    """Replace metadata fields on *fn*, returning the new frozen instance."""
    meta = get_or_create(fn)
    new_meta = dataclasses.replace(meta, **changes)
    setattr(fn, _ATTR, new_meta)
    return new_meta
