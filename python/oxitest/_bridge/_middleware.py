from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "ExecutionPlan",
    "Middleware",
    "MiddlewareBuilder",
    "TimeoutMiddleware",
    "build_pipeline",
]

import asyncio
import contextlib
import inspect
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._boundary import async_safe_call
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_context import FixtureTeardownWarning
from oxitest._bridge._runners import run_base_async

if TYPE_CHECKING:
    from oxitest._bridge._mark_api import MarkInfo
    from oxitest._bridge._mark_registry import MarkWrapper
from oxitest._bridge._timeout import (
    OxitestTimeoutError,
    extract_timeout_seconds,
    make_timeout_wrapper,
)
from oxitest._bridge.result import TestResult, _error_result


def _compose(
    wrapper: MarkWrapper, inner: Callable[[], TestResult]
) -> Callable[[], TestResult]:
    """Return a callable that runs wrapper(inner).

    Using a named function avoids the loop-variable capture problem
    (ruff B023) that a bare lambda inside a for-loop would cause.
    """
    return lambda: wrapper(inner)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable context passed through the middleware stack."""

    fn: Callable[..., Any]
    fn_name: str
    kwargs: MappingProxyType[str, Any]
    marks: tuple[MarkInfo, ...]
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


async def _unpack_async_fixtures(
    kwargs: Any,
) -> tuple[dict[str, Any], list[tuple[str, Any]]] | TestResult:
    """Phase 1: await coroutines and advance async generators.

    Returns (resolved_kwargs, async_teardowns) on success, or a TestResult
    on fixture setup error.
    """
    resolved: dict[str, Any] = {}
    async_teardowns: list[tuple[str, Any]] = []
    for k, v in kwargs.items():
        if inspect.isasyncgen(v):
            try:
                resolved[k] = await anext(v)
                async_teardowns.append((k, v))
            except Exception as exc:  # noqa: BLE001 — async fixture setup runs user code
                return _error_result(str(FixtureSetupError(k, exc)))
        elif inspect.iscoroutine(v):
            try:
                resolved[k] = await v
            except Exception as exc:  # noqa: BLE001 — async fixture setup runs user code
                return _error_result(str(FixtureSetupError(k, exc)))
        else:
            resolved[k] = v
    return resolved, async_teardowns


async def _run_with_timeout(
    fn: Any,
    resolved: dict[str, Any],
    no_message_lines: tuple[int, ...],
    timeout_secs: int | None,
) -> TestResult:
    """Phase 2: run the async test body with optional asyncio timeout."""
    if timeout_secs is not None:
        try:
            return await asyncio.wait_for(
                run_base_async(fn, resolved, no_message_lines),
                timeout=timeout_secs,
            )
        except TimeoutError:
            raise OxitestTimeoutError from None
    return await run_base_async(fn, resolved, no_message_lines)


async def _teardown_async_generators(
    async_teardowns: list[tuple[str, Any]],
) -> None:
    """Phase 3: teardown async generators in reverse order."""
    for name, gen in reversed(async_teardowns):

        async def _drain(generator: Any = gen) -> None:
            with contextlib.suppress(StopAsyncIteration):
                await anext(generator)

        await async_safe_call(
            _drain(),
            default=None,
            on_error=lambda exc, fixture_name=name: warnings.warn(
                FixtureTeardownWarning(
                    f"error in teardown of fixture '{fixture_name}': {exc}"
                ),
                stacklevel=2,
            ),
        )


class AsyncBridgeMiddleware:
    """Replaces base runner with async bridge when fn is async."""

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not inspect.iscoroutinefunction(plan.fn):
            return next_fn

        backend = plan.backend or AsyncioBackend()

        timeout_mark = next((m for m in plan.marks if m.name == "timeout"), None)
        _timeout_secs = (
            extract_timeout_seconds(timeout_mark.kwargs)
            if timeout_mark
            else plan.default_timeout
        )

        async def _async_core() -> TestResult:
            unpacked = await _unpack_async_fixtures(plan.kwargs)
            if isinstance(unpacked, TestResult):
                return unpacked
            resolved, async_teardowns = unpacked
            try:
                return await _run_with_timeout(
                    plan.fn, resolved, plan.no_message_lines, _timeout_secs
                )
            finally:
                await _teardown_async_generators(async_teardowns)

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

        AsyncDepGuardMiddleware -> TimeoutMiddleware -> AsyncBridgeMiddleware

    Plugins can customise ordering via ``insert_after``, ``insert_before``,
    and ``remove`` before calling ``build()``.

    Ordering constraints (enforced):

    - ``AsyncDepGuardMiddleware`` must remain **first** in the pipeline.
      It cannot be removed or have another middleware inserted before it.
    - ``AsyncBridgeMiddleware`` must remain **last** in the pipeline.
      It cannot be removed or have another middleware inserted after it.
    """

    def __init__(self) -> None:
        self._pipeline: list[type[Middleware]] = [
            AsyncDepGuardMiddleware,
            TimeoutMiddleware,
            AsyncBridgeMiddleware,
        ]

    def insert_after(self, target: type, new: type) -> None:
        if target is AsyncBridgeMiddleware:
            msg = (
                "Cannot insert after AsyncBridgeMiddleware"
                " — it must remain last in the pipeline"
            )
            raise ValueError(msg)
        idx = self._pipeline.index(target)
        self._pipeline.insert(idx + 1, new)

    def insert_before(self, target: type, new: type) -> None:
        if target is AsyncDepGuardMiddleware:
            msg = (
                "Cannot insert before AsyncDepGuardMiddleware"
                " — it must remain first in the pipeline"
            )
            raise ValueError(msg)
        idx = self._pipeline.index(target)
        self._pipeline.insert(idx, new)

    def remove(self, target: type) -> None:
        if target is AsyncBridgeMiddleware:
            msg = (
                "AsyncBridgeMiddleware cannot be removed"
                " — it must remain last in the pipeline"
            )
            raise ValueError(msg)
        if target is AsyncDepGuardMiddleware:
            msg = (
                "AsyncDepGuardMiddleware cannot be removed"
                " — it must remain first in the pipeline"
            )
            raise ValueError(msg)
        self._pipeline.remove(target)

    def build(
        self,
        plan: ExecutionPlan,
        base: Callable[[], TestResult],
        default_timeout: int | None,
    ) -> Callable[[], TestResult]:
        mw_args: dict[type, dict[str, Any]] = {
            TimeoutMiddleware: {"default_timeout": default_timeout},
        }
        instances: list[Any] = [cls(**mw_args.get(cls, {})) for cls in self._pipeline]
        return build_pipeline(instances, plan, base)
