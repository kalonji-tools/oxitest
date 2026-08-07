"""Test execution orchestration for the oxitest bridge.

Loads the target module, resolves fixtures and parametrize values, evaluates
marks, builds the middleware pipeline, and returns a `TestResult`.  This is
the single entry point called by the Rust core (via PyO3) and by the parallel
worker subprocess.
"""

from __future__ import annotations

__all__ = [
    "DebugMode",
    "_debug_post_mortem",
    "_print_banner",
    "_resolve_debugger_backend",
    "_suspend_capture",
    "_trace_before_test",
    "run_test",
]

import contextlib
import functools
import hashlib
import inspect
import sys
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, assert_never

from oxitest._bridge._async_session_guard import acquire_session_guarded
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._cwd_guard import report_and_repair
from oxitest._bridge._debugger import _NULL_DEBUGGER, DebuggerBackend, _PdbBackend
from oxitest._bridge._doctest_runner import run_doctest
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    ArrangeError,
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
)
from oxitest._bridge._fixture_context import (
    TestRunContext,
    _current_teardown_node_id,
    _in_teardown,
    _test_run_context,
    _warn_teardown,
)
from oxitest._bridge._fixture_registry import FixtureScope
from oxitest._bridge._fixture_session import (
    FixtureSession,
    _SessionProtocol,
)
from oxitest._bridge._fn_metadata import get_metadata as _get_metadata
from oxitest._bridge._loader import (
    _load_module,
    _LoadError,
    _resolve_fn,
)
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import (
    MarkHandler,
    MarksHalt,
    MarksOutcome,
    MarksProceed,
    MarkWrapper,
    _PluginMarkHandler,
    evaluate_marks,
)
from oxitest._bridge._metadata import (
    get_fixture_name as _get_fixture_name,
    get_marks,
)
from oxitest._bridge._middleware import (
    ExecutionPlan,
    _compose,
    _MiddlewarePipeline,
    build_pipeline,
    resolve_strategy,
)
from oxitest._bridge._runners import (
    NO_DEBUG,
    DebugContext,
    DebugMode,
    _debug_post_mortem,
    _print_banner,
    _suspend_capture,
    _trace_before_test,
    run_base as _run_base,
)
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge._timeout import parse_timeout
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.result import StatusKind, TestResult, _error_result

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import AsyncBackend, AsyncSession
    from oxitest._bridge._fixture_registry import FixtureRegistry


def _scan_arrange_entries_for_async_mismatch(
    arranged: tuple[Any, ...],
    registry: FixtureRegistry,
    *,
    test_is_async: bool,
) -> list[tuple[str, Any]]:
    """Return the list of (name, FixtureDef) entries that are illegal.

    Illegal cell: fixture.is_async AND scope == EACH AND test is sync.
    Missing fixtures are NOT collected here — they surface via the
    existing not_found path so the diagnostic wording stays specific.
    """
    illegal: list[tuple[str, Any]] = []
    for entry in arranged:
        if isinstance(entry, type):
            defs = registry.get_by_type(entry)
            defn = defs[0] if defs else None
        else:  # str
            defn = registry.get(entry)
        if defn is None:
            continue  # not-found handled by existing arrange loop
        if defn.is_async and defn.scope == FixtureScope.EACH and not test_is_async:
            name = entry if isinstance(entry, str) else defn.name
            illegal.append((name, defn))
    return illegal


@functools.cache
def _exec_unique_name(module_path: str) -> str:
    digest = hashlib.md5(module_path.encode(), usedforsecurity=False)
    return f"_oxitest_exec_{digest.hexdigest()[:12]}"


def _resolve_debugger_backend(
    session: _SessionProtocol,
    debug_mode: DebugMode,
) -> DebuggerBackend:
    """Resolve the debugger backend from the plugin registry or fall back to pdb.

    Returns ``_NULL_DEBUGGER`` when ``debug_mode is DebugMode.OFF`` (no
    debugging requested) so callers do not waste a ``_PdbBackend``
    allocation on the happy path.
    """
    if debug_mode is DebugMode.OFF:
        return _NULL_DEBUGGER
    backend = session.plugin_registry.debugger_backend
    if backend is _NULL_DEBUGGER:
        return _PdbBackend()
    return backend


@dataclass(frozen=True, slots=True)
class _MarkResult:
    """Output of mark evaluation — marks metadata + execution wrappers."""

    marks: tuple[MarkInfo, ...]
    wrappers: tuple[MarkWrapper, ...]


@dataclass(frozen=True, slots=True)
class _ChainContext:
    """Runtime resources needed to wire the middleware chain.

    Bundles session, arrange-session, and timeout so ``_build_execution_chain``
    stays within the five-argument limit (PLR0913).
    """

    default_timeout: int | None
    session: _SessionProtocol
    arrange_session: AsyncSession | None


@dataclass(frozen=True, slots=True)
class _ResolvedTest:
    module: Any
    fn_raw: Any
    fn: Callable[..., Any]
    fn_name: str
    all_kwargs: dict[str, Any]
    fn_teardowns: list[Callable[[], None]]


def _load_and_resolve(
    meta: TestMeta,
    session: _SessionProtocol,
    unique_name: str,
) -> TestResult | _ResolvedTest:
    """Load module, resolve function, parametrize, and fixtures.

    Returns _ResolvedTest on success, or TestResult on module/fn/resolve errors.
    """
    _cache = session.module_cache
    _cached = _cache.get(meta.module_path)
    if _cached is not None:
        module = _cached
        sys.modules[unique_name] = module
    else:
        try:
            module = _load_module(meta.module_path, unique_name)
        except _LoadError as e:
            return e.result
        _cache.set(meta.module_path, module)
    try:
        fn_raw, fn = _resolve_fn(module, meta.fn_name, meta.module_path)
    except _LoadError as e:
        return e.result

    # Resolve parametrize case values
    try:
        param_kwargs, fixref_names = resolve_parametrize(fn_raw, fn, meta.kind)
    except ParametrizeError as exc:
        return _error_result(str(exc))

    # Resolve fixtures from function signature
    fn_teardowns: list[Callable[[], None]] = []
    try:
        fixture_kwargs: dict[str, Any]
        fixture_kwargs, fn_teardowns = session.resolve_for_test(
            fn,
            meta,
            skip_names=fixref_names,
        )
        # Resolve FixtureRef fields using each case's specific fixture function
        for field_name in fixref_names:
            fixture_fn = param_kwargs[field_name]
            fixture_name = _get_fixture_name(
                fixture_fn, fallback=getattr(fixture_fn, "__name__", "")
            )
            namespace = session.get_namespace_for_func(fixture_name, fixture_fn)
            if namespace:
                param_kwargs[field_name] = session.get_fixture_in_namespace(
                    fixture_name,
                    namespace,
                    meta.module_path,
                    fn_teardowns,
                    # A FixtureRef lands in param_kwargs, which
                    # AsyncDepGuardMiddleware inspects for coroutines — and a
                    # wider-than-function async fixture is not one. Omitting
                    # this used to inherit the permissive default and hand a
                    # sync test an AsyncFixtureHandle it could only fail on
                    # (#1876).
                    test_is_async=inspect.iscoroutinefunction(fn),
                )
            else:
                param_kwargs[field_name] = session.get_fixture_by_name(
                    fixture_name, meta.module_path, fn_teardowns
                )
    except (FixtureSetupError, FixtureNotFoundError) as exc:
        return _error_result(str(exc))

    all_kwargs: dict[str, Any] = {**fixture_kwargs, **param_kwargs}
    return _ResolvedTest(module, fn_raw, fn, meta.fn_name, all_kwargs, fn_teardowns)


def _build_execution_plan(
    resolved: _ResolvedTest,
    mark_result: _MarkResult,
) -> ExecutionPlan:
    """Assemble the immutable ExecutionPlan from the resolved test."""
    _bare_map: dict[str, list[int]] = getattr(
        resolved.module, "_oxitest_bare_asserts", {}
    )
    _simple_fn_name = resolved.fn_name.rsplit("::", maxsplit=1)[-1]
    no_message_lines = tuple(_bare_map.get(_simple_fn_name, []))

    return ExecutionPlan(
        fn=resolved.fn,
        fn_name=resolved.fn_name,
        kwargs=MappingProxyType(resolved.all_kwargs),
        marks=mark_result.marks,
        no_message_lines=no_message_lines,
        is_async=inspect.iscoroutinefunction(resolved.fn),
    )


def _build_execution_chain(
    resolved: _ResolvedTest,
    plan: ExecutionPlan,
    mark_result: _MarkResult,
    ctx: _ChainContext,
    debug: DebugContext = NO_DEBUG,
) -> Callable[[], TestResult]:
    """Build the composed execution callable via middleware pipeline."""

    def _base() -> TestResult:
        return _run_base(
            resolved.fn,
            resolved.all_kwargs,
            plan.no_message_lines,
            debug=debug,
        )

    used_shared = getattr(ctx.session, "_used_shared_async", False)
    shared = getattr(ctx.session, "_shared_session", None) if used_shared else None
    # Promote by visibility. A fixture reached via `await fx.<ns>.<name>` is
    # built on whatever loop the body is running on, so a wider-than-function
    # async fixture needs that loop to outlive the test. `_used_shared_async`
    # cannot answer this: lazy resolution sets it from inside the body, after
    # the strategy has already been chosen.
    if not used_shared and plan.is_async:
        shared = _promote_for_wide_async(ctx.session)
        used_shared = shared is not None
    backend = ctx.session.async_backend
    strategy = resolve_strategy(
        used_shared=used_shared,
        shared=shared,
        arrange=ctx.arrange_session,
        default_backend=backend,
    )
    timeout = parse_timeout(ctx.default_timeout)
    pipeline = _MiddlewarePipeline(timeout=timeout)
    middlewares = pipeline.build_for(plan, strategy)
    execute = build_pipeline(middlewares, plan, _base)
    for wrapper in reversed(mark_result.wrappers):
        execute = _compose(wrapper, execute)
    return execute


_NULL_SESSION: _SessionProtocol = FixtureSession([])


def _evaluate_marks_phase(
    session: _SessionProtocol,
    marks: Sequence[MarkInfo],
) -> MarksOutcome:
    """Evaluate marks over the plugin-augmented registry and return the outcome."""
    _plugin_handlers: list[MarkHandler] = [
        _PluginMarkHandler(pw) for pw in session.plugin_registry.execution_wrappers
    ]

    return evaluate_marks(
        marks,
        plugin_handlers=_plugin_handlers,
    )


def _run_teardowns(fn_teardowns: list[Callable[[], None]], node_id: str) -> None:
    """Run function-scope teardowns in reverse order, suppressing errors."""
    token = _current_teardown_node_id.set(node_id)
    # The function tier's half of the teardown window; _Scope.drain marks every
    # wider tier. Separate from the node id above because that one names *which*
    # node is tearing down and has no answer at the process tier (#1952).
    in_td = _in_teardown.set(True)
    try:
        for td in reversed(fn_teardowns):
            with contextlib.suppress(Exception):
                td()
    finally:
        _in_teardown.reset(in_td)
        _current_teardown_node_id.reset(token)


def _promote_for_wide_async(session: _SessionProtocol) -> AsyncSession | None:
    """Return the shared session if this test can reach a wide async fixture.

    ``None`` means no promotion: either nothing wider than function lifetime
    is registered, or the session predates this capability.

    Duck-typed via ``getattr`` rather than widened on ``_SessionProtocol``,
    because the protocol is structurally satisfied by test doubles that have
    no async machinery at all and no reason to grow any.
    """
    sees_wide = getattr(session, "has_wide_async_fixtures", None)
    acquire = getattr(session, "ensure_async_session", None)
    if sees_wide is None or acquire is None or not sees_wide():
        return None
    return acquire()


def _drive_arrange_async_each(
    value: Any,
    fixture_name: str,
    session: AsyncSession,
    fn_teardowns: list[Callable[[], None]],
) -> None:
    """Advance an arranged async-each fixture on the caller-supplied session.

    The fixture instantiator returns coroutines/asyncgens for async fixtures
    unchanged (pass-through). For arranged fixtures this would leave setup
    un-run and teardown unregistered. Advance the coroutine or async generator
    on the per-test session, and register a teardown that drains the generator
    on the same session, routing failures through `safe_teardown` +
    `_warn_teardown` — the sync convention.

    The session is the same one later reused by the middleware for the test
    body, so async resources bound to the session's loop (Events, Queues,
    etc.) yielded in setup remain valid across setup → body → teardown.
    """
    if inspect.isasyncgen(value):
        agen = value

        async def _first() -> Any:
            return await agen.__anext__()

        session.run(_first())

        def _teardown(agen_ref: Any = agen, name: str = fixture_name) -> None:
            async def _drain() -> None:
                with contextlib.suppress(StopAsyncIteration):
                    await agen_ref.__anext__()

            safe_teardown(
                lambda: session.run(_drain()),
                name,
                warn=_warn_teardown,
            )

        fn_teardowns.append(_teardown)
        return
    # coroutine — await it; no teardown for plain coroutine fixtures
    session.run(value)


def _resolve_arranged_entry(
    entry: Any,
    effective_session: _SessionProtocol,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    get_each_session: Callable[[], AsyncSession],
) -> None:
    """Resolve one arranged entry (name or type) and drive if async-each.

    `get_each_session` is a lazy accessor: the caller only pays for an
    async session when at least one arranged entry turns out to be an
    async fixture.
    """
    if isinstance(entry, type):
        value = effective_session.get_fixture_by_type(entry, module_path, fn_teardowns)
        fixture_name = entry.__name__
    else:  # str
        value = effective_session.get_fixture_by_name(entry, module_path, fn_teardowns)
        fixture_name = entry
    # If the fixture is async (each-scope), the value returned by the
    # instantiator is the raw coroutine/asyncgen (pass-through from
    # _unpack_sync). Advance it on the per-test session so the fixture's
    # setup actually runs and its teardown lands in fn_teardowns.
    if inspect.iscoroutine(value) or inspect.isasyncgen(value):
        _drive_arrange_async_each(value, fixture_name, get_each_session(), fn_teardowns)


@dataclass(frozen=True, slots=True)
class ArrangeReady:
    """Arrange succeeded; no async-each fixture required a session."""


@dataclass(frozen=True, slots=True)
class ArrangeReadyAsync:
    """Arrange succeeded; a per-test AsyncSession was acquired.

    Threaded to the middleware so the async test body reuses the same loop
    the arrange fixtures were set up on — closes the ADR-0006 body-loop-
    identity gap.
    """

    session: AsyncSession


@dataclass(frozen=True, slots=True)
class ArrangeFailed:
    """Arrange failed with this error TestResult."""

    error: TestResult


_ArrangeResult = ArrangeReady | ArrangeReadyAsync | ArrangeFailed


def _acquire_each_session(
    backend: AsyncBackend,
    fn_teardowns: list[Callable[[], None]],
) -> AsyncSession:
    """Acquire a per-test AsyncSession and register its close on ``fn_teardowns``.

    Extracted from ``_run_arrange_phase`` for direct unit testing. Callers use
    this inside a lazy accessor closure so the session is only acquired when
    at least one arranged fixture turns out to be async — sync-only arranges
    still pay nothing.

    Args:
        backend: The AsyncBackend supplying the session (via the guard).
        fn_teardowns: The per-test teardown list. This function appends
            exactly one callable that closes the acquired session's stack.

    Returns:
        A live ``AsyncSession`` that ``.run(coro)`` can be called on. The
        session stays open until the appended teardown fires (typically at
        the end of ``_run_teardowns`` in reverse LIFO order).
    """
    stack = ExitStack()
    session = stack.enter_context(acquire_session_guarded(backend))

    def _close_stack() -> None:
        stack.close()

    fn_teardowns.append(_close_stack)
    return session


def _run_arrange_phase(
    fn_raw: Any,
    effective_session: _SessionProtocol,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
) -> _ArrangeResult:
    """Execute the arrange phase for a test.

    Scans for async-mismatch entries first (all-or-nothing loud rejection),
    then instantiates each arranged fixture in order. Fixture-error
    exceptions surface as error results via the existing convention;
    missing arranged fixtures are caught upstream by the Rust
    FixtureValidationPhase and reach this catch only if that phase misses.

    A per-test async session is acquired lazily on the first arranged
    async-each fixture via ``acquire_session_guarded(backend)`` pushed onto
    an ``ExitStack``. The stack's ``close`` is registered as a teardown so
    the session is finalized (loop closed, asyncgens shut down) after every
    async-each teardown has drained — LIFO ordering guarantees the close
    runs last on the shared loop.

    Returns:
        ``ArrangeReady`` on success with no async-each fixture,
        ``ArrangeReadyAsync(session)`` on success when an async-each fixture
        triggered lazy session acquisition, or ``ArrangeFailed(error)`` on
        failure.
    """
    arranged = _get_metadata(fn_raw).arranged
    if not arranged:
        return ArrangeReady()
    test_is_async = inspect.iscoroutinefunction(fn_raw)
    illegal = _scan_arrange_entries_for_async_mismatch(
        arranged,
        effective_session.registry,
        test_is_async=test_is_async,
    )
    if illegal:
        return ArrangeFailed(error=_error_result(str(ArrangeError(fn_raw, illegal))))

    cell: ArrangeReady | ArrangeReadyAsync = ArrangeReady()

    def get_each_session() -> AsyncSession:
        nonlocal cell
        match cell:
            case ArrangeReadyAsync(session=existing):
                return existing
            case ArrangeReady():
                session = _acquire_each_session(
                    effective_session.async_backend, fn_teardowns
                )
                cell = ArrangeReadyAsync(session=session)
                return session
            case _:
                assert_never(cell)

    try:
        for entry in arranged:
            _resolve_arranged_entry(
                entry, effective_session, module_path, fn_teardowns, get_each_session
            )
    except (
        FixtureNotFoundError,
        FixtureSetupError,
        AmbiguousFixtureError,
        FixtureCycleError,
    ) as exc:
        return ArrangeFailed(error=_error_result(str(exc)))
    return cell


def run_test(
    meta: TestMeta,
    session: _SessionProtocol | None = None,
    default_timeout: int | None = None,
    keep_tmp: str = "cleanup",
    *,
    debug: DebugContext = NO_DEBUG,
) -> TestResult:
    """Run one test, then verify it left the worker's working directory alive.

    Tests in a worker share a process and ``os.chdir`` is global, so a test
    that leaves the working directory inside a deleted directory kills every
    subprocess spawned afterwards. The check runs after ``_run_test_impl``'s
    teardowns, which is where the deletion happens (#1957).

    A wrapper rather than a check inside that function's ``finally``: the
    ``try`` there has several return points, and returning from ``finally``
    would swallow exceptions.
    """
    result = _run_test_impl(meta, session, default_timeout, keep_tmp, debug=debug)
    msg = report_and_repair(meta.node_id)
    if msg is None:
        return result
    # Never downgrade an already-loud result: replacing a FailedResult would
    # discard the real failure message the author needs.
    if result.status in (StatusKind.FAILED, StatusKind.ERROR, StatusKind.TIMEOUT):
        return result
    return _error_result(msg)


def _run_test_impl(
    meta: TestMeta,
    session: _SessionProtocol | None = None,
    default_timeout: int | None = None,
    keep_tmp: str = "cleanup",
    *,
    debug: DebugContext = NO_DEBUG,
) -> TestResult:
    """Load, resolve, and execute a single test function.

    Args:
        meta: Test identity metadata (module path, function name, node ID,
            parametrize case ID, and marks).
        session: Active `FixtureSession` for fixture injection.  When `None`
            a null session is used, meaning no user fixtures are available.
        default_timeout: Per-test timeout in seconds inherited from config.
            Overridden by a ``@mark.timeout`` decorator on the test.
        keep_tmp: Controls TempDir cleanup. ``"cleanup"`` always removes the
            directory (default). ``"failed"`` preserves only on test failure.
            ``"always"`` preserves unconditionally.
        debug: Debug/trace and diagnostic display configuration. Controls
            interactive debugger mode, traceback local variables, and
            internal frame visibility.

    Returns:
        A `TestResult` whose `status` is one of ``"passed"``, ``"failed"``,
        ``"error"``, ``"skipped"``, ``"warned"``, ``"xfailed"``, or
        ``"xpassed"``.

    """
    # Doctest dispatch — bypass normal fixture/mark pipeline
    if meta.fn_name.startswith("<doctest>"):
        doctest_name = meta.fn_name.removeprefix("<doctest>")
        return run_doctest(meta.module_path, doctest_name)

    effective_session: _SessionProtocol = (
        session if session is not None else _NULL_SESSION
    )
    _run_ctx = TestRunContext(
        keep_tmp=keep_tmp,
        result_cell=[None] if keep_tmp != "cleanup" else [],
        meta=meta,
    )
    _run_ctx_token = _test_run_context.set(_run_ctx)
    backend = _resolve_debugger_backend(effective_session, debug.mode)
    # Enrich debug context with resolved backend and test node_id
    debug = replace(debug, node_id=meta.node_id, backend=backend)
    unique_name = _exec_unique_name(meta.module_path)
    resolved = _load_and_resolve(meta, effective_session, unique_name)
    if not isinstance(resolved, _ResolvedTest):
        # The one return that escapes the try/finally below. Without this
        # reset the failed test's identity outlives run_test, and a later
        # TestContext.current() — in a wider fixture's teardown, say — hands
        # back a context for a test that never ran (#1949).
        _test_run_context.reset(_run_ctx_token)
        return resolved
    fn_raw = resolved.fn_raw
    fn_teardowns = resolved.fn_teardowns
    # resolve_for_test builds the teardown list, so the set above could not
    # carry it — keep_tmp is read *during* fixture setup, so that set cannot
    # move later either. Re-set now that resolution is done (#1949).
    _run_ctx_token2 = _test_run_context.set(
        replace(_run_ctx, fn_teardowns=fn_teardowns)
    )

    try:
        marks = get_marks(fn_raw)
        marks_outcome = _evaluate_marks_phase(effective_session, marks)
        match marks_outcome:
            case MarksHalt(result=short_circuit_result):
                return short_circuit_result
            case MarksProceed(wrappers=mark_wrappers):
                pass
            case _:
                assert_never(marks_outcome)

        # --- Arrange phase (side-effect-only fixtures declared via @oxi.arrange) ---
        arrange_result = _run_arrange_phase(
            fn_raw, effective_session, meta.module_path, fn_teardowns
        )
        match arrange_result:
            case ArrangeFailed(error=arrange_err):
                return arrange_err
            case ArrangeReadyAsync(session=acquired_session):
                arrange_session: AsyncSession | None = acquired_session
            case ArrangeReady():
                arrange_session = None
            case _:
                assert_never(arrange_result)

        mark_result = _MarkResult(marks=tuple(marks), wrappers=mark_wrappers)
        plan = _build_execution_plan(resolved, mark_result)
        chain_ctx = _ChainContext(
            default_timeout=default_timeout,
            session=effective_session,
            arrange_session=arrange_session,
        )
        execute = _build_execution_chain(
            resolved, plan, mark_result, chain_ctx, debug=debug
        )
        result = execute()
        _active_ctx = _test_run_context.get()
        if _active_ctx.result_cell:
            _active_ctx.result_cell[0] = result
        return result
    finally:
        sys.modules.pop(unique_name, None)
        _run_teardowns(fn_teardowns, meta.node_id)
        _test_run_context.reset(_run_ctx_token2)
        _test_run_context.reset(_run_ctx_token)
