from __future__ import annotations

__all__ = ["run_test"]

import contextlib
import functools
import hashlib
import inspect
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
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
    _compose,
    _handle_assertion_error,
    _handle_runtime_exception,
    build_pipeline,
)
from oxitest._bridge._timeout import OxitestTimeoutError
from oxitest._bridge.fixtures import FixtureTeardownWarning
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.result import StatusKind, TestResult, _error_result


@functools.cache
def _exec_unique_name(module_path: str) -> str:
    return f"_oxitest_exec_{hashlib.md5(module_path.encode()).hexdigest()[:12]}"  # noqa: S324


def _run_base(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: list[int],
) -> TestResult:
    """Run the test function and map exceptions to TestResult."""
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn(**all_kwargs)
        caught: list[str] = [
            f"{wi.category.__name__}: {wi.message}"
            for wi in w
            if not issubclass(wi.category, FixtureTeardownWarning)
        ]
        if caught:
            return TestResult(
                status=StatusKind.WARNED,
                message="\n".join(str(c) for c in caught),
                no_message_lines=no_message_lines,
            )
        return TestResult(status=StatusKind.PASSED, no_message_lines=no_message_lines)
    except OxitestTimeoutError:
        raise  # propagate to timeout wrapper
    except AssertionError as exc:
        return _handle_assertion_error(exc)
    except Exception as exc:
        result = _handle_runtime_exception(exc)
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
    module_path: str,
    fn_name: str,
    session: _SessionProtocol,
    param_id: str | None,
) -> TestResult | _ResolvedTest:
    """Load module, resolve function, parametrize, and fixtures.

    Returns _ResolvedTest on success, or TestResult on module/fn/resolve errors.
    """
    unique_name = _exec_unique_name(module_path)
    _cache = getattr(session, "_module_cache", None)
    _cached = _cache.get(module_path) if _cache is not None else None
    if _cached is not None:
        module = _cached
        sys.modules[unique_name] = module
    else:
        try:
            module = _load_module(module_path, unique_name)
        except _LoadError as e:
            return e.result
        if _cache is not None:
            _cache.set(module_path, module)
    try:
        fn_raw, fn = _resolve_fn(module, fn_name, module_path)
    except _LoadError as e:
        return e.result

    # Resolve parametrize case values
    try:
        param_kwargs, fixref_names = resolve_parametrize(fn_raw, fn, param_id)
    except ParametrizeError as exc:
        return _error_result(str(exc))

    # Resolve fixtures from function signature
    fn_teardowns: list[Callable[[], None]] = []
    try:
        fixture_kwargs: dict[str, Any]
        fixture_kwargs, fn_teardowns = session.resolve_for_test(
            fn,  # type: ignore[arg-type]
            module_path,
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
                    fixture_name, namespace, module_path, fn_teardowns
                )
            else:
                param_kwargs[field_name] = session.get_fixture(
                    fixture_name, module_path, fn_teardowns
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
        return _run_base(fn, all_kwargs, plan.no_message_lines)

    execute = build_pipeline(middlewares, plan, _base)

    # Apply mark wrappers (from evaluate_marks) around the pipeline result
    for wrapper in reversed(wrappers):
        execute = _compose(wrapper, execute)

    return execute


_NULL_SESSION: _SessionProtocol = FixtureSession(_FixtureRegistry())


def run_test(
    module_path: str,
    fn_name: str,
    session: _SessionProtocol | None = None,
    param_id: str | None = None,
    default_timeout: int | None = None,
) -> TestResult:
    """Run a test function and return a TestResult.

    Status values: "passed", "failed", "error", "skipped", "warned", "xfailed",
    "xpassed".
    session: optional FixtureSession for fixture injection.
    """
    effective_session: _SessionProtocol = (
        session if session is not None else _NULL_SESSION
    )
    unique_name = _exec_unique_name(module_path)
    resolved = _load_and_resolve(module_path, fn_name, effective_session, param_id)
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
        module_path=module_path,
        fn_teardowns=fn_teardowns,
        default_timeout=default_timeout,
    )
    try:
        marks: list[MarkInfo] = get_marks(fn_raw)

        # Build plugin mark handlers for unified dispatch
        _plugin_registry = getattr(effective_session, "_plugin_registry", None)
        _plugin_handlers: list[MarkHandler] = []
        if _plugin_registry is not None:  # pragma: no cover
            from oxitest._bridge._mark_registry import _PluginMarkHandler

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
            fn_name,
            fn,
            all_kwargs,
            marks,
            wrappers,
            default_timeout,
            shared_session=_shared_session if _used_shared else None,
            session=effective_session,
        )
        return execute()
    finally:
        sys.modules.pop(unique_name, None)
        for td in reversed(fn_teardowns):
            # teardown errors already printed by FixtureSession._safe_call
            with contextlib.suppress(Exception):
                td()
