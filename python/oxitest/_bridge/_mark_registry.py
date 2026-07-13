"""Mark handler registry for skip/xfail/timeout/usefixtures evaluation.

Marker *conditions* are evaluated here at test execution time.
Marker *names* are collected at collection time by validate_markers() in
src/filter.rs for -m expression filtering. Both phases must agree on the
set of built-in marker names defined by BUILTIN_MARKERS in filter.rs.
"""

from __future__ import annotations

__all__ = [
    "ExecutionWrapper",
    "MarkAction",
    "MarkHandler",
    "MarkWrapper",
    "PassThrough",
    "ShortCircuit",
    "Wrap",
    "_PluginMarkHandler",
    "evaluate_marks",
]

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from oxitest._bridge._fixture_session import _SessionProtocol
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._timeout import extract_timeout_seconds, make_timeout_wrapper
from oxitest._bridge.result import (
    ErrorResult,
    FailedResult,
    SkippedResult,
    StatusKind,
    TestResult,
    XFailedResult,
    XPassedResult,
)

MarkWrapper = Callable[[Callable[[], TestResult]], TestResult]

#: Backward-compatible alias — external consumers may still use this name.
ExecutionWrapper = MarkWrapper


@dataclasses.dataclass(frozen=True, slots=True)
class ShortCircuit:
    """Mark evaluation result: skip test execution, return this result."""

    result: TestResult


@dataclasses.dataclass(frozen=True, slots=True)
class Wrap:
    """Mark evaluation result: wrap the test execution with this callable."""

    wrapper: MarkWrapper


@dataclasses.dataclass(frozen=True, slots=True)
class PassThrough:
    """Mark evaluation result: no effect on execution."""


MarkAction = ShortCircuit | Wrap | PassThrough
_PASS_THROUGH = PassThrough()


class MarkHandler(ABC):
    """Base class for mark handlers in the registry."""

    mark_name: str = ""  # subclasses must override

    @abstractmethod
    def handle(self, mark: MarkInfo) -> MarkAction: ...


class _SkipHandler(MarkHandler):
    mark_name = "skip"

    def handle(self, mark: MarkInfo) -> MarkAction:
        """Short-circuit test execution with a `skipped` result."""
        reason = mark.kwargs.get("reason") or (mark.args[0] if mark.args else "")
        return ShortCircuit(SkippedResult(message=str(reason)))


class _XFailHandler(MarkHandler):
    mark_name = "xfail"

    def handle(self, mark: MarkInfo) -> MarkAction:
        """Wrap execution to convert failures to `xfailed` and passes to `xpassed`."""
        strict = mark.kwargs.get("strict", True)
        reason = mark.kwargs.get("reason", "")
        raises_raw = mark.kwargs.get("raises", None)
        # Extract __name__ up front so the closure captures a plain str | None
        # and ty does not need to resolve .__name__ on object inside the wrapper.
        raises_name: str | None = (
            getattr(raises_raw, "__name__", None) if raises_raw is not None else None
        )

        def xfail_wrapper(next_fn: Callable[[], TestResult]) -> TestResult:
            result = next_fn()
            if result.status is StatusKind.SKIPPED:
                return result
            if result.status in (StatusKind.PASSED, StatusKind.WARNED):
                return XPassedResult(strict=bool(strict))
            if (
                raises_name is not None
                and isinstance(result, (FailedResult, ErrorResult))
                and result.exc_type != raises_name
            ):
                return result
            return XFailedResult(message=str(reason))

        return Wrap(xfail_wrapper)


class _TimeoutHandler(MarkHandler):
    mark_name = "timeout"

    def handle(self, mark: MarkInfo) -> MarkAction:
        """Wrap execution with a deadline; raises `OxitestTimeoutError` if exceeded."""
        seconds = extract_timeout_seconds(mark.kwargs)
        return Wrap(make_timeout_wrapper(seconds))


class _PluginMarkHandler(MarkHandler):
    """Adapter: wraps a plugin execution wrapper as a MarkHandler."""

    def __init__(self, pw: Any) -> None:
        self.mark_name = pw.marker
        self._pw = pw

    def handle(self, mark: MarkInfo) -> MarkAction:
        args = {**dict(enumerate(mark.args)), **mark.kwargs}
        pw = self._pw

        def wrapper(
            next_fn: Callable[[], TestResult],
            _w: Any = pw,
            _a: dict[int | str, Any] = args,
        ) -> TestResult:
            return _w.wrap(test_fn=next_fn, marker_args=_a)

        return Wrap(wrapper)


# Registry pattern: module-level dict comprehension. Appropriate for a
# fixed set of handlers known at import time. Plugins extend this via
# evaluate_marks(plugin_handlers=...) rather than mutating the dict.
# Compare: BuiltinFixture._registry (auto-reg), FixtureRegistry (instance),
# PluginRegistry (dataclass + cached_property).
# NOTE: @oxitest.mark.timeout combined with @oxitest.mark.xfail is not supported.
# Behaviour is undefined — see docs/superpowers/specs/2026-05-03-timeout-design.md.
_MARK_REGISTRY: dict[str, MarkHandler] = {
    h.mark_name: h
    for h in [
        _SkipHandler(),
        _XFailHandler(),
        _TimeoutHandler(),
    ]
}

_BUILTIN_HANDLER_NAMES: frozenset[str] = frozenset(_MARK_REGISTRY)


def evaluate_marks(
    marks: Sequence[MarkInfo],
    session: _SessionProtocol,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    plugin_handlers: Sequence[MarkHandler] = (),
) -> tuple[TestResult | None, list[MarkWrapper]]:
    """Run marks through the handler registry.

    Returns (short_circuit, wrappers).
    short_circuit: if not None, return it immediately without running the test.
    wrappers: callables to compose around the test execution, in order added.
    plugin_handlers: additional handlers from plugin execution wrappers.
    """
    registry = _MARK_REGISTRY
    if plugin_handlers:
        registry = {**_MARK_REGISTRY, **{h.mark_name: h for h in plugin_handlers}}
    wrappers: list[MarkWrapper] = []
    for mark in marks:
        if mark.name == "usefixtures":
            for fx_name in mark.args:
                session.get_fixture(str(fx_name), module_path, fn_teardowns)
            continue
        handler = registry.get(mark.name)
        if handler is None:
            continue
        action = handler.handle(mark)
        if isinstance(action, ShortCircuit):
            return action.result, []
        if isinstance(action, Wrap):
            wrappers.append(action.wrapper)
    return None, wrappers
