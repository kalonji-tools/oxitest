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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import SharedAsyncSession
    from oxitest._bridge._debugger import DebuggerBackend

from oxitest._bridge._errors import FixtureNotFoundError, FixtureSetupError
from oxitest._bridge._fixture_context import (
    TestRunContext,
    _current_teardown_node_id,
    _test_run_context,
)
from oxitest._bridge._fixture_registry import FixtureRegistry as _FixtureRegistry
from oxitest._bridge._fixture_session import (
    FixtureSession,
    _SessionProtocol,
)
from oxitest._bridge._loader import (
    _load_module,
    _LoadError,
    _resolve_fn,
)
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import (
    MarkHandler,
    MarkWrapper,
    _HandlerContext,
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
    return f"_oxitest_exec_{hashlib.md5(module_path.encode()).hexdigest()[:12]}"  # noqa: S324


def _resolve_debugger_backend(
    session: _SessionProtocol,
    debug_mode: str | None,
) -> DebuggerBackend | None:
    """Resolve the debugger backend from the plugin registry or fall back to pdb.

    Returns None when debug_mode is None (no debugging requested).
    """
    if debug_mode is None:
        return None
    registry = getattr(session, "_plugin_registry", None)
    if registry is not None and registry.debugger_backends:
        return registry.debugger_backends[0][1]
    from oxitest._bridge._debugger import _PdbBackend

    return _PdbBackend()


@dataclass
class _ResolvedTest:
    module: Any
    fn_raw: Any
    fn: Callable[..., Any]
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
    _cache = getattr(session, "_module_cache", None)
    _cached = _cache.get(meta.module_path) if _cache is not None else None
    if _cached is not None:
        module = _cached
        sys.modules[unique_name] = module
    else:
        try:
            module = _load_module(meta.module_path, unique_name)
        except _LoadError as e:
            return e.result
        if _cache is not None:
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
            fn,  # type: ignore[arg-type]
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
                param_kwargs[field_name] = session.get_fixture(
                    fixture_name, meta.module_path, fn_teardowns
                )
    except (FixtureSetupError, FixtureNotFoundError) as exc:
        return _error_result(str(exc))

    all_kwargs: dict[str, Any] = {**fixture_kwargs, **param_kwargs}
    return _ResolvedTest(module, fn_raw, fn, all_kwargs, fn_teardowns)


def _build_execution_chain(
    module: Any,
    fn_raw: object,
    fn_name: str,
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    marks: list[MarkInfo],
    wrappers: list[MarkWrapper],
    default_timeout: int | None,
    shared_session: SharedAsyncSession | None = None,
    session: _SessionProtocol | None = None,
    *,
    debug_mode: str | None = None,
    node_id: str = "",
    backend: DebuggerBackend | None = None,
    show_locals: bool = False,
    show_internals: bool = False,
) -> Callable[[], TestResult]:
    """Build the composed execution callable via middleware pipeline."""
    plan = ExecutionPlan(
        fn=fn,
        fn_name=fn_name,
        kwargs=all_kwargs,
        marks=marks,
        no_message_lines=(),
        is_async=inspect.iscoroutinefunction(fn),
        default_timeout=default_timeout,
        backend=getattr(session, "_async_backend", None),
        shared_session=shared_session,
    )

    def _base() -> TestResult:
        return _run_base(
            fn,
            all_kwargs,
            plan.no_message_lines,
            debug_mode=debug_mode,
            node_id=node_id,
            backend=backend,
            show_locals=show_locals,
            show_internals=show_internals,
        )

    execute = MiddlewareBuilder().build(plan, _base, module, default_timeout)

    # Apply mark wrappers (from evaluate_marks) around the pipeline result
    for wrapper in reversed(wrappers):
        execute = _compose(wrapper, execute)

    return execute


_NULL_SESSION: _SessionProtocol = FixtureSession(_FixtureRegistry())


def _evaluate_marks_phase(
    resolved: _ResolvedTest,
    session: _SessionProtocol,
    module_path: str,
    default_timeout: int | None,
    marks: list[MarkInfo],
) -> tuple[TestResult | None, list[MarkWrapper]]:
    """Evaluate marks and return (short_circuit, wrappers)."""
    _plugin_registry = getattr(session, "_plugin_registry", None)
    _plugin_handlers: list[MarkHandler] = []
    if _plugin_registry is not None:  # pragma: no cover
        _plugin_handlers = [
            _PluginMarkHandler(pw) for pw in _plugin_registry.execution_wrappers
        ]

    ctx = _HandlerContext(
        fn_raw=resolved.fn_raw,
        fn=resolved.fn,
        all_kwargs=resolved.all_kwargs,
        session=session,
        module_path=module_path,
        fn_teardowns=resolved.fn_teardowns,
        default_timeout=default_timeout,
    )
    return evaluate_marks(marks, ctx, plugin_handlers=_plugin_handlers or None)


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
    debug_mode: str | None = None,
    keep_tmp: str | None = None,
    show_locals: bool = False,
    show_internals: bool = False,
) -> TestResult:
    """Load, resolve, and execute a single test function.

    Args:
        meta: Test identity metadata (module path, function name, node ID,
            parametrize case ID, and marks).
        session: Active `FixtureSession` for fixture injection.  When `None`
            a null session is used, meaning no user fixtures are available.
        default_timeout: Per-test timeout in seconds inherited from config.
            Overridden by a ``@mark.timeout`` decorator on the test.
        debug_mode: When set, drop into an interactive debugger.
            ``"post-mortem"`` enters pdb after failure; ``"always"`` enters
            pdb before every test.  ``None`` disables debugging.
        keep_tmp: When set, preserve TempDir contents instead of cleaning up.
            ``"failed"`` preserves only on test failure; ``"always"`` preserves
            unconditionally.  ``None`` always cleans up (default).
        show_locals: When ``True``, capture local variables in each traceback
            frame and include them in the failure output.
        show_internals: When ``True``, include oxitest-internal frames in the
            traceback instead of filtering them out.

    Returns:
        A `TestResult` whose `status` is one of ``"passed"``, ``"failed"``,
        ``"error"``, ``"skipped"``, ``"warned"``, ``"xfailed"``, or
        ``"xpassed"``.
    """
    # Doctest dispatch — bypass normal fixture/mark pipeline
    if meta.fn_name.startswith("<doctest>"):
        from oxitest._bridge._doctest_runner import run_doctest

        doctest_name = meta.fn_name.removeprefix("<doctest>")
        return run_doctest(meta.module_path, doctest_name)

    effective_session: _SessionProtocol = (
        session if session is not None else _NULL_SESSION
    )
    _run_ctx = (
        TestRunContext(
            keep_tmp=keep_tmp,
            result_cell=[None] if keep_tmp else None,
        )
        if keep_tmp is not None
        else None
    )
    _run_ctx_token = _test_run_context.set(_run_ctx)
    backend = _resolve_debugger_backend(effective_session, debug_mode)
    unique_name = _exec_unique_name(meta.module_path)
    resolved = _load_and_resolve(meta, effective_session, unique_name)
    if isinstance(resolved, TestResult):
        return resolved
    module = resolved.module
    fn_raw = resolved.fn_raw
    fn = resolved.fn
    all_kwargs = resolved.all_kwargs
    fn_teardowns = resolved.fn_teardowns

    try:
        marks: list[MarkInfo] = get_marks(fn_raw)
        short_circuit, wrappers = _evaluate_marks_phase(
            resolved, effective_session, meta.module_path, default_timeout, marks
        )
        if short_circuit is not None:
            return short_circuit

        _shared_session = getattr(effective_session, "_shared_session", None)
        _used_shared = getattr(effective_session, "_used_shared_async", False)

        execute = _build_execution_chain(
            module,
            fn_raw,
            meta.fn_name,
            fn,
            all_kwargs,
            marks,
            wrappers,
            default_timeout,
            shared_session=_shared_session if _used_shared else None,
            session=effective_session,
            debug_mode=debug_mode,
            node_id=meta.node_id,
            backend=backend,
            show_locals=show_locals,
            show_internals=show_internals,
        )
        result = execute()
        _active_ctx = _test_run_context.get()
        if _active_ctx is not None and _active_ctx.result_cell is not None:
            _active_ctx.result_cell[0] = result
        return result
    finally:
        sys.modules.pop(unique_name, None)
        _run_teardowns(fn_teardowns, meta.node_id)
        _test_run_context.reset(_run_ctx_token)
