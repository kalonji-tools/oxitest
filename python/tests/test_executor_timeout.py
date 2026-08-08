"""Tests for timeout mark execution during sync and async tests."""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncGenerator
from types import MappingProxyType

from oxitest import TempDir
from oxitest._bridge._errors import OxitestTimeoutError
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._middleware import ExecutionPlan, _effective_timeout_secs
from oxitest._bridge._timeout import (
    TimeoutOff,
    TimeoutSet,
    _IdleHandler,
    _IdleTimer,
    _UnixTimeoutContext,
    _WindowsTimeoutContext,
    make_timeout_wrapper,
)
from oxitest._bridge.result import PassedResult, TimeoutResult
from tests import helpers


def test_run_test_timeout_mark_fires(tmp: TempDir) -> None:
    """@mark.timeout on a slow sync test produces status='timeout' with limit value."""
    result = helpers.exec_inline(
        tmp,
        "import time, oxitest\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "def test_slow():\n"
        "    time.sleep(5)\n",
        "test_slow",
    )
    result = helpers.assert_result(
        result,
        TimeoutResult,
        why="@mark.timeout on a slow test should produce status='timeout'",
    )
    assert "1s" in result.message, (
        f"timeout message should mention the limit '1s', got {result.message!r}"
    )


def test_run_test_timeout_passes_fast_test(tmp: TempDir) -> None:
    """A fast sync test that finishes within the @mark.timeout limit still passes."""
    result = helpers.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.timeout(seconds=5)\n"
        "def test_fast():\n"
        "    pass\n",
        "test_fast",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="the deadline exists to catch overruns -- a mark that also penalised"
        " tests finishing inside it would make declaring one unsafe",
    )


def test_run_test_default_timeout_fires(tmp: TempDir) -> None:
    """The default_timeout parameter enforces a timeout even without @mark.timeout."""
    result = helpers.exec_inline(
        tmp,
        "import time\ndef test_slow():\n    time.sleep(5)\n",
        "test_slow",
        default_timeout=1,
    )
    helpers.assert_result(
        result,
        TimeoutResult,
        why="default_timeout is the only deadline an unmarked test ever gets -- if it"
        " does not fire here, a hung suite has nothing to stop it",
    )


def test_run_test_no_timeout_by_default(tmp: TempDir) -> None:
    """Tests run without timeout when no timeout mark or default_timeout is given."""
    result = helpers.exec_inline(tmp, "def test_ok():\n    pass\n", "test_ok")
    helpers.assert_result(
        result,
        PassedResult,
        why="with neither a mark nor default_timeout there is no deadline -- inventing"
        " one would silently cap every legitimately long test in the suite",
    )


# ── Async timeouts ───────────────────────────────────────────────────────────


def test_async_test_timeout_mark_fires(tmp: TempDir) -> None:
    """@mark.timeout on a slow async test produces status='timeout' with limit value."""
    result = helpers.exec_inline(
        tmp,
        "import asyncio, oxitest\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "async def test_slow():\n"
        "    await asyncio.sleep(10)\n",
        "test_slow",
    )
    result = helpers.assert_result(
        result,
        TimeoutResult,
        why="@mark.timeout on slow async test should produce status='timeout'",
    )
    assert "1s" in result.message, (
        f"timeout message should mention the limit '1s', got {result.message!r}"
    )


def test_async_test_default_timeout_fires(tmp: TempDir) -> None:
    """The default_timeout parameter enforces a timeout on slow async tests too."""
    result = helpers.exec_inline(
        tmp,
        "import asyncio\nasync def test_slow():\n    await asyncio.sleep(10)\n",
        "test_slow",
        default_timeout=1,
    )
    helpers.assert_result(
        result,
        TimeoutResult,
        why="default_timeout=1 should fire on slow async test",
    )


def test_async_test_timeout_passes_fast_test(tmp: TempDir) -> None:
    """A fast async test that finishes within the @mark.timeout limit still passes."""
    result = helpers.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.timeout(seconds=5)\n"
        "async def test_fast():\n"
        "    pass\n",
        "test_fast",
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="the async path measures the deadline against a separate clock, so the"
        " same mark must not mean two things depending on how a test is written",
    )


def test_async_yield_fixture_teardown_runs_on_timeout(tmp: TempDir) -> None:
    """Async yield fixture teardown must run even when test times out."""
    torn_down: list[bool] = []

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        torn_down.append(True)

    session = helpers.make_session_with("val", async_yield_factory)
    result = helpers.exec_inline(
        tmp,
        "import asyncio, oxitest\n"
        "from oxitest import Fixture\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "async def test_slow(val: Fixture[int]) -> None:\n"
        "    await asyncio.sleep(10)\n",
        "test_slow",
        session=session,
    )
    helpers.assert_result(
        result,
        TimeoutResult,
        why="the timeout must actually fire, or the teardown assertion below passes"
        " for the trivial reason that nothing was ever interrupted",
    )
    assert torn_down == [True], (
        f"async yield fixture teardown should run on timeout, got {torn_down!r}"
    )


def test_windows_timeout_state_starts_idle() -> None:
    """Fresh _WindowsTimeoutContext must be in _IdleTimer before __enter__ fires."""
    ctx = _WindowsTimeoutContext(seconds=1)
    assert isinstance(ctx._state, _IdleTimer), (  # noqa: SLF001
        "Fresh Windows timer must be in _IdleTimer state"
        " — no timer scheduled until __enter__"
    )


def test_unix_timeout_state_starts_idle() -> None:
    """Fresh _UnixTimeoutContext must be in _IdleHandler before __enter__ fires."""
    ctx = _UnixTimeoutContext(seconds=1)
    assert isinstance(ctx._state, _IdleHandler), (  # noqa: SLF001
        "Fresh Unix context must be in _IdleHandler state"
        " — no signal handler installed until __enter__"
    )


# ── Which arm is applied to which kind (#1998) ───────────────────────────────
#
# `context_cls` makes both arms reachable from one platform. Without it the
# Windows branch is unexecuted on every job that is not Windows, which is the
# state that let #1998 ship.


def test_async_test_does_not_arm_an_arm_that_cannot_bound_blocking_calls() -> None:
    """An arm that cannot bound blocking calls is not armed for an async test.

    The ctypes arm cannot bound one, so an async test gains nothing from it and
    pays the post-__exit__ injection race (#1998).
    """
    entered: list[str] = []

    class _Probe(_WindowsTimeoutContext):
        def __enter__(self) -> None:
            entered.append("armed")
            super().__enter__()

    wrapper = make_timeout_wrapper(60, is_async=True, context_cls=_Probe)
    result = wrapper(PassedResult)

    assert entered == [], (
        "an arm that cannot bound a blocking call must not be armed for an async"
        " test — asyncio.wait_for already owns everything the loop can see"
    )
    assert isinstance(result, PassedResult), (
        "skipping the arm must not skip the body — the wrapper still has to run"
        " the test and pass its result through"
    )


def test_async_test_keeps_an_arm_that_can_bound_blocking_calls() -> None:
    """An arm that can bound blocking calls stays armed for an async test.

    SIGALRM bounds a blocking call the event loop cannot see, so removing it
    for async tests would regress a measured 1.00s guarantee.
    """
    entered: list[str] = []

    class _Probe(_UnixTimeoutContext):
        # Deliberately does NOT call super().__enter__(): this test asserts the
        # dispatch decision, and entering the real Unix arm would need
        # signal.SIGALRM, which does not exist on Windows. Calling super() here
        # made the Windows job red with AttributeError.
        def __enter__(self) -> None:
            entered.append("armed")

        def __exit__(self, *_: object) -> None:
            return

    wrapper = make_timeout_wrapper(60, is_async=True, context_cls=_Probe)
    wrapper(PassedResult)

    assert entered == ["armed"], (
        "the Unix arm must still be entered for async tests — asyncio.wait_for"
        " cannot bound a coroutine that blocks, so this is the only mechanism"
        " that covers that case"
    )


def test_an_unarmed_async_wrapper_still_reports_a_timeout() -> None:
    """Skipping the OS arm must not skip the OxitestTimeoutError translation.

    `_run_with_timeout` raises OxitestTimeoutError out of `asyncio.wait_for`,
    and this wrapper is the only place that turns it into a TimeoutResult.
    An early-return passthrough dropped that, letting the error escape — which
    is invisible on any platform whose arm keeps the full wrapper, and made
    three async timeout tests hang to the harness limit on Windows.
    """
    wrapper = make_timeout_wrapper(1, is_async=True, context_cls=_WindowsTimeoutContext)

    def _times_out() -> PassedResult:
        raise OxitestTimeoutError

    result = wrapper(_times_out)

    assert isinstance(result, TimeoutResult), (
        f"an unarmed async wrapper must still translate the loop's timeout into"
        f" a TimeoutResult, got {result!r} — otherwise the error escapes and the"
        f" test is reported as an error, or hangs"
    )


def test_sync_test_always_arms_even_when_blocking_calls_are_unbounded() -> None:
    """A sync test always arms, whatever the arm can bound.

    It has no event loop, so the best-effort arm is all there is — the async
    skip must not leak into the sync path.
    """
    entered: list[str] = []

    class _Probe(_WindowsTimeoutContext):
        def __enter__(self) -> None:
            entered.append("armed")
            super().__enter__()

    wrapper = make_timeout_wrapper(60, is_async=False, context_cls=_Probe)
    wrapper(PassedResult)

    assert entered == ["armed"], (
        "a sync test must keep the arm on every platform — dropping it would"
        " leave the sync path with no deadline enforcement at all"
    )


# ── The ambient default reaches the event loop (#1998 AC1) ───────────────────
#
# `test_async_test_default_timeout_fires` cannot cover this on Linux: SIGALRM
# still arms for async tests there, so it passes whether or not the default
# ever reaches asyncio.wait_for. These assert the routing itself, which is the
# only enforcement left on a platform whose arm is skipped for async.


def _plan_with(marks: tuple[MarkInfo, ...]) -> ExecutionPlan:
    """A minimal async ExecutionPlan carrying *marks* and nothing else."""
    return ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=marks,
        no_message_lines=(),
        is_async=True,
    )


def test_ambient_default_becomes_the_effective_deadline() -> None:
    """A global timeout becomes the effective deadline for an unmarked test.

    Without it the async path sees None and asyncio.wait_for is never armed,
    leaving a global --timeout unenforced wherever the OS arm is skipped.
    """
    secs = _effective_timeout_secs(_plan_with(()), TimeoutSet(seconds=7))

    assert secs == 7, (
        f"a global timeout must reach the async path, got {secs!r} — on Windows"
        " this is the only enforcement an async test has left"
    )


def test_a_timeout_mark_wins_over_the_ambient_default() -> None:
    """A per-test mark takes precedence over the ambient default.

    Precedence has to match TimeoutMiddleware, which skips when a mark is
    present — if these disagreed a marked test would get the global value.
    """
    mark = MarkInfo(name="timeout", args=(), kwargs=MappingProxyType({"seconds": 3}))

    secs = _effective_timeout_secs(_plan_with((mark,)), TimeoutSet(seconds=7))

    assert secs == 3, (
        f"the per-test mark must win over the ambient default, got {secs!r}"
    )


def test_no_timeout_anywhere_leaves_the_deadline_unset() -> None:
    """No mark and no ambient timeout means no deadline at all.

    TimeoutOff must not become a deadline — an unmarked test under no global
    timeout has to stay unbounded, or every test acquires a silent limit.
    """
    secs = _effective_timeout_secs(_plan_with(()), TimeoutOff())

    assert secs is None, (
        f"no mark and no ambient timeout must mean no deadline, got {secs!r}"
    )


# ── The injection cannot outlive its region (#1998) ──────────────────────────


def test_injection_after_exit_is_refused() -> None:
    """A timer firing after __exit__ must not inject.

    Timer.cancel() is only `finished.set()` and Timer.run() checks it *before*
    calling the function, so a cancel arriving after that check does nothing.
    This guard is the only thing left that can stop the injection.
    """
    ctx = _WindowsTimeoutContext(seconds=60)
    ctx.__enter__()
    ctx.__exit__()

    injected: list[int] = []

    def _record(thread_id: int, _exc: type[BaseException] | None) -> int:
        injected.append(thread_id)
        return 1

    ctx._inject(threading.get_ident(), _record)  # noqa: SLF001

    assert injected == [], (
        "a timer that fires after __exit__ must not inject — the region it"
        " guarded is over, so the exception would surface on unrelated code"
    )


def test_injection_while_active_still_fires() -> None:
    """A timer firing inside the region must still inject.

    The guard must refuse only *after* __exit__ — refusing while the region is
    live would disable timeouts entirely, and read as a passing test.
    """
    ctx = _WindowsTimeoutContext(seconds=60)
    ctx.__enter__()

    injected: list[int] = []

    def _record(thread_id: int, _exc: type[BaseException] | None) -> int:
        injected.append(thread_id)
        return 1

    try:
        ctx._inject(threading.get_ident(), _record)  # noqa: SLF001
    finally:
        ctx.__exit__()

    assert injected == [threading.get_ident()], (
        "an in-region timer must still inject — this is the assertion that stops"
        " the guard above from being satisfied by never injecting at all"
    )


def test_the_windows_arm_end_to_end_still_times_a_test_out() -> None:
    """The Windows arm, driven for real, still produces a TimeoutResult.

    Every other test here either never enters the context or uses a deadline
    long enough that the timer never fires, so a guard that refused *valid*
    in-region injections would leave all of them green. This one runs the real
    timer thread and the real ctypes injection — it works on any platform, and
    it is the only end-to-end proof that the guard did not disarm the arm it
    was added to protect.
    """
    wrapper = make_timeout_wrapper(1, context_cls=_WindowsTimeoutContext)

    def _busy_loop() -> PassedResult:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pass
        return PassedResult()

    started = time.monotonic()
    result = wrapper(_busy_loop)
    elapsed = time.monotonic() - started

    assert isinstance(result, TimeoutResult), (
        f"the Windows arm must still fire on pure-Python work, got {result!r}"
        " — if this fails the guard is refusing injections inside their own region"
    )
    assert elapsed < 5, (
        f"the injection must arrive near the 1s deadline, not at the body's own"
        f" 10s end; took {elapsed:.2f}s"
    )


def test_a_fired_timer_whose_test_finished_reports_passed() -> None:
    """A completed test reports passed even if its timer already fired.

    __exit__ running means the body returned, so the deadline never bit;
    reporting `timeout` for a completed test would be a lie (#1998 AC4).
    """
    wrapper = make_timeout_wrapper(60, context_cls=_WindowsTimeoutContext)

    result = wrapper(PassedResult)

    assert isinstance(result, PassedResult), (
        "a completed body must not be relabelled as a timeout by a late timer"
    )
