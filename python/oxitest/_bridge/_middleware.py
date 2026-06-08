from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "BareAssertMiddleware",
    "ExecutionPlan",
    "Middleware",
    "MiddlewareBuilder",
    "TimeoutMiddleware",
    "build_pipeline",
]

import inspect
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import MarkWrapper
from oxitest._bridge._timeout import OxitestTimeoutError, make_timeout_wrapper
from oxitest._bridge.result import TestResult


def _compose(
    wrapper: MarkWrapper, inner: Callable[[], TestResult]
) -> Callable[[], TestResult]:
    """Return a callable that runs wrapper(inner).

    Using a named function avoids the loop-variable capture problem
    (ruff B023) that a bare lambda inside a for-loop would cause.
    """
    return lambda: wrapper(inner)


@dataclass
class ExecutionPlan:
    """Immutable context passed through the middleware stack."""

    fn: Callable[..., Any]
    fn_name: str
    kwargs: dict[str, Any]
    marks: list[MarkInfo]
    no_message_lines: tuple[int, ...]
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
        plan.no_message_lines = tuple(_bare_map.get(_simple_fn_name, []))
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

                    from oxitest._bridge.result import _error_result

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
        from oxitest._bridge._fixture_context import FixtureTeardownWarning
        from oxitest._bridge._runners import run_base_async

        backend = plan.backend or AsyncioBackend()

        _timeout_secs: int | None = None
        for m in plan.marks:
            if m.name == "timeout":
                _timeout_secs = int(m.kwargs["seconds"])  # type: ignore[arg-type]  # ty: ignore
                break
        if _timeout_secs is None:
            _timeout_secs = plan.default_timeout

        async def _async_core() -> TestResult:
            # Phase 1: Unpack async fixtures — await coroutines and advance
            # async generators to their first yielded value.
            resolved: dict[str, Any] = {}
            async_teardowns: list[tuple[str, Any]] = []
            for k, v in plan.kwargs.items():
                if inspect.isasyncgen(v):
                    try:
                        resolved[k] = await anext(v)
                        async_teardowns.append((k, v))
                    except Exception as exc:
                        from oxitest._bridge.result import _error_result

                        return _error_result(str(FixtureSetupError(k, exc)))
                elif inspect.iscoroutine(v):
                    try:
                        resolved[k] = await v
                    except Exception as exc:
                        from oxitest._bridge.result import _error_result

                        return _error_result(str(FixtureSetupError(k, exc)))
                else:
                    resolved[k] = v
            # Phase 2: Run the test body, applying optional timeout.
            try:
                if _timeout_secs is not None:
                    import asyncio

                    try:
                        return await asyncio.wait_for(
                            run_base_async(plan.fn, resolved, plan.no_message_lines),
                            timeout=_timeout_secs,
                        )
                    except TimeoutError:
                        raise OxitestTimeoutError() from None
                return await run_base_async(plan.fn, resolved, plan.no_message_lines)
            # Phase 3: Teardown async generators in reverse order.
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


class MiddlewareBuilder:
    """Configurable builder for the middleware pipeline.

    The default pipeline is::

        BareAssertMiddleware -> AsyncDepGuardMiddleware
        -> TimeoutMiddleware -> AsyncBridgeMiddleware

    Plugins can customise ordering via ``insert_after``, ``insert_before``,
    and ``remove`` before calling ``build()``.
    """

    def __init__(self) -> None:
        self._pipeline: list[type[Middleware]] = [
            BareAssertMiddleware,
            AsyncDepGuardMiddleware,
            TimeoutMiddleware,
            AsyncBridgeMiddleware,
        ]

    def insert_after(self, target: type, new: type) -> None:
        idx = self._pipeline.index(target)
        self._pipeline.insert(idx + 1, new)

    def insert_before(self, target: type, new: type) -> None:
        idx = self._pipeline.index(target)
        self._pipeline.insert(idx, new)

    def remove(self, target: type) -> None:
        self._pipeline.remove(target)

    def build(
        self,
        plan: ExecutionPlan,
        base: Callable[[], TestResult],
        module: Any,
        default_timeout: int | None,
    ) -> Callable[[], TestResult]:
        mw_args: dict[type, dict[str, Any]] = {
            BareAssertMiddleware: {"module": module},
            TimeoutMiddleware: {"default_timeout": default_timeout},
        }
        instances: list[Any] = [cls(**mw_args.get(cls, {})) for cls in self._pipeline]
        return build_pipeline(instances, plan, base)
