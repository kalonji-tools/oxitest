from __future__ import annotations

import ast
import functools
import hashlib
import inspect
import linecache
import reprlib
import sys
import textwrap
import traceback
import warnings
from collections.abc import Callable
from typing import Any, cast

from oxitest._bridge._exceptions import FixtureNotFoundError, FixtureSetupError
from oxitest._bridge._loader import (
    _load_module,
    _LoadError,
    _resolve_fn,
)
from oxitest._bridge._metadata import (
    get_fixture_name as _get_fixture_name,
    get_marks,
)
from oxitest._bridge._timeout import OxitestTimeoutError
from oxitest._bridge.ast_rewriter import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)
from oxitest._bridge.fixtures import (
    FixtureTeardownWarning,
    MarkInfo,
    _SessionProtocol,
)
from oxitest._bridge.marks import ExecutionWrapper, _HandlerContext, evaluate_marks
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.result import Frame, TestResult

_REPR_MAX = 80
_repr = reprlib.Repr()


@functools.cache
def _exec_unique_name(module_path: str) -> str:
    return f"_oxitest_exec_{hashlib.md5(module_path.encode()).hexdigest()[:12]}"  # noqa: S324


_repr.maxstring = _REPR_MAX
_repr.maxother = _REPR_MAX


def _repr_safe(value: object) -> str:
    try:
        return _repr.repr(value)
    except Exception:
        return "<repr failed>"


def _find_bare_asserts(fn: object) -> list[int]:
    """Return absolute file line numbers of assert statements with no message."""
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


def _error_result(
    msg: str, file: str = "", lineno: int = 0, source_line: str = ""
) -> TestResult:
    return TestResult(
        status="error", message=msg, file=file, lineno=lineno, source_line=source_line
    )


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
        status="failed",
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
        return TestResult(status="skipped", message=str(exc))
    if isinstance(exc, Exception):
        file, lineno, source_line = _get_location(exc)
        return TestResult(
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            file=file,
            lineno=lineno,
            source_line=source_line,
            exc_type=type(exc).__name__,
            frames=_get_frames(exc),
        )
    return None


def _compose(
    wrapper: ExecutionWrapper, inner: Callable[[], TestResult]
) -> Callable[[], TestResult]:
    """Return a callable that runs wrapper(inner).

    Using a named function avoids the loop-variable capture problem
    (ruff B023) that a bare lambda inside a for-loop would cause.
    """
    return lambda: wrapper(inner)


class _NullFixtureSession:
    """Null Object for when no conftest session is available.

    Allows run_test to treat session as always present, eliminating guards.
    """

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        return {}, []

    def get_fixture(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        raise FixtureNotFoundError(name)

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        raise FixtureNotFoundError(name, namespace=namespace)


_NULL_SESSION: _SessionProtocol = _NullFixtureSession()


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
    try:
        _cache = getattr(effective_session, "_module_cache", None)
        _cached = _cache.get(module_path) if _cache is not None else None
        if _cached is not None:
            module = _cached
            sys.modules[unique_name] = module
        else:
            module = _load_module(module_path, unique_name)
            if _cache is not None:
                _cache.set(module_path, module)
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
        fixture_kwargs, fn_teardowns = effective_session.resolve_for_test(
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
            namespace = getattr(fixture_fn, "_oxitest_namespace", None)
            if namespace:
                param_kwargs[field_name] = effective_session.get_fixture_in_namespace(
                    fixture_name, namespace, module_path, fn_teardowns
                )
            else:
                param_kwargs[field_name] = effective_session.get_fixture(
                    fixture_name, module_path, fn_teardowns
                )
    except (FixtureSetupError, FixtureNotFoundError) as exc:
        return _error_result(str(exc))

    all_kwargs: dict[str, Any] = {**fixture_kwargs, **param_kwargs}

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
        short_circuit, wrappers = evaluate_marks(marks, ctx)
        if short_circuit is not None:
            return short_circuit

        # Apply global default timeout if no per-test @timeout mark
        if default_timeout is not None and not any(m.name == "timeout" for m in marks):
            from oxitest._bridge._timeout import _timeout_context

            def _default_timeout_wrapper(
                next_fn: Callable[[], TestResult],
                _t: int = default_timeout,
            ) -> TestResult:
                try:
                    with _timeout_context(_t):
                        return next_fn()
                except OxitestTimeoutError:
                    return TestResult(
                        status="timeout",
                        message=f"Timed out after {_t}s",
                    )

            wrappers.append(_default_timeout_wrapper)

        # Plugin execution wrappers — match by marker name
        from oxitest._bridge.plugin_loader import get_registry  # pragma: no cover

        for pw in get_registry().execution_wrappers:  # pragma: no cover
            for mark in marks:
                if mark.name == pw.marker:
                    marker_args = {**dict(enumerate(mark.args)), **mark.kwargs}
                    _pw, _args = pw, marker_args  # capture for closure

                    def _plugin_wrapper(
                        next_fn: Callable[[], TestResult],
                        _w: Any = _pw,
                        _a: dict[int | str, Any] = _args,
                    ) -> TestResult:
                        return _w.wrap(next_fn, _a)

                    wrappers.append(_plugin_wrapper)
                    break  # one match per wrapper per test

        # Base execution: run fn and map exceptions to TestResult
        _bare_map: dict[str, list[int]] = getattr(module, "_oxitest_bare_asserts", {})
        _simple_fn_name = fn_name.split("::")[-1]  # strip class prefix
        no_message_lines = _bare_map.get(_simple_fn_name, _find_bare_asserts(fn_raw))

        def base() -> TestResult:
            try:
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    fn(**all_kwargs)  # type: ignore[operator]
                caught: list[str] = [
                    f"{wi.category.__name__}: {wi.message}"
                    for wi in w
                    if not issubclass(wi.category, FixtureTeardownWarning)
                ]
                if caught:
                    return TestResult(
                        status="warned",
                        message="\n".join(str(c) for c in caught),
                        no_message_lines=no_message_lines,
                    )
                return TestResult(status="passed", no_message_lines=no_message_lines)
            except OxitestTimeoutError:
                raise  # propagate to timeout wrapper
            except AssertionError as exc:
                return _handle_assertion_error(exc)
            except BaseException as exc:
                result = _handle_runtime_exception(exc)
                if result is not None:
                    return result
                raise

        # Compose wrappers: last appended = outermost
        execute: Callable[[], TestResult] = base
        for wrapper in reversed(wrappers):
            execute = _compose(wrapper, execute)

        return execute()
    finally:
        sys.modules.pop(unique_name, None)
        for td in reversed(fn_teardowns):
            try:
                td()
            except Exception:
                pass  # teardown errors already printed by FixtureSession._safe_call
