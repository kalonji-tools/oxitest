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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from oxitest._bridge._async_backend import AsyncBackend, AsyncioBackend, AsyncSession
from oxitest._bridge._async_session_guard import acquire_session_guarded
from oxitest._bridge._boundary import async_safe_call
from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._runners import run_base_async

if TYPE_CHECKING:
    from oxitest._bridge._mark_api import MarkInfo
    from oxitest._bridge._mark_registry import MarkWrapper
from oxitest._bridge._timeout import (
    OxitestTimeoutError,
    extract_timeout_seconds,
    make_timeout_wrapper,
)
from oxitest._bridge.result import DiagnosticSeverity, TestResult, _error_result


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
    """Immutable context passed through the middleware stack.

    ``arrange_session`` is populated by the executor when the arrange phase
    acquired a per-test :class:`AsyncSession` for async-each fixtures. The
    :class:`AsyncBridgeMiddleware` reuses it for the async test body so
    setup, body, and teardown share one loop identity — resources bound to
    the arrange loop (e.g. :class:`asyncio.Event`) remain valid in the body.
    Precedence when both are present: ``shared_session`` wins because it is
    longer-lived (session/shared scope) than ``arrange_session`` (each).
    """

    fn: Callable[..., Any]
    fn_name: str
    kwargs: MappingProxyType[str, Any]
    marks: tuple[MarkInfo, ...]
    no_message_lines: tuple[int, ...]
    is_async: bool
    default_timeout: int | None
    backend: AsyncBackend | None
    shared_session: AsyncSession | None
    arrange_session: AsyncSession | None


class Middleware(Protocol):
    def apply(
        self,
        *,
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
        execute = mw.apply(plan=plan, next_fn=execute)
    return execute


class TimeoutMiddleware:
    """Adds timeout wrapper if no per-test @timeout mark and default_timeout is set."""

    def __init__(self, default_timeout: int | None) -> None:
        self._default = default_timeout

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if self._default is not None and not any(
            m.name == "timeout" for m in plan.marks
        ):
            return _compose(make_timeout_wrapper(self._default), next_fn)
        return next_fn


class AsyncDepGuardMiddleware:
    """Rejects async fixture values passed to sync tests."""

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
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
            on_error=lambda exc, fixture_name=name: emit_diagnostic(
                DiagnosticSeverity.WARNING,
                "fixture teardown",
                f"error in teardown of fixture '{fixture_name}': {exc}",
            ),
        )


class AsyncBridgeMiddleware:
    """Replaces base runner with async bridge when fn is async."""

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
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
            shared_session = plan.shared_session

            def _base() -> TestResult:  # pragma: no cover
                return shared_session.run(_async_core())
        elif plan.arrange_session is not None:
            # Reuse the per-test session created by the executor's arrange
            # phase. This closes the ADR-0006 body-loop-identity gap: setup,
            # body, and teardown all run on the same event loop, so any loop-
            # bound resource yielded by an arrange fixture (asyncio.Event,
            # Queue, aiohttp.ClientSession, ...) stays valid in the body.
            arrange_session = plan.arrange_session

            def _base() -> TestResult:
                return arrange_session.run(_async_core())
        else:

            def _base() -> TestResult:
                with acquire_session_guarded(backend) as session:
                    return session.run(_async_core())

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

    @property
    def pipeline(self) -> tuple[type[Middleware], ...]:
        """The current middleware pipeline (immutable view)."""
        return tuple(self._pipeline)

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
