from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "ExecutionPlan",
    "Middleware",
    "TimeoutMiddleware",
    "build_pipeline",
    "resolve_strategy",
]

import asyncio
import contextlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, assert_never

from oxitest._bridge._async_backend import AsyncBackend, AsyncSession
from oxitest._bridge._async_fixture_handle import async_teardown_sink
from oxitest._bridge._async_session_guard import acquire_session_guarded
from oxitest._bridge._boundary import (
    advance_async_gen,
    async_safe_call,
    setup_completed,
)
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
    """Immutable per-test data passed through the middleware stack.

    Post-ADR-0007 Rule 3 refactor: contains only pure test-shape data.
    Session resources live on session middlewares; timeout config lives on
    TimeoutMiddleware; async backend lives on AsyncBridgeMiddleware.
    """

    fn: Callable[..., Any]
    fn_name: str
    kwargs: MappingProxyType[str, Any]
    marks: tuple[MarkInfo, ...]
    no_message_lines: tuple[int, ...]
    is_async: bool


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
                return _compose(
                    make_timeout_wrapper(s, is_async=plan.is_async), next_fn
                )
            case _:
                assert_never(self.timeout)


def _effective_timeout_secs(plan: ExecutionPlan, timeout: Timeout) -> int | None:
    """The deadline that applies to this test — a mark wins over the ambient default.

    Resolved here rather than inside ``_async_test_core`` so the ambient default
    reaches ``asyncio.wait_for`` too. Before #1998 only a mark did, which left an
    async test under a global ``timeout`` enforced solely by the OS-level arm.
    """
    mark = next((m for m in plan.marks if m.name == "timeout"), None)
    if mark is not None:
        return extract_timeout_seconds(mark.kwargs)
    match timeout:
        case TimeoutSet(seconds=s):
            return s
        case TimeoutOff():
            return None
        case _:
            assert_never(timeout)


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
                # Appended before the advance (#1962). `await anext(v)`
                # suspends to the loop, and an interrupt delivered there would
                # otherwise leave the fixture set up with no teardown queued.
                async_teardowns.append((k, v))
                resolved[k] = await advance_async_gen(v)
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
    # Same live-list shape as executor._run_teardowns, deliberately left
    # unguarded (#1952). This list is fed only by async_teardown_sink during
    # fixture resolution, never by ctx.addfinalizer, so a user callback cannot
    # land here at all.
    for name, gen in reversed(async_teardowns):

        async def _drain(generator: Any = gen) -> None:
            # The list is populated before each generator is advanced (#1962),
            # so it can hold one whose setup never completed. Advancing that
            # would run its setup here, during teardown.
            if not setup_completed(generator):
                return
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


async def _async_test_core(
    plan: ExecutionPlan, timeout_secs: int | None = None
) -> TestResult:
    """Await coroutines, run the test body, teardown async fixtures.

    ``timeout_secs`` is resolved upstream by ``_effective_timeout_secs`` so all
    three session middleware variants share identical async test logic and the
    ambient default reaches ``wait_for``, not only a per-test mark (#1998).
    """
    unpacked = await _unpack_async_fixtures(plan.kwargs)
    if isinstance(unpacked, TestResult):
        return unpacked
    resolved, async_teardowns = unpacked
    # Fixtures resolved lazily inside the body (via `await fx.<ns>.<name>`)
    # append their generators to this same list, so the drain below covers
    # them too. Doing it any later would run their post-yield half after this
    # loop has closed.
    token = async_teardown_sink.set(async_teardowns)
    try:
        return await _run_with_timeout(
            plan.fn, resolved, plan.no_message_lines, timeout_secs
        )
    finally:
        async_teardown_sink.reset(token)
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
    timeout_secs: int | None = None

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not plan.is_async:
            return next_fn
        session = self.session
        secs = self.timeout_secs

        def _base() -> TestResult:
            return session.run(_async_test_core(plan, secs))

        return _base


@dataclass(frozen=True, slots=True)
class AsyncBridgeMiddleware:
    """Acquires a fresh AsyncSession from the backend for the test body.

    The "no upstream session" variant. Used when neither Shared nor Arrange
    strategy applies.
    """

    backend: AsyncBackend
    timeout_secs: int | None = None

    def apply(
        self, *, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not plan.is_async:
            return next_fn
        backend = self.backend
        secs = self.timeout_secs

        def _base() -> TestResult:
            with acquire_session_guarded(backend) as session:
                return session.run(_async_test_core(plan, secs))

        return _base


@dataclass(slots=True)
class _MiddlewarePipeline:
    """Pipeline assembler for the executor middleware chain.

    Three internal zones — `_pre_guard`, `_post_guard`, `_pre_session` — are
    reserved for a future plugin API. Empty today; adding plugin support later
    is a ~30-line addition, no rework required.

    Not ``frozen=True`` per ADR-0005: the zone lists are mutated as plugins
    register into their zones during pipeline configuration. Private and
    single-caller (only the executor constructs it), so the mutability is
    contained.
    """

    timeout: Timeout
    _pre_guard: list[Middleware] = field(default_factory=list)
    _post_guard: list[Middleware] = field(default_factory=list)
    _pre_session: list[Middleware] = field(default_factory=list)

    def build_for(
        self, plan: ExecutionPlan, strategy: SessionStrategy
    ) -> list[Middleware]:
        mws: list[Middleware] = []
        mws.extend(self._pre_guard)
        mws.append(AsyncDepGuardMiddleware())
        mws.extend(self._post_guard)
        mws.append(TimeoutMiddleware(timeout=self.timeout))
        mws.extend(self._pre_session)
        if plan.is_async:
            secs = _effective_timeout_secs(plan, self.timeout)
            match strategy:
                case Shared(session=s) | Arrange(session=s):
                    mws.append(_SessionRunMiddleware(session=s, timeout_secs=secs))
                case Fresh(backend=b):
                    mws.append(AsyncBridgeMiddleware(backend=b, timeout_secs=secs))
                case _:
                    assert_never(strategy)
        return mws
