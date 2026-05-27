from __future__ import annotations

__all__ = [
    "AsyncBridgeMiddleware",
    "AsyncDepGuardMiddleware",
    "BareAssertMiddleware",
    "ExecutionPlan",
    "Middleware",
    "TimeoutMiddleware",
    "build_pipeline",
]

import ast
import inspect
import linecache
import reprlib
import textwrap
import traceback
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from oxitest._bridge._builtins._warncapture import _WarnCapture
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import ExecutionWrapper
from oxitest._bridge._timeout import OxitestTimeoutError, make_timeout_wrapper
from oxitest._bridge.ast_rewriter import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)
from oxitest._bridge.fixtures import FixtureTeardownWarning
from oxitest._bridge.result import Frame, StatusKind, TestResult, _error_result

_REPR_MAX = 80
_repr = reprlib.Repr()
_repr.maxstring = _REPR_MAX
_repr.maxother = _REPR_MAX


def _repr_safe(value: object) -> str:
    try:
        return _repr.repr(value)
    except Exception:
        return "<repr failed>"


def _get_location(exc: BaseException) -> tuple[str, int, str]:
    """Extract (file, lineno, source_line) from the innermost traceback frame."""
    tb = exc.__traceback__
    if tb is None:
        return ("", 0, "")
    while tb.tb_next is not None:
        tb = tb.tb_next
    file = tb.tb_frame.f_code.co_filename
    lineno = tb.tb_lineno
    source_line = linecache.getline(file, lineno).strip()
    return (file, lineno, source_line)


def _get_frames(exc: BaseException) -> list[Frame]:
    """Extract structured traceback frames from an exception."""
    tb = exc.__traceback__
    if tb is None:
        return []
    return [
        Frame(file=f.filename, lineno=f.lineno or 0, name=f.name, line=f.line or "")
        for f in traceback.extract_tb(tb)
    ]


def _handle_assertion_error(exc: AssertionError) -> TestResult:
    """Map an AssertionError to a failed TestResult."""
    file, lineno, source_line = _get_location(exc)
    if isinstance(exc, _OxitestAssertionError):
        msg = exc.args[0] if exc.args and exc.args[0] else ""
        left_repr = _repr_safe(exc.left)
        right_repr = _repr_safe(exc.right) if exc.right is not _OXITEST_NO_RHS else ""
        op = exc.op
    else:
        msg = str(exc) if str(exc) else ""
        left_repr = right_repr = op = ""
    return TestResult(
        status=StatusKind.FAILED,
        message=msg,
        file=file,
        lineno=lineno,
        source_line=source_line,
        left=left_repr,
        right=right_repr,
        op=op,
        exc_type="AssertionError",
        frames=_get_frames(exc),
    )


def _handle_runtime_exception(exc: BaseException) -> TestResult | None:
    """Map a non-assertion BaseException to a TestResult, or None to re-raise."""
    exc_type = type(exc).__name__
    if exc_type in ("Skipped", "SkipTest"):
        return TestResult(status=StatusKind.SKIPPED, message=str(exc))
    if isinstance(exc, Exception):
        file, lineno, source_line = _get_location(exc)
        return TestResult(
            status=StatusKind.ERROR,
            message=f"{type(exc).__name__}: {exc}",
            file=file,
            lineno=lineno,
            source_line=source_line,
            exc_type=type(exc).__name__,
            frames=_get_frames(exc),
        )
    return None


def _check_warnings(
    caught: list[warnings.WarningMessage],
    all_kwargs: dict[str, Any],
) -> tuple[bool, str]:
    """Filter caught warnings, excluding captured and teardown warnings."""
    warn_capture = next(
        (v for v in all_kwargs.values() if isinstance(v, _WarnCapture)), None
    )
    captured_ids = warn_capture._all_captured_ids if warn_capture else set()
    relevant: list[str] = [
        f"{wi.category.__name__}: {wi.message}"
        for wi in caught
        if not issubclass(wi.category, FixtureTeardownWarning)
        and id(wi) not in captured_ids
    ]
    if not relevant:
        return False, ""
    return True, "\n".join(relevant)


def _dispatch_exception(exc: BaseException) -> TestResult | None:
    """Map exception to TestResult. Returns None to signal re-raise."""
    if isinstance(exc, AssertionError):
        return _handle_assertion_error(exc)
    return _handle_runtime_exception(exc)


def _compose(
    wrapper: ExecutionWrapper, inner: Callable[[], TestResult]
) -> Callable[[], TestResult]:
    """Return a callable that runs wrapper(inner).

    Using a named function avoids the loop-variable capture problem
    (ruff B023) that a bare lambda inside a for-loop would cause.
    """
    return lambda: wrapper(inner)


def _find_bare_asserts(fn: object) -> list[int]:
    """Fallback bare-assert detection for modules loaded without the AST rewriter.

    Normally _oxitest_bare_asserts is populated by ast_rewriter.py during module
    import. This function re-parses the function source at runtime when that
    attribute is missing (e.g., module imported outside _load_module).
    """
    try:
        source_lines, start_line = inspect.getsourcelines(cast(Any, fn))
        source = textwrap.dedent("".join(source_lines))
        tree = ast.parse(source)
        return [
            n.lineno + start_line - 1
            for n in ast.walk(tree)
            if isinstance(n, ast.Assert) and n.msg is None
        ]
    except (OSError, TypeError, SyntaxError):
        return []


async def _run_base_async(  # pragma: no cover — runs inside asyncio.run()
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: list[int],
) -> TestResult:
    """Run an async test function and map exceptions to TestResult."""
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await fn(**all_kwargs)
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
        result = _dispatch_exception(exc)
        if result is not None:
            return result
        raise


@dataclass
class ExecutionPlan:
    """Immutable context passed through the middleware stack."""

    fn: Callable[..., Any]
    fn_name: str
    kwargs: dict[str, Any]
    marks: list[MarkInfo]
    no_message_lines: list[int]
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
        plan.no_message_lines = _bare_map.get(
            _simple_fn_name, _find_bare_asserts(plan.fn)
        )
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


class AsyncBridgeMiddleware:
    """Replaces base runner with async bridge when fn is async."""

    def apply(
        self, plan: ExecutionPlan, next_fn: Callable[[], TestResult]
    ) -> Callable[[], TestResult]:
        if not inspect.iscoroutinefunction(plan.fn):
            return next_fn

        from oxitest._bridge._async_backend import AsyncioBackend
        from oxitest._bridge._errors import FixtureSetupError

        backend = plan.backend or AsyncioBackend()

        _timeout_secs: int | None = None
        for m in plan.marks:
            if m.name == "timeout":
                _timeout_secs = int(m.kwargs["seconds"])  # type: ignore[arg-type]  # ty: ignore
                break
        if _timeout_secs is None:
            _timeout_secs = plan.default_timeout

        async def _async_core() -> TestResult:  # pragma: no cover
            resolved: dict[str, Any] = {}
            async_teardowns: list[tuple[str, Any]] = []
            for k, v in plan.kwargs.items():
                if inspect.isasyncgen(v):
                    try:
                        resolved[k] = await anext(v)
                        async_teardowns.append((k, v))
                    except Exception as exc:
                        return _error_result(str(FixtureSetupError(k, exc)))
                elif inspect.iscoroutine(v):
                    try:
                        resolved[k] = await v
                    except Exception as exc:
                        return _error_result(str(FixtureSetupError(k, exc)))
                else:
                    resolved[k] = v
            try:
                if _timeout_secs is not None:
                    import asyncio

                    try:
                        return await asyncio.wait_for(
                            _run_base_async(plan.fn, resolved, plan.no_message_lines),
                            timeout=_timeout_secs,
                        )
                    except TimeoutError:
                        raise OxitestTimeoutError() from None
                return await _run_base_async(plan.fn, resolved, plan.no_message_lines)
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
