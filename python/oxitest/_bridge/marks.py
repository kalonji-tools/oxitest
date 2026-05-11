"""Mark handler registry for skip/skipif/xfail/timeout/usefixtures evaluation.

Marker *conditions* are evaluated here at test execution time.
Marker *names* are collected at collection time by validate_markers() in
src/filter.rs for -m expression filtering. Both phases must agree on the
set of built-in marker names defined by BUILTIN_MARKERS in filter.rs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from oxitest._bridge.fixtures import MarkInfo, _SessionProtocol
from oxitest._bridge.result import TestResult

ExecutionWrapper = Callable[[Callable[[], TestResult]], TestResult]


@dataclasses.dataclass
class MarkEvalResult:
    """Result returned by a mark handler.

    short_circuit: return this TestResult immediately, skip execution.
    wrapper: wrap the execution with this callable.
    """

    short_circuit: TestResult | None = None
    wrapper: ExecutionWrapper | None = None


@dataclasses.dataclass
class _HandlerContext:
    """Context bundle passed to each mark handler.

    All mutable state (fn_teardowns, all_kwargs) is passed by reference;
    handlers mutate in place for usefixtures injection.
    """

    fn_raw: object
    fn: Callable[..., Any]
    all_kwargs: dict[str, Any]
    session: _SessionProtocol
    module_path: str
    fn_teardowns: list[Callable[[], None]]
    default_timeout: int | None = None


class MarkHandler:
    """Base class for mark handlers in the registry."""

    mark_name: str = ""  # subclasses must override

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        return MarkEvalResult()


class _UsefixturesHandler(MarkHandler):
    mark_name = "usefixtures"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        # NOTE: name guard removed — registry dispatch already filtered by name
        for fx_name in mark.args:
            ctx.session.get_fixture(str(fx_name), ctx.module_path, ctx.fn_teardowns)
        return MarkEvalResult()


class _SkipHandler(MarkHandler):
    mark_name = "skip"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        reason = mark.kwargs.get("reason") or (mark.args[0] if mark.args else "")
        return MarkEvalResult(
            short_circuit=TestResult(status="skipped", message=str(reason))
        )


class _SkipIfHandler(MarkHandler):
    mark_name = "skipif"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        if mark.args[0]:
            reason = str(mark.kwargs.get("reason", ""))
            return MarkEvalResult(
                short_circuit=TestResult(status="skipped", message=reason)
            )
        return MarkEvalResult()


class _XFailHandler(MarkHandler):
    mark_name = "xfail"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        strict = mark.kwargs.get("strict", True)
        reason = mark.kwargs.get("reason", "")

        def xfail_wrapper(next_fn: Callable[[], TestResult]) -> TestResult:
            result = next_fn()
            if result.status == "skipped":
                return result
            if result.status in ("passed", "warned"):
                return TestResult(status="xpassed", strict=bool(strict))
            return TestResult(status="xfailed", message=str(reason))

        return MarkEvalResult(wrapper=xfail_wrapper)


class _TimeoutHandler(MarkHandler):
    mark_name = "timeout"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        seconds = int(mark.kwargs["seconds"])  # type: ignore[arg-type]  # ty: ignore

        from oxitest._bridge._timeout import OxitestTimeoutError, _timeout_context

        def timeout_wrapper(next_fn: Callable[[], TestResult]) -> TestResult:
            try:
                with _timeout_context(seconds):
                    return next_fn()
            except OxitestTimeoutError:
                return TestResult(
                    status="timeout",
                    message=f"Timed out after {seconds}s",
                )

        return MarkEvalResult(wrapper=timeout_wrapper)


# NOTE: @oxitest.mark.timeout combined with @oxitest.mark.xfail is not supported.
# Behaviour is undefined — see docs/superpowers/specs/2026-05-03-timeout-design.md.
_MARK_REGISTRY: dict[str, MarkHandler] = {
    h.mark_name: h
    for h in [
        _UsefixturesHandler(),
        _SkipHandler(),
        _SkipIfHandler(),
        _XFailHandler(),
        _TimeoutHandler(),
    ]
}


def evaluate_marks(
    marks: list[MarkInfo], ctx: _HandlerContext
) -> tuple[TestResult | None, list[ExecutionWrapper]]:
    """Run marks through the handler registry.

    Returns (short_circuit, wrappers).
    short_circuit: if not None, return it immediately without running the test.
    wrappers: callables to compose around the test execution, in order added.
    """
    wrappers: list[ExecutionWrapper] = []
    for mark in marks:
        handler = _MARK_REGISTRY.get(mark.name)
        if handler is None:
            continue
        result = handler.handle(mark, ctx)
        if result.short_circuit is not None:
            return result.short_circuit, []
        if result.wrapper is not None:
            wrappers.append(result.wrapper)
    return None, wrappers
