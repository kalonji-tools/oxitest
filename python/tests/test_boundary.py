"""Direct tests for trust-boundary helpers.

Covers all four exported helpers in ``_boundary.py``. The Rule-5 no-op
default behavior for ``safe_call``, ``async_safe_call``, and
``safe_teardown`` is exercised via tests that omit the callback and
assert the default silently swallows without side effects.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Annotated

import oxitest as oxi
from oxitest._bridge._boundary import (
    _ADVANCED_ASYNC_GENS,
    advance_async_gen,
    async_safe_call,
    safe_call,
    safe_teardown,
    safe_type_hints,
    setup_completed,
)
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge.result import Diagnostic


def _async_setup_completed_via_record(agen: object) -> bool:
    """The 3.11 arm of `setup_completed`, reachable on any interpreter."""
    return agen in _ADVANCED_ASYNC_GENS


def _raise() -> None:
    msg = "boom"
    raise RuntimeError(msg)


def test_safe_call_returns_default_when_fn_raises_without_on_error() -> None:
    """Omitted on_error must not prevent default from being returned on failure."""
    sentinel = object()

    result = safe_call(_raise, default=sentinel)

    assert result is sentinel, (
        "safe_call must return default when fn raises and no on_error is provided"
    )


def test_safe_call_invokes_on_error_when_fn_raises() -> None:
    """Provided on_error receives the caught exception before default is returned."""
    captured: list[Exception] = []

    result = safe_call(_raise, default=42, on_error=captured.append)

    assert result == 42, "safe_call must return default when fn raises"
    assert len(captured) == 1, "on_error must be invoked exactly once"
    assert isinstance(captured[0], RuntimeError), (
        "on_error receives the caught exception verbatim"
    )


def test_safe_call_returns_value_when_fn_succeeds() -> None:
    """Fn success must return its value, not the default."""

    def _ok() -> int:
        return 7

    result = safe_call(_ok, default=0)

    assert result == 7, "safe_call returns fn result when no exception is raised"


async def _raise_async() -> None:
    msg = "async boom"
    raise RuntimeError(msg)


def test_async_safe_call_returns_default_when_coro_raises_without_on_error() -> None:
    """Omitted on_error still yields default when the coroutine raises."""
    sentinel = object()

    result = asyncio.run(async_safe_call(_raise_async(), default=sentinel))

    assert result is sentinel, (
        "async_safe_call must return default when coroutine raises with no on_error"
    )


def test_async_safe_call_invokes_on_error_when_coro_raises() -> None:
    """Provided on_error must receive the caught exception from a failing coroutine."""
    captured: list[Exception] = []

    result = asyncio.run(
        async_safe_call(_raise_async(), default=99, on_error=captured.append)
    )

    assert result == 99, "async_safe_call must return default when coroutine raises"
    assert len(captured) == 1, "on_error must be invoked exactly once"
    assert isinstance(captured[0], RuntimeError), (
        "on_error receives the caught exception verbatim"
    )


def test_safe_teardown_swallows_without_warn_when_fn_raises() -> None:
    """Default warn must be a pure no-op — no diagnostic emitted on failure."""
    sink: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(sink)
    try:
        safe_teardown(_raise, "fixture-x")
    finally:
        _diagnostic_collector_var.reset(token)

    assert sink == [], (
        "safe_teardown with default warn must not emit diagnostics — "
        "the default is a pure no-op, not a debug-trace fallback"
    )


def test_safe_teardown_invokes_warn_when_fn_raises() -> None:
    """Provided warn must receive (name, exc) when the teardown fn raises."""
    captured: list[tuple[str, Exception]] = []

    def _record(name: str, exc: Exception) -> None:
        captured.append((name, exc))

    safe_teardown(_raise, "fixture-x", warn=_record)

    assert len(captured) == 1, "warn callback must be invoked exactly once on failure"
    name, exc = captured[0]
    assert name == "fixture-x", "warn receives the fixture name"
    assert isinstance(exc, RuntimeError), "warn receives the caught exception verbatim"


def test_safe_teardown_returns_none_without_calling_warn_when_fn_succeeds() -> None:
    """Successful teardown must run the fn but never invoke warn."""
    captured: list[tuple[str, Exception]] = []
    called = False

    def _ok() -> None:
        nonlocal called
        called = True

    def _record(name: str, exc: Exception) -> None:
        captured.append((name, exc))

    safe_teardown(_ok, "fixture-y", warn=_record)

    assert called, "teardown fn must be invoked"
    assert captured == [], "warn must not be invoked when teardown succeeds"


def _annotated_fn(x: int, y: str) -> None:
    del x, y


def test_safe_type_hints_returns_resolved_hints_for_annotated_fn() -> None:
    """Well-annotated functions must yield the resolved hint dict verbatim."""
    hints = safe_type_hints(_annotated_fn)

    assert hints is not None, "safe_type_hints must not fail on well-annotated fn"
    assert hints["x"] is int, "int annotation must resolve to the int type"
    assert hints["y"] is str, "str annotation must resolve to the str type"


def test_safe_type_hints_returns_none_when_get_type_hints_raises() -> None:
    """Any get_type_hints failure must return None, not propagate."""
    # get_type_hints rejects non-module/class/function/method targets with
    # TypeError — a real failure path that exercises the safe wrapper.
    hints = safe_type_hints(42)

    assert hints is None, (
        "safe_type_hints must swallow the underlying TypeError and return "
        "None so callers can fall back — propagating would crash test "
        "collection on any user function with broken annotations"
    )


def _extras_fn(x: Annotated[int, "metadata"]) -> None:
    del x


def test_safe_type_hints_forwards_kwargs_to_get_type_hints() -> None:
    """include_extras=True must pass through so Annotated metadata survives."""
    with_extras = safe_type_hints(_extras_fn, include_extras=True)
    without_extras = safe_type_hints(_extras_fn)

    assert with_extras is not None, "annotated fn should not fail to resolve"
    assert without_extras is not None, "annotated fn should not fail to resolve"
    assert with_extras["x"] != without_extras["x"], (
        "include_extras=True must preserve Annotated[...] wrapper; "
        "without it the wrapper is stripped"
    )


def test_setup_completed_only_for_suspended_sync_generators() -> None:
    """Only a generator sitting at its yield has setup worth tearing down.

    The three states are not interchangeable: never-started must be False
    because resuming it would *run* the setup, and raised-before-yield must be
    False because the body already unwound (#1962).
    """

    def gen() -> Generator[str, None, None]:
        yield "v"

    created = gen()
    assert not setup_completed(created), (
        "an unstarted generator has no completed setup — resuming it would run "
        "the fixture body during teardown"
    )
    next(created)
    assert setup_completed(created), (
        "a generator suspended at its yield has completed setup and must be torn down"
    )
    created.close()

    def raiser() -> Generator[str, None, None]:
        msg = "boom"
        raise RuntimeError(msg)
        yield "never"

    closed = raiser()
    with oxi.raises(RuntimeError):
        next(closed)
    assert not setup_completed(closed), (
        "a generator that raised before yielding has already unwound; there is "
        "nothing left to resume"
    )


def test_setup_completed_only_for_async_generators_advanced_through_the_helper() -> (
    None
):
    """Async setup-completion is bookkeeping, not introspection.

    There is no stdlib predicate for this across 3.11 to 3.14 —
    ``getasyncgenstate`` and ``ag_suspended`` are 3.12+, and the pre-3.12
    ``f_lasti == -1`` idiom is wrong on both 3.11 and 3.12. So the one function
    that advances an async fixture records that it did, and this is what the
    drain reads (#1962).
    """

    async def agen() -> AsyncGenerator[str, None]:
        yield "v"

    async def drive() -> None:
        created = agen()
        assert not setup_completed(created), (
            "an async generator that was never advanced has no completed setup "
            "— resuming it would run the fixture body during teardown"
        )

        advanced = agen()
        value = await advance_async_gen(advanced)
        assert value == "v", "the helper must return the yielded value"
        assert setup_completed(advanced), (
            "advancing through the helper is what records setup completion; "
            "without it every async fixture teardown would be skipped"
        )
        await advanced.aclose()

        async def araiser() -> AsyncGenerator[str, None]:
            msg = "boom"
            raise RuntimeError(msg)
            yield "never"

        failed = araiser()
        with oxi.raises(RuntimeError):
            await advance_async_gen(failed)
        assert not setup_completed(failed), (
            "a body that raised before yielding never reached the mark, so it "
            "has nothing to tear down"
        )

    asyncio.run(drive())


def test_the_311_fallback_arm_reads_the_advance_record() -> None:
    """Exercise the bookkeeping arm on every version, not just on 3.11.

    ``setup_completed`` asks the interpreter on 3.12+ and falls back to the
    record kept by ``advance_async_gen`` on 3.11, which has no equivalent
    question to ask. Only one arm runs per interpreter, so without this the
    3.11 arm would be exercised by exactly one CI job and by nothing a
    developer runs — and the previous attempt at a version-branched predicate
    shipped broken for precisely that reason (#1962).
    """

    async def agen() -> AsyncGenerator[str, None]:
        yield "v"

    async def drive() -> None:
        created = agen()
        assert not _async_setup_completed_via_record(created), (
            "the fallback must not claim setup for a generator that was never "
            "advanced through the helper"
        )

        advanced = agen()
        await advance_async_gen(advanced)
        assert _async_setup_completed_via_record(advanced), (
            "advance_async_gen is what writes the record the 3.11 arm reads; "
            "if this fails, every async fixture teardown is skipped on 3.11"
        )
        await advanced.aclose()

    asyncio.run(drive())
