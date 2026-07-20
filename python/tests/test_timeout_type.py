"""Tests for Timeout sum type, parse_timeout, and TimeoutMiddleware dispatch."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._middleware import ExecutionPlan, TimeoutMiddleware
from oxitest._bridge._timeout import (
    TimeoutOff,
    TimeoutSet,
    parse_timeout,
)
from oxitest._bridge.result import PassedResult, TestResult


def test_parse_timeout_none_yields_timeout_off() -> None:
    """None at the config boundary maps to TimeoutOff (no wrapper applied)."""
    result = parse_timeout(None)
    assert isinstance(result, TimeoutOff), "None must map to TimeoutOff"


def test_parse_timeout_int_yields_timeout_set() -> None:
    """A positive int at the config boundary maps to TimeoutSet carrying that value."""
    result = parse_timeout(60)
    assert isinstance(result, TimeoutSet), "int must map to TimeoutSet"
    assert result.seconds == 60, "TimeoutSet must carry the int value as seconds"


def test_parse_timeout_zero_yields_timeout_set_zero() -> None:
    """Zero maps to TimeoutSet(0) — fires immediately, which is intentional."""
    result = parse_timeout(0)
    assert isinstance(result, TimeoutSet), (
        "0 must map to TimeoutSet (fires immediately)"
    )
    assert result.seconds == 0, "TimeoutSet must carry 0 as seconds"


def _make_plan(*, marks: tuple[Any, ...] = ()) -> ExecutionPlan:
    def _fn() -> None:
        pass

    return ExecutionPlan(
        fn=_fn,
        fn_name="t",
        kwargs=MappingProxyType({}),
        marks=marks,
        no_message_lines=(),
        is_async=False,
    )


def _base() -> TestResult:
    return PassedResult()


def test_timeout_middleware_off_passes_through() -> None:
    """TimeoutOff variant leaves next_fn completely unwrapped."""
    mw = TimeoutMiddleware(timeout=TimeoutOff())
    plan = _make_plan()
    wrapped = mw.apply(plan=plan, next_fn=_base)
    assert wrapped is _base, "TimeoutOff must pass next_fn through unchanged"


def test_timeout_middleware_set_wraps_next() -> None:
    """TimeoutSet variant wraps next_fn with a timeout enforcement callable."""
    mw = TimeoutMiddleware(timeout=TimeoutSet(60))
    plan = _make_plan()
    wrapped = mw.apply(plan=plan, next_fn=_base)
    assert wrapped is not _base, "TimeoutSet must wrap next_fn"


def test_timeout_middleware_defers_to_mark() -> None:
    """When a @mark.timeout is present, middleware defers (mark wrapper handles it)."""
    mark = MarkInfo(name="timeout", args=(), kwargs=MappingProxyType({"seconds": 5}))
    mw = TimeoutMiddleware(timeout=TimeoutSet(60))
    plan = _make_plan(marks=(mark,))
    wrapped = mw.apply(plan=plan, next_fn=_base)
    assert wrapped is _base, "@mark.timeout must override middleware default"
