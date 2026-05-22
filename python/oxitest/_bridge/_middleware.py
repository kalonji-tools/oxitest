from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "BareAssertMiddleware",
    "ExecutionPlan",
    "Middleware",
    "TimeoutMiddleware",
    "build_pipeline",
]

import inspect
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._timeout import OxitestTimeoutError, make_timeout_wrapper
from oxitest._bridge.executor import (
    _compose,
    _find_bare_asserts,
    _run_base_async,
)
from oxitest._bridge.fixtures import FixtureTeardownWarning
from oxitest._bridge.result import TestResult, _error_result


@dataclass
class ExecutionPlan:
    """Immutable context passed through the middleware stack."""

    fn: Callable[..., Any]
    fn_name: str
    kwargs: dict[str, Any]
    marks: list[MarkInfo]
    no_message_lines: list[int]
    is_async: bool
    default_timeout: int | None
    backend: Any  # AsyncBackend | None
    shared_session: Any  # SharedAsyncSession | None


class Middleware(Protocol):
    def apply(
        self,
        plan: ExecutionPlan,
        next_fn: Callable[[], TestResult],
    ) -> Callable[[], TestResult]:
        """Wrap or replace next_fn. Return next_fn unchanged to skip."""
        ...


def build_pipeline(
    middlewares: Sequence[Middleware],
    plan: ExecutionPlan,
    base: Callable[[], TestResult],
) -> Callable[[], TestResult]:
    """Compose middlewares around a base runner. Last in list = outermost."""
    execute = base
    for mw in reversed(middlewares):
        execute = mw.apply(plan, execute)
    return execute


class TimeoutMiddleware:
    """Adds timeout wrapper if no per-test @timeout mark and default_timeout is set."""

    def __init__(self, default_timeout: int | None) -> None:
        self._default = default_timeout

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if self._default is not None and not any(
            m.name == "timeout" for m in plan.marks
        ):
            return _compose(make_timeout_wrapper(self._default), next_fn)
        return next_fn


class BareAssertMiddleware:
    """Resolves bare-assert line map from the module."""

    def __init__(self, module: Any) -> None:
        self._module = module

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        _bare_map: dict[str, list[int]] = getattr(
            self._module, "_oxitest_bare_asserts", {}
        )
        _simple_fn_name = plan.fn_name.split("::")[-1]
        plan.no_message_lines = _bare_map.get(
            _simple_fn_name, _find_bare_asserts(plan.fn)
        )
        return next_fn


class AsyncDepGuardMiddleware:
    """Rejects async fixture values passed to sync tests."""

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not inspect.iscoroutinefunction(plan.fn):
            for k, v in plan.kwargs.items():
                if inspect.iscoroutine(v) or inspect.isasyncgen(v):
                    if inspect.iscoroutine(v):
                        v.close()
                    _msg = (
                        f"async fixture '{k}' cannot be used by sync test "
                        f"'{plan.fn_name}' \u2014 make the test async def"
                    )
                    return lambda: _error_result(_msg)
        return next_fn


class AsyncBridgeMiddleware:
    """Replaces base runner with async bridge when fn is async."""

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not inspect.iscoroutinefunction(plan.fn):
            return next_fn

        from oxitest._bridge._async_backend import AsyncioBackend
        from oxitest._bridge._errors import FixtureSetupError

        backend = plan.backend or AsyncioBackend()

        _timeout_secs: int | None = None
        for m in plan.marks:
            if m.name == "timeout":
                _timeout_secs = int(m.kwargs["seconds"])  # type: ignore[arg-type]  # ty: ignore
                break
        if _timeout_secs is None:
            _timeout_secs = plan.default_timeout

        async def _async_core() -> TestResult:  # pragma: no cover
            resolved: dict[str, Any] = {}
            async_teardowns: list[tuple[str, Any]] = []
            for k, v in plan.kwargs.items():
                if inspect.isasyncgen(v):
                    try:
                        resolved[k] = await anext(v)
                        async_teardowns.append((k, v))
                    except Exception as exc:
                        return _error_result(str(FixtureSetupError(k, exc)))
                elif inspect.iscoroutine(v):
                    try:
                        resolved[k] = await v
                    except Exception as exc:
                        return _error_result(str(FixtureSetupError(k, exc)))
                else:
                    resolved[k] = v
            try:
                if _timeout_secs is not None:
                    import asyncio

                    try:
                        return await asyncio.wait_for(
                            _run_base_async(plan.fn, resolved, plan.no_message_lines),
                            timeout=_timeout_secs,
                        )
                    except TimeoutError:
                        raise OxitestTimeoutError() from None
                return await _run_base_async(plan.fn, resolved, plan.no_message_lines)
            finally:
                for name, gen in reversed(async_teardowns):
                    try:
                        await anext(gen)
                    except StopAsyncIteration:
                        pass
                    except Exception as exc:
                        warnings.warn(
                            FixtureTeardownWarning(
                                f"error in teardown of fixture '{name}': {exc}"
                            ),
                            stacklevel=2,
                        )

        if plan.shared_session is not None:

            def _base() -> TestResult:  # pragma: no cover
                return plan.shared_session.run(_async_core())
        else:

            def _base() -> TestResult:
                return backend.run(_async_core())

        return _base
