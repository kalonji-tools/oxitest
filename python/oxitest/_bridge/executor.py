from __future__ import annotations

__all__ = ["run_test"]

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
from typing import TYPE_CHECKING, Any, cast

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
    _HandlerContext,
    evaluate_marks,
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
from oxitest._bridge.fixtures import FixtureTeardownWarning
from oxitest._bridge.parametrize import ParametrizeError, resolve_parametrize
from oxitest._bridge.plugin_loader import PluginRegistry
from oxitest._bridge.result import Frame, StatusKind, TestResult, _error_result

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


def _compose(
    wrapper: ExecutionWrapper, inner: Callable[[], TestResult]
) -> Callable[[], TestResult]:
    """Return a callable that runs wrapper(inner).

    Using a named function avoids the loop-variable capture problem
    (ruff B023) that a bare lambda inside a for-loop would cause.
    """
    return lambda: wrapper(inner)


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


class _ResolveError(Exception):
    """Internal: wraps an early-return TestResult from resolution failures."""

    def __init__(self, result: TestResult) -> None:
        self.result = result


def _load_and_resolve(
    module_path: str,
    fn_name: str,
    session: _SessionProtocol,
    param_id: str | None,
) -> tuple[
    Any,
    object,
    Callable[..., Any],
    dict[str, Any],
    list[Callable[[], None]],
]:
    """Load module, resolve function, parametrize, and fixtures.

    Returns (module, fn_raw, fn, all_kwargs, fn_teardowns).
    Raises _LoadError on module/fn errors.
    Raises _ResolveError on parametrize/fixture errors.
    """
    unique_name = _exec_unique_name(module_path)
    _cache = getattr(session, "_module_cache", None)
    _cached = _cache.get(module_path) if _cache is not None else None
    if _cached is not None:
        module = _cached
        sys.modules[unique_name] = module
    else:
        module = _load_module(module_path, unique_name)
        if _cache is not None:
            _cache.set(module_path, module)
    fn_raw, fn = _resolve_fn(module, fn_name, module_path)

    # Resolve parametrize case values
    try:
        param_kwargs, fixref_names = resolve_parametrize(fn_raw, fn, param_id)
    except ParametrizeError as exc:
        raise _ResolveError(_error_result(str(exc))) from None

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
        raise _ResolveError(_error_result(str(exc))) from None

    all_kwargs: dict[str, Any] = {**fixture_kwargs, **param_kwargs}
    return module, fn_raw, fn, all_kwargs, fn_teardowns


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
    plugin_registry: PluginRegistry | None = None,
) -> Callable[[], TestResult]:
    """Build the composed execution callable from wrappers and base runner.

    Appends default-timeout and plugin wrappers to the list, then composes
    them around ``_run_base`` so the last-appended wrapper is outermost.
    """
    # Apply global default timeout if no per-test @timeout mark
    if default_timeout is not None and not any(m.name == "timeout" for m in marks):
        from oxitest._bridge._timeout import make_timeout_wrapper

        wrappers.append(make_timeout_wrapper(default_timeout))

    # Plugin execution wrappers — match by marker name
    _plugin_reg = plugin_registry or PluginRegistry()

    for pw in _plugin_reg.execution_wrappers:  # pragma: no cover
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

    # Bare-assert map lookup
    # _oxitest_bare_asserts is set by ast_rewriter.py during module import.
    # Falls back to _find_bare_asserts (runtime AST parse) if the module was
    # loaded without rewriting.
    _bare_map: dict[str, list[int]] = getattr(module, "_oxitest_bare_asserts", {})
    _simple_fn_name = fn_name.split("::")[-1]  # strip class prefix
    no_message_lines = _bare_map.get(_simple_fn_name, _find_bare_asserts(fn_raw))

    # Determine the effective timeout for async tests.
    # Sync tests use the sync timeout wrapper (unchanged).
    _timeout_secs: int | None = None
    for m in marks:
        if m.name == "timeout":
            _timeout_secs = int(m.kwargs["seconds"])  # type: ignore[arg-type]  # ty: ignore
            break
    if _timeout_secs is None and default_timeout is not None:
        _timeout_secs = default_timeout

    # Reject async fixture values in sync tests
    if not inspect.iscoroutinefunction(fn):
        for k, v in all_kwargs.items():
            if inspect.iscoroutine(v) or inspect.isasyncgen(v):
                if inspect.iscoroutine(v):
                    v.close()  # prevent "coroutine was never awaited" warning
                _msg = (
                    f"async fixture '{k}' cannot be used by sync test "
                    f"'{fn_name}' \u2014 make the test async def"
                )
                return lambda: _error_result(_msg)

    # Compose wrappers: last appended = outermost
    if inspect.iscoroutinefunction(fn):
        from oxitest._bridge._async_backend import get_async_backend

        backend = get_async_backend()

        async def _async_core() -> TestResult:  # pragma: no cover
            # Resolve async fixture values (coroutines and async generators).
            # Sync fixture values pass through unchanged.
            resolved: dict[str, Any] = {}
            async_teardowns: list[tuple[str, Any]] = []  # (name, async_gen)
            for k, v in all_kwargs.items():
                if inspect.isasyncgen(v):
                    try:
                        resolved[k] = await anext(v)
                        async_teardowns.append((k, v))
                    except Exception as exc:
                        from oxitest._bridge._errors import FixtureSetupError

                        return _error_result(str(FixtureSetupError(k, exc)))
                elif inspect.iscoroutine(v):
                    try:
                        resolved[k] = await v
                    except Exception as exc:
                        from oxitest._bridge._errors import FixtureSetupError

                        return _error_result(str(FixtureSetupError(k, exc)))
                else:
                    resolved[k] = v
            try:
                if _timeout_secs is not None:
                    import asyncio

                    try:
                        return await asyncio.wait_for(
                            _run_base_async(fn, resolved, no_message_lines),
                            timeout=_timeout_secs,
                        )
                    except TimeoutError:
                        raise OxitestTimeoutError() from None
                return await _run_base_async(fn, resolved, no_message_lines)
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

        if shared_session is not None:

            def _base() -> TestResult:  # pragma: no cover
                return shared_session.run(_async_core())
        else:

            def _base() -> TestResult:
                return backend.run(_async_core())
    else:

        def _base() -> TestResult:
            return _run_base(fn, all_kwargs, no_message_lines)

    execute: Callable[[], TestResult] = _base
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
    try:
        module, fn_raw, fn, all_kwargs, fn_teardowns = _load_and_resolve(
            module_path, fn_name, effective_session, param_id
        )
    except _LoadError as e:
        return e.result
    except _ResolveError as e:
        return e.result

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

        _shared_session = getattr(effective_session, "_shared_session", None)
        _used_shared = getattr(effective_session, "_used_shared_async", False)

        _plugin_registry = getattr(effective_session, "_plugin_registry", None)

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
            plugin_registry=_plugin_registry,
        )
        return execute()
    finally:
        sys.modules.pop(unique_name, None)
        for td in reversed(fn_teardowns):
            try:
                td()
            except Exception:
                pass  # teardown errors already printed by FixtureSession._safe_call
