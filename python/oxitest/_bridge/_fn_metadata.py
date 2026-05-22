"""Centralized metadata registry for decorated test functions.

Replaces scattered setattr/getattr calls with magic string keys
(_oxitest_marks, _oxitest_param_cases, _oxitest_fixture_name).

Metadata is stored as a single ``_oxitest_meta`` attribute on the
function object itself, so it is garbage-collected with the function
and there is no stale-id problem from ``id()`` reuse.
"""

from __future__ import annotations

__all__ = ["FunctionMetadata", "get_metadata", "get_or_create"]

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._mark_api import MarkInfo

_ATTR = "_oxitest_meta"


@dataclass
class FunctionMetadata:
    marks: list[MarkInfo] = field(default_factory=list)
    param_cases: Any = None  # _DictCases | _DataclassCases | None
    fixture_name: str | None = None


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
