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
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from oxitest._bridge._debugger import DebuggerBackend, _PdbBackend
from oxitest._bridge._doctest_runner import run_doctest
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    FixtureCycleError,
    FixtureNotFoundError,
    FixtureSetupError,
)
from oxitest._bridge._fixture_context import (
    TestRunContext,
    _current_teardown_node_id,
    _test_run_context,
)
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
    MiddlewareBuilder,
    _compose,
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
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.result import TestResult, _error_result


@functools.cache
def _exec_unique_name(module_path: str) -> str:
    digest = hashlib.md5(module_path.encode(), usedforsecurity=False)
    return f"_oxitest_exec_{digest.hexdigest()[:12]}"


def _resolve_debugger_backend(
    session: _SessionProtocol,
    debug_mode: str | None,
) -> DebuggerBackend | None:
    """Resolve the debugger backend from the plugin registry or fall back to pdb.

    Returns None when debug_mode is None (no debugging requested).
    """
    if debug_mode is None:
        return None
    if session.plugin_registry.debugger_backend is not None:
        return session.plugin_registry.debugger_backend
    return _PdbBackend()


@dataclass(frozen=True, slots=True)
class _MarkResult:
    """Output of mark evaluation — marks metadata + execution wrappers."""

    marks: tuple[MarkInfo, ...]
    wrappers: tuple[MarkWrapper, ...]


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
        param_kwargs, fixref_names = resolve_parametrize(fn_raw, fn, meta.param_id)
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
                    fixture_name, namespace, meta.module_path, fn_teardowns
                )
            else:
                param_kwargs[field_name] = session.get_fixture_by_name(
                    fixture_name, meta.module_path, fn_teardowns
                )
    except (FixtureSetupError, FixtureNotFoundError) as exc:
        return _error_result(str(exc))

    all_kwargs: dict[str, Any] = {**fixture_kwargs, **param_kwargs}
    return _ResolvedTest(module, fn_raw, fn, meta.fn_name, all_kwargs, fn_teardowns)


def _build_execution_chain(
    resolved: _ResolvedTest,
    mark_result: _MarkResult,
    default_timeout: int | None,
    session: _SessionProtocol | None = None,
    *,
    debug: DebugContext = NO_DEBUG,
) -> Callable[[], TestResult]:
    """Build the composed execution callable via middleware pipeline."""
    # Resolve bare-assert lines (was BareAssertMiddleware)
    _bare_map: dict[str, list[int]] = getattr(
        resolved.module, "_oxitest_bare_asserts", {}
    )
    _simple_fn_name = resolved.fn_name.rsplit("::", maxsplit=1)[-1]
    no_message_lines = tuple(_bare_map.get(_simple_fn_name, []))

    _used_shared = getattr(session, "_used_shared_async", False)
    _shared = getattr(session, "_shared_session", None) if _used_shared else None

    plan = ExecutionPlan(
        fn=resolved.fn,
        fn_name=resolved.fn_name,
        kwargs=MappingProxyType(resolved.all_kwargs),
        marks=mark_result.marks,
        no_message_lines=no_message_lines,
        is_async=inspect.iscoroutinefunction(resolved.fn),
        default_timeout=default_timeout,
        backend=getattr(session, "_async_backend", None),
        shared_session=_shared,
    )

    def _base() -> TestResult:
        return _run_base(
            resolved.fn,
            resolved.all_kwargs,
            plan.no_message_lines,
            debug=debug,
        )

    execute = MiddlewareBuilder().build(plan, _base, default_timeout)

    # Apply mark wrappers (from evaluate_marks) around the pipeline result
    for wrapper in reversed(mark_result.wrappers):
        execute = _compose(wrapper, execute)

    return execute


_NULL_SESSION: _SessionProtocol = FixtureSession([])


def _evaluate_marks_phase(
    session: _SessionProtocol,
    marks: Sequence[MarkInfo],
) -> tuple[TestResult | None, list[MarkWrapper]]:
    """Evaluate marks and return (short_circuit, wrappers)."""
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
    try:
        for td in reversed(fn_teardowns):
            with contextlib.suppress(Exception):
                td()
    finally:
        _current_teardown_node_id.reset(token)


def run_test(
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
    )
    _run_ctx_token = _test_run_context.set(_run_ctx)
    backend = _resolve_debugger_backend(effective_session, debug.mode)
    # Enrich debug context with resolved backend and test node_id
    debug = replace(debug, node_id=meta.node_id, backend=backend)
    unique_name = _exec_unique_name(meta.module_path)
    resolved = _load_and_resolve(meta, effective_session, unique_name)
    if not isinstance(resolved, _ResolvedTest):
        return resolved
    fn_raw = resolved.fn_raw
    fn_teardowns = resolved.fn_teardowns

    try:
        marks = get_marks(fn_raw)
        short_circuit, wrappers = _evaluate_marks_phase(effective_session, marks)
        if short_circuit is not None:
            return short_circuit

        # --- Arrange phase (side-effect-only fixtures declared via @oxi.arrange) ---
        arranged = _get_metadata(fn_raw).arranged
        if arranged:
            try:
                for entry in arranged:
                    if isinstance(entry, type):
                        effective_session.get_fixture_by_type(
                            entry, meta.module_path, fn_teardowns
                        )
                    else:  # str
                        effective_session.get_fixture_by_name(
                            entry, meta.module_path, fn_teardowns
                        )
            except (
                FixtureSetupError,
                FixtureNotFoundError,
                AmbiguousFixtureError,
                FixtureCycleError,
            ) as exc:
                return _error_result(str(exc))

        execute = _build_execution_chain(
            resolved,
            _MarkResult(marks=tuple(marks), wrappers=tuple(wrappers)),
            default_timeout,
            session=effective_session,
            debug=debug,
        )
        result = execute()
        _active_ctx = _test_run_context.get()
        if _active_ctx.result_cell:
            _active_ctx.result_cell[0] = result
        return result
    finally:
        sys.modules.pop(unique_name, None)
        _run_teardowns(fn_teardowns, meta.node_id)
        _test_run_context.reset(_run_ctx_token)
