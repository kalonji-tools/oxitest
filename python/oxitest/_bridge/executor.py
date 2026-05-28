"""Test execution orchestration for the oxitest bridge.

Loads the target module, resolves fixtures and parametrize values, evaluates
marks, builds the middleware pipeline, and returns a `TestResult`.  This is
the single entry point called by the Rust core (via PyO3) and by the parallel
worker subprocess.
"""

from __future__ import annotations

__all__ = [
    "DebugMode",
    "_print_debug_banner",
    "_print_trace_banner",
    "_suspend_and_trace",
    "_suspend_capture",
    "run_test",
]

import contextlib
import functools
import hashlib
import inspect
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import SharedAsyncSession

from oxitest._bridge._errors import FixtureNotFoundError, FixtureSetupError
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
    ExecutionWrapper,
    MarkHandler,
    _HandlerContext,
    _PluginMarkHandler,
    evaluate_marks,
)
from oxitest._bridge._metadata import (
    get_fixture_name as _get_fixture_name,
    get_marks,
)
from oxitest._bridge._middleware import (
    AsyncBridgeMiddleware,
    AsyncDepGuardMiddleware,
    BareAssertMiddleware,
    ExecutionPlan,
    TimeoutMiddleware,
    _check_warnings,
    _compose,
    _dispatch_exception,
    _is_debuggable,
    build_pipeline,
)
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge._timeout import OxitestTimeoutError
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.result import StatusKind, TestResult, _error_result


class DebugMode(StrEnum):
    """Debug mode passed from Rust via the bridge.

    StrEnum values match the Rust ``DebugMode::as_str()`` output so
    PyO3 string extraction works without custom glue.
    """

    POST_MORTEM = "post-mortem"
    ALWAYS = "always"


@functools.cache
def _exec_unique_name(module_path: str) -> str:
    return f"_oxitest_exec_{hashlib.md5(module_path.encode()).hexdigest()[:12]}"  # noqa: S324


def _suspend_capture(all_kwargs: dict[str, Any]) -> None:
    """Restore real stdout/stderr by suspending any active capture fixtures."""
    from oxitest._bridge._builtins._capture import _FdCapture, _StdCapture

    for v in all_kwargs.values():
        if isinstance(v, (_StdCapture, _FdCapture)):
            v._restore()


def _print_debug_banner(node_id: str, exc: BaseException, *, file: Any = None) -> None:
    """Print a banner before dropping into the debugger."""
    import sys as _sys

    out = file if file is not None else _sys.__stderr__
    width = max(60, len(node_id) + 12)
    header = f"── DEBUG {node_id} "
    header += "─" * (width - len(header))
    print(header, file=out)
    print(f"{type(exc).__name__}: {exc}", file=out)
    print("Entering debugger (type 'h' for help, 'q' to quit)", file=out)


def _print_trace_banner(node_id: str, *, file: Any = None) -> None:
    """Print a banner before stepping into a test."""
    import sys as _sys

    out = file if file is not None else _sys.__stderr__
    width = max(60, len(node_id) + 12)
    header = f"── TRACE {node_id} "
    header += "─" * (width - len(header))
    print(header, file=out)
    print("Stepping into test (type 'c' to run, 'q' to quit)", file=out)


def _suspend_and_trace(all_kwargs: dict[str, Any], node_id: str) -> None:
    """Drop into pdb before test execution, with capture temporarily suspended."""
    import pdb

    from oxitest._bridge._builtins._capture import _FdCapture, _StdCapture

    managers = [
        v.disabled()
        for v in all_kwargs.values()
        if isinstance(v, (_StdCapture, _FdCapture))
    ]

    with contextlib.ExitStack() as stack:
        for mgr in managers:
            stack.enter_context(mgr)
        _print_trace_banner(node_id)
        pdb.set_trace()


def _run_base(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: list[int],
    *,
    debug_mode: str | None = None,
    node_id: str = "",
) -> TestResult:
    """Run the test function and map exceptions to TestResult."""
    if debug_mode == DebugMode.ALWAYS:
        _suspend_and_trace(all_kwargs, node_id)
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn(**all_kwargs)
        has_warnings, warning_msg = _check_warnings(w, all_kwargs)
        if has_warnings:
            return TestResult(
                status=StatusKind.WARNED,
                message=warning_msg,
                no_message_lines=no_message_lines,
            )
        return TestResult(status=StatusKind.PASSED, no_message_lines=no_message_lines)
    except OxitestTimeoutError:
        raise  # propagate to timeout wrapper
    except BaseException as exc:
        if debug_mode in (DebugMode.POST_MORTEM, DebugMode.ALWAYS) and _is_debuggable(
            exc
        ):
            _suspend_capture(all_kwargs)
            _print_debug_banner(node_id, exc)
            import bdb
            import pdb

            try:
                pdb.post_mortem(exc.__traceback__)
            except bdb.BdbQuit:
                raise
        result = _dispatch_exception(exc)
        if result is not None:
            return result
        raise


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
    wrappers: list[ExecutionWrapper],
    default_timeout: int | None,
    shared_session: SharedAsyncSession | None = None,
    session: _SessionProtocol | None = None,
    *,
    debug_mode: str | None = None,
    node_id: str = "",
) -> Callable[[], TestResult]:
    """Build the composed execution callable via middleware pipeline."""
    plan = ExecutionPlan(
        fn=fn,
        fn_name=fn_name,
        kwargs=all_kwargs,
        marks=marks,
        no_message_lines=[],
        is_async=inspect.iscoroutinefunction(fn),
        default_timeout=default_timeout,
        backend=getattr(session, "_async_backend", None),
        shared_session=shared_session,
    )

    # BareAssert must run first (populates plan.no_message_lines)
    middlewares: list[Any] = [
        BareAssertMiddleware(module),
        AsyncDepGuardMiddleware(),
        TimeoutMiddleware(default_timeout),
        AsyncBridgeMiddleware(),
    ]

    def _base() -> TestResult:
        return _run_base(
            fn,
            all_kwargs,
            plan.no_message_lines,
            debug_mode=debug_mode,
            node_id=node_id,
        )

    execute = build_pipeline(middlewares, plan, _base)

    # Apply mark wrappers (from evaluate_marks) around the pipeline result
    for wrapper in reversed(wrappers):
        execute = _compose(wrapper, execute)

    return execute


_NULL_SESSION: _SessionProtocol = FixtureSession(_FixtureRegistry())


def run_test(
    meta: TestMeta,
    session: _SessionProtocol | None = None,
    default_timeout: int | None = None,
    debug_mode: str | None = None,
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

    Returns:
        A `TestResult` whose `status` is one of ``"passed"``, ``"failed"``,
        ``"error"``, ``"skipped"``, ``"warned"``, ``"xfailed"``, or
        ``"xpassed"``.
    """
    effective_session: _SessionProtocol = (
        session if session is not None else _NULL_SESSION
    )
    unique_name = _exec_unique_name(meta.module_path)
    resolved = _load_and_resolve(meta, effective_session, unique_name)
    if isinstance(resolved, TestResult):
        return resolved
    module = resolved.module
    fn_raw = resolved.fn_raw
    fn = resolved.fn
    all_kwargs = resolved.all_kwargs
    fn_teardowns = resolved.fn_teardowns

    ctx = _HandlerContext(
        fn_raw=fn_raw,
        fn=fn,
        all_kwargs=all_kwargs,
        session=effective_session,
        module_path=meta.module_path,
        fn_teardowns=fn_teardowns,
        default_timeout=default_timeout,
    )
    try:
        marks: list[MarkInfo] = get_marks(fn_raw)

        # Build plugin mark handlers for unified dispatch
        _plugin_registry = getattr(effective_session, "_plugin_registry", None)
        _plugin_handlers: list[MarkHandler] = []
        if _plugin_registry is not None:  # pragma: no cover
            _plugin_handlers = [
                _PluginMarkHandler(pw) for pw in _plugin_registry.execution_wrappers
            ]

        short_circuit, wrappers = evaluate_marks(
            marks, ctx, plugin_handlers=_plugin_handlers or None
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
        )
        return execute()
    finally:
        sys.modules.pop(unique_name, None)
        for td in reversed(fn_teardowns):
            # teardown errors already printed by FixtureSession._safe_call
            with contextlib.suppress(Exception):
                td()
