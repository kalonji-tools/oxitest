from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "ExecutionPlan",
    "Middleware",
    "MiddlewareBuilder",
    "TimeoutMiddleware",
    "build_pipeline",
    "resolve_strategy",
]

import asyncio
import contextlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, assert_never

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
    Timeout,
    TimeoutOff,
    TimeoutSet,
    extract_timeout_seconds,
    make_timeout_wrapper,
    parse_timeout,
)
from oxitest._bridge.result import DiagnosticSeverity, TestResult, _error_result


@dataclass(frozen=True, slots=True)
class Shared:
    """Longest-lived session variant — session-scoped fixture provides the loop."""

    session: AsyncSession


@dataclass(frozen=True, slots=True)
class Arrange:
    """Per-test session variant — an @arrange fixture supplies the body loop."""

    session: AsyncSession


@dataclass(frozen=True, slots=True)
class Fresh:
    """Fallback variant — no upstream session; acquire one from the backend."""

    backend: AsyncBackend


# Discriminated union — async session strategy for one test.
# See ADR-0007 Rule 1 (Sum-type-in-disguise) and Rule 3 (Fat-context Optional).
# Precedence: Shared > Arrange > Fresh (Shared is longer-lived, so it wins).
# Union alias matches the ADR-0007 precedent set by ``MarkAction`` and
# ``Timeout``; enables ``assert_never`` exhaustiveness at dispatch sites.
SessionStrategy = Shared | Arrange | Fresh


def resolve_strategy(
    *,
    used_shared: bool,
    shared: AsyncSession | None,
    arrange: AsyncSession | None,
    default_backend: AsyncBackend,
) -> SessionStrategy:
    """Collapse fixture-session Optionals into a SessionStrategy variant.

    The Optional collapse happens ONCE here — never in middleware or pipeline.
    """
    if used_shared and shared is not None:
        return Shared(shared)
    if arrange is not None:
        return Arrange(arrange)
    return Fresh(default_backend)


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


@dataclass(frozen=True, slots=True)
class TimeoutMiddleware:
    """Adds timeout wrapper unless a per-test @mark.timeout is present.

    Dispatches on the ``Timeout`` variant — ``TimeoutOff`` is a no-op.
    """

    timeout: Timeout

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if any(m.name == "timeout" for m in plan.marks):
            return next_fn
        match self.timeout:
            case TimeoutOff():
                return next_fn
            case TimeoutSet(seconds=s):
                return _compose(make_timeout_wrapper(s), next_fn)
            case _:
                assert_never(self.timeout)


class AsyncDepGuardMiddleware:
    """Rejects async fixture values passed to sync tests."""

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not plan.is_async:
            for k, v in plan.kwargs.items():
                if inspect.iscoroutine(v) or inspect.isasyncgen(v):
                    if inspect.iscoroutine(v):
                        v.close()

                    _msg = (
                        f"async fixture '{k}' cannot be used by sync test "
                        f"'{plan.fn_name}' — make the test async def"
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


async def _async_test_core(plan: ExecutionPlan) -> TestResult:
    """Await coroutines, run the test body, teardown async fixtures.

    Handles mark-timeout extraction so all three session middleware variants
    share identical async test logic.
    """
    timeout_mark = next((m for m in plan.marks if m.name == "timeout"), None)
    timeout_secs = (
        extract_timeout_seconds(timeout_mark.kwargs) if timeout_mark else None
    )
    unpacked = await _unpack_async_fixtures(plan.kwargs)
    if isinstance(unpacked, TestResult):
        return unpacked
    resolved, async_teardowns = unpacked
    try:
        return await _run_with_timeout(
            plan.fn, resolved, plan.no_message_lines, timeout_secs
        )
    finally:
        await _teardown_async_generators(async_teardowns)


@dataclass(frozen=True, slots=True)
class _SessionRunMiddleware:
    """Runs async test bodies on a caller-supplied AsyncSession.

    Used for both the ``Shared`` and ``Arrange`` ``SessionStrategy`` variants —
    both simply run the body on an upstream-provided session. The scope
    distinction (session-lifetime vs per-test) is expressed at the strategy
    level and enforced by ``resolve_strategy``; downstream this middleware
    just calls ``session.run(...)``. For the ``Arrange`` case this preserves
    ADR-0006 loop-identity: setup, body, and teardown share the same loop.
    """

    session: AsyncSession

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not plan.is_async:
            return next_fn
        session = self.session

        def _base() -> TestResult:
            return session.run(_async_test_core(plan))

        return _base


@dataclass(frozen=True, slots=True)
class AsyncBridgeMiddleware:
    """Acquires a fresh AsyncSession from the backend for the test body.

    The "no upstream session" variant. Used when neither Shared nor Arrange
    strategy applies.
    """

    backend: AsyncBackend

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not plan.is_async:
            return next_fn
        backend = self.backend

        def _base() -> TestResult:
            with acquire_session_guarded(backend) as session:
                return session.run(_async_test_core(plan))

        return _base


class MiddlewareBuilder:
    """Legacy adapter for the middleware pipeline.

    The default pipeline is::

        AsyncDepGuardMiddleware -> TimeoutMiddleware -> <session middleware>

    Task 3 replaces this call site with executor-side construction using
    ``SessionStrategy`` / ``resolve_strategy`` directly.
    """

    def build(
        self,
        plan: ExecutionPlan,
        base: Callable[[], TestResult],
        default_timeout: int | None,
    ) -> Callable[[], TestResult]:
        """Legacy adapter — Task 3 replaces this with executor-side construction."""
        backend = plan.backend or AsyncioBackend()
        session_mw: Middleware
        if plan.shared_session is not None:
            session_mw = _SessionRunMiddleware(session=plan.shared_session)
        elif plan.arrange_session is not None:
            session_mw = _SessionRunMiddleware(session=plan.arrange_session)
        else:
            session_mw = AsyncBridgeMiddleware(backend=backend)
        instances: list[Middleware] = [
            AsyncDepGuardMiddleware(),
            TimeoutMiddleware(timeout=parse_timeout(default_timeout)),
            session_mw,
        ]
        return build_pipeline(instances, plan, base)
