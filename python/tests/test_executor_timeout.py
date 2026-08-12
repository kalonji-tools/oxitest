"""Tests for timeout mark execution during sync and async tests."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
import time
from collections.abc import AsyncGenerator
from types import MappingProxyType
from typing import Any

import oxitest as oxi
from oxitest import Fixture, TempDir
from oxitest._bridge._errors import OxitestTimeoutError
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._middleware import (
    ExecutionPlan,
    _async_test_core,
    _effective_timeout_secs,
)
from oxitest._bridge._timeout import (
    TimeoutOff,
    TimeoutSet,
    _IdleHandler,
    _IdleTimer,
    _timeout_context_class,
    _timeout_message,
    _UnixTimeoutContext,
    _WindowsTimeoutContext,
    make_timeout_wrapper,
)
from oxitest._bridge.result import (
    Diagnostic,
    FailedResult,
    PassedResult,
    TestResult,
    TimeoutResult,
    WarnedResult,
)
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


_IS_LINUX = sys.platform == "linux"
_TIMERS_ONLY_SETTLE_ON_LINUX = (
    "which of the two timers governs is decided by the platform. On Windows the OS"
    " arm is skipped for async tests entirely (#1998). On macOS the two race: this"
    " assertion was measured failing on both arm64 and x86_64, with asyncio.wait_for"
    " winning outright, and that race is what makes #2070 macOS-only. Only Linux"
    " settles it deterministically, so only Linux can assert it"
)


@oxi.mark.skip(when=not _IS_LINUX, reason=_TIMERS_ONLY_SETTLE_ON_LINUX)
def test_wait_for_never_governs_a_deadline_on_linux(
    tmp: TempDir, diag_collector: Fixture[list[Diagnostic]]
) -> None:
    """On Linux the OS timer always governs, so `asyncio.wait_for` never fires.

    Both timers are armed at the same value and the OS timer is armed first, so
    its deadline is the earlier one. On Linux that ordering holds every time --
    measured 10 of 10 -- which is why the two never contend there and why #2070
    does not reproduce on Linux at all. It is NOT a Unix-wide invariant: on
    macOS `wait_for` has been measured winning outright, on both architectures.

    Pinning the Linux half still earns its place, because a change that
    re-orders the two -- offsetting the OS arm past the loop's deadline, say --
    would otherwise silently swap which timer enforces the Deadline, and
    thereby which span it bounds (#2082).

    The leaked-task diagnostic is what tells them apart: SIGALRM raises out of
    `run_until_complete` and leaves `wait_for`'s inner task pending, while
    `wait_for` cancels and awaits its own task before raising (#2070).
    """
    # Arrange / Act
    result = helpers.exec_inline(
        tmp,
        "import asyncio\nasync def test_slow():\n    await asyncio.sleep(10)\n",
        "test_slow",
        default_timeout=1,
    )

    # Assert
    helpers.assert_result(
        result,
        TimeoutResult,
        why="the deadline must fire whichever timer governs -- without this the"
        " assertion below could pass on a test that never timed out at all",
    )
    assert any("leaked" in diag.message for diag in diag_collector), (
        f"the OS timer must govern on Linux: it raises out of run_until_complete"
        f" and leaves wait_for's inner task pending, so a 'leaked' diagnostic is"
        f" emitted. Its absence means asyncio.wait_for fired instead, which"
        f" re-scopes the Deadline to the test body alone (#2082) and puts Linux"
        f" into the racing state ADR-0016's fourth known limit records for macOS."
        f" Got {[diag.message for diag in diag_collector]!r}"
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

        def __exit__(self, exc_type: object = None, *_: object) -> None:
            # Signature tracks the base class, which grew `exc_type` in #2001 to
            # tell a fired deadline from a cancelled one. Unread here for the
            # same reason __enter__ does not call super(): this probe records a
            # dispatch decision and never arms a real timer.
            del exc_type

    async def body() -> None:
        pass

    plan = ExecutionPlan(
        fn=body,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )
    asyncio.run(_async_test_core(plan, 60, context_cls=_Probe))

    assert entered == ["armed"], (
        "the arm that bounds blocking calls must still be entered for an async"
        " test — asyncio.wait_for cannot bound a coroutine that blocks, so this"
        " is the only mechanism that covers that case. #2082 moved the arming"
        " out of make_timeout_wrapper and into _async_test_core, so this asserts"
        " the new site; the guarantee itself did not change"
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


# ── Both arms bound the same span: the call (#2082) ──────────────────────────
#
# A Deadline encloses the call of the test function. Fixture setup above it and
# fixture teardown below it are outside, on both arms, which is the span a sync
# Deadline already bounded. Before #2082 the armed arm wrapped the whole of
# `next_fn()`, so it also bounded async fixture setup and teardown while the
# unarmed arm bounded the body alone — one Deadline, two spans, decided by the
# arm. The arming moved into `_async_test_core`, around the body.
#
# Each test below drives one arm through `context_cls`, which is what makes both
# reachable from one platform. The tests that need to *enter* the real Unix
# context guard on `hasattr(signal, "alarm")`, because SIGALRM is absent on
# Windows.

#: Longer than the deadline below, so a bounded setup cannot reach its end.
_SETUP_SECONDS = 2
_SPAN_DEADLINE_SECONDS = 1


def test_an_unarmed_arm_leaves_async_fixture_setup_outside_the_deadline() -> None:
    """Where the OS arm is skipped, async fixture setup has no Deadline at all.

    `asyncio.wait_for` starts after the fixtures are unpacked, so it cannot bound
    them. A fixture that sleeps longer than the whole Deadline still runs to its
    end, and the test then reports **passed** — later than the limit the user
    asked for. On Windows that is every async test (#1998 AC2).

    Since #2082 the armed arm does the same, because the Deadline encloses the
    call alone. `test_an_armed_arm_leaves_async_fixture_setup_outside_the_deadline`
    is the mirror of this test, and the pair is what says the two arms agree.

    The assertion is on the setup reaching its end rather than on elapsed wall
    time: a bound would have cancelled the sleep mid-flight, so the marker is
    absent exactly when the span is covered, and no timing threshold is needed.

    Scope: this drives `_async_test_core` under `asyncio.run`, where production
    drives it under `session.run` (`_middleware.py:341`). The ordering that
    creates the span sits inside `_async_test_core`, so it holds either way —
    but this does not cover the production composition.
    """
    setup_reached_its_end: list[str] = []

    async def slow_setup() -> str:
        await asyncio.sleep(_SETUP_SECONDS)
        setup_reached_its_end.append("finished")
        return "ready"

    async def body(slow: str) -> None:
        assert slow == "ready", f"fixture must resolve before the body, got {slow!r}"

    plan = ExecutionPlan(
        fn=body,
        fn_name="test_x",
        kwargs=MappingProxyType({"slow": slow_setup()}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )
    wrapper = make_timeout_wrapper(
        _SPAN_DEADLINE_SECONDS,
        is_async=True,
        context_cls=_WindowsTimeoutContext,
    )

    result = wrapper(
        lambda: asyncio.run(_async_test_core(plan, _SPAN_DEADLINE_SECONDS))
    )

    assert setup_reached_its_end == ["finished"], (
        f"a {_SETUP_SECONDS}s fixture setup must run to its end under a"
        f" {_SPAN_DEADLINE_SECONDS}s Deadline when the OS arm is skipped, because"
        f" asyncio.wait_for is armed after the fixtures are unpacked and cannot"
        f" reach them — an empty marker means something bounded the setup, so the"
        f" two arms now agree and this issue's premise is gone"
    )
    assert isinstance(result, PassedResult), (
        f"the unarmed arm must report a pass, got {result!r} — the body finished"
        f" inside its own {_SPAN_DEADLINE_SECONDS}s window, and only the unbounded"
        f" setup pushed the run past the Deadline. A TimeoutResult here means the"
        f" arm bounded the setup after all"
    )


def test_an_armed_arm_leaves_async_fixture_setup_outside_the_deadline() -> None:
    """The arm that bounds blocking calls leaves fixture setup outside too.

    The mirror of the test above, and the half that changed in #2082. Before it,
    this arm enclosed the whole call and cut a slow setup at the Deadline; the
    interrupt landed inside the fixture body, where the setup handler caught it
    first and reported `Error in fixture 'dep': ` with an empty cause. The user
    read that their fixture raised, and never read what raised it.

    A blocking sleep is used rather than an awaiting one, because the blocking
    case is the one this arm could always reach.
    """
    if not hasattr(signal, "alarm"):
        return

    # Arrange
    setup_reached_its_end: list[str] = []

    async def slow_setup() -> str:
        # A blocking setup is the case under test. The awaiting
        # form reported `timeout` before #2082; only the blocking form produced
        # the mis-attributed `Error in fixture 'dep': ` this test pins.
        time.sleep(_SETUP_SECONDS)  # noqa: ASYNC251
        setup_reached_its_end.append("finished")
        return "ready"

    async def body(slow: str) -> None:
        if slow != "ready":
            raise AssertionError(slow)

    plan = ExecutionPlan(
        fn=body,
        fn_name="test_x",
        kwargs=MappingProxyType({"slow": slow_setup()}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )
    wrapper = make_timeout_wrapper(
        _SPAN_DEADLINE_SECONDS, is_async=True, context_cls=_UnixTimeoutContext
    )

    # Act
    result = wrapper(
        lambda: asyncio.run(
            _async_test_core(
                plan, _SPAN_DEADLINE_SECONDS, context_cls=_UnixTimeoutContext
            )
        )
    )

    # Assert
    assert setup_reached_its_end == ["finished"], (
        f"a {_SETUP_SECONDS}s fixture setup must reach its end under a"
        f" {_SPAN_DEADLINE_SECONDS}s Deadline on this arm too, because a Deadline"
        f" bounds the call and not the setup. An empty marker means the arm"
        f" bounded the setup again, which is the state that reported a Deadline"
        f" as a failure of the user's fixture"
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="the body finished inside its own window and only the unbounded setup"
        " ran long, so the outcome is a pass — an ErrorResult here means the"
        " Deadline fired inside the fixture again",
    )


def test_a_slow_async_teardown_is_not_cut_by_the_deadline() -> None:
    """Fixture teardown runs outside the Deadline, so cleanup always completes.

    Before #2082 the Deadline enclosed teardown. A teardown that blocked past the
    limit was stopped part way, the teardown error handler absorbed the
    interrupt, and the test reported **passed** — measured stopping at 1.00s
    against a 3s teardown. The fixture never released what it took, and nothing
    in the report said so.
    """
    if not hasattr(signal, "alarm"):
        return

    # Arrange
    teardown_reached_its_end: list[str] = []

    async def gen() -> AsyncGenerator[str]:
        yield "ready"
        # A blocking teardown is the case under test. The
        # awaiting form reported `timeout`; only the blocking form produced the
        # silent `passed` with cleanup stopped part way that this test pins.
        time.sleep(_SETUP_SECONDS)  # noqa: ASYNC251
        teardown_reached_its_end.append("finished")

    async def body(dep: str) -> None:
        if dep != "ready":
            raise AssertionError(dep)

    plan = ExecutionPlan(
        fn=body,
        fn_name="test_x",
        kwargs=MappingProxyType({"dep": gen()}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )
    wrapper = make_timeout_wrapper(
        _SPAN_DEADLINE_SECONDS, is_async=True, context_cls=_UnixTimeoutContext
    )

    # Act
    result = wrapper(
        lambda: asyncio.run(
            _async_test_core(
                plan, _SPAN_DEADLINE_SECONDS, context_cls=_UnixTimeoutContext
            )
        )
    )

    # Assert
    assert teardown_reached_its_end == ["finished"], (
        f"a {_SETUP_SECONDS}s teardown must reach its end under a"
        f" {_SPAN_DEADLINE_SECONDS}s Deadline. A fixture stopped part way never"
        f" releases what it took, and the test still reports passed, so this"
        f" marker is what keeps cleanup outside the Deadline"
    )
    helpers.assert_result(
        result,
        PassedResult,
        why="the body finished inside its own window, so the outcome is a pass",
    )


def test_an_arm_that_cannot_bound_blocking_calls_is_not_armed_for_async() -> None:
    """Only the arm that bounds blocking calls is armed; the other one is not.

    Two timers on one test is what ADR-0016's fourth known limit needs to
    happen, so #2082 arms exactly one. Where the arm cannot bound a blocking
    call it buys an outcome name and no bound — measured, a 1s Deadline
    reporting a timeout 3.02s late — so `asyncio.wait_for` is left as the only
    enforcement and this context is never entered.

    Written because a mutant survived: dropping `not cls.bounds_blocking_calls`
    from the guard in `_async_test_core` left the whole suite green. Nothing
    else asserts this half of the dispatch.
    """
    # Arrange
    entered: list[str] = []

    class _Probe(_WindowsTimeoutContext):
        def __enter__(self) -> None:
            entered.append("armed")

        def __exit__(self, exc_type: object = None, *_: object) -> None:
            del exc_type

    async def body() -> None:
        pass

    plan = ExecutionPlan(
        fn=body,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )

    # Act
    asyncio.run(_async_test_core(plan, 60, context_cls=_Probe))

    # Assert
    assert entered == [], (
        "an arm that cannot bound a blocking call must not be armed for an async"
        " test. Arming it adds a second timer holding the same value as"
        " asyncio.wait_for, which is the precondition for the deadline loss"
        f" ADR-0016 records as its fourth known limit. Got {entered!r}"
    )


def test_the_alarm_capability_decides_which_arm_the_platform_gets() -> None:
    """Windows selects the arm that is skipped for async tests; Unix does not.

    This is what makes the test above mean something on Windows: without it, that
    test proves a property of a class nobody has shown Windows selects. Dispatch
    is on `hasattr(signal, "alarm")` (`_timeout.py:332`), so the expectation here
    is keyed on `sys.platform` instead — an independent oracle, rather than a
    restatement of the branch under test.
    """
    on_windows = sys.platform == "win32"
    expected = _WindowsTimeoutContext if on_windows else _UnixTimeoutContext

    selected = _timeout_context_class()

    assert selected is expected, (
        f"{sys.platform} must select {expected.__name__}, got {selected.__name__}"
        f" — the arm decides whether an async test's fixture setup is bounded, so"
        f" selecting the wrong one silently changes what a Deadline covers"
    )
    assert selected.bounds_blocking_calls is not on_windows, (
        f"{selected.__name__} must report bounds_blocking_calls="
        f"{not on_windows} — `arm` is computed from it (`_timeout.py:396`), so"
        f" this capability is what skips the OS arm for async tests on Windows"
    )


# ── The ambient default reaches the event loop (#1998 AC1) ───────────────────
#
# `test_async_test_default_timeout_fires` cannot cover this on Linux: SIGALRM
# still arms for async tests there, so it passes whether or not the default
# ever reaches asyncio.wait_for. These assert the routing itself, which is the
# only enforcement left on a platform whose arm is skipped for async.
#
# That same test and `test_async_marked_timeout_fires` are the two that can go
# red on macOS arm64 for a reason that is not a regression: ADR-0016's fourth
# known limit (#2070). Read it before debugging your own branch — it names the
# `leaked N task(s)` marker that says which timer governed the run.


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


# ── #2001: the deadline is unowned ───────────────────────────────────────────
#
# `_UnixTimeoutContext` shares one process-global ITIMER_REAL slot with every
# other writer in the process. These pin that it saves what it found, caps
# itself to the enclosing deadline, and restores on the way out.


def test_nested_unix_context_restores_the_enclosing_deadline() -> None:
    """A nested context must hand the enclosing deadline back, not zero it.

    Before #2001 the inner ``__exit__`` called ``signal.alarm(0)``, so the
    enclosing test finished with no deadline at all.
    """
    if not hasattr(signal, "alarm"):
        return
    outer = _UnixTimeoutContext(seconds=30)
    outer.__enter__()
    try:
        inner = _UnixTimeoutContext(seconds=1)
        inner.__enter__()
        inner.__exit__(None, None, None)
        remaining = signal.getitimer(signal.ITIMER_REAL)[0]
        assert remaining > 25, (
            "the enclosing 30s deadline must survive an inner context;"
            f" the slot holds {remaining}s, so the enclosing test would run unguarded"
        )
    finally:
        outer.__exit__(None, None, None)


def test_nested_unix_context_caps_at_the_enclosing_remaining() -> None:
    """An inner deadline longer than the enclosing one must not extend it.

    Arming the inner value as asked would silently grant the enclosing test more
    time than its own deadline allows.
    """
    if not hasattr(signal, "alarm"):
        return
    outer = _UnixTimeoutContext(seconds=5)
    outer.__enter__()
    try:
        inner = _UnixTimeoutContext(seconds=60)
        inner.__enter__()
        try:
            armed = signal.getitimer(signal.ITIMER_REAL)[0]
            assert armed <= 5, (
                "an inner 60s deadline inside a 5s enclosing deadline must cap at 5s;"
                f" the slot holds {armed}s, which breaks the enclosing promise"
            )
            assert inner.effective_seconds <= 5, (
                "the context must report the value it armed, because the wrapper"
                " reads it to build the timeout message"
            )
        finally:
            inner.__exit__(None, None, None)
    finally:
        outer.__exit__(None, None, None)


def test_unaffected_unix_context_arms_what_it_was_asked() -> None:
    """With no enclosing deadline nothing is capped -- the common case."""
    if not hasattr(signal, "alarm"):
        return
    ctx = _UnixTimeoutContext(seconds=7)
    ctx.__enter__()
    try:
        assert ctx.effective_seconds == 7, (
            "a context with no enclosing deadline must arm exactly what it was asked,"
            " or every ordinary test would report the wrong limit"
        )
    finally:
        ctx.__exit__(None, None, None)


def test_timeout_message_names_the_armed_deadline_when_capped() -> None:
    """A capped deadline must report the limit it enforced, not the one asked for.

    ``make_timeout_wrapper`` closed over the requested seconds, so an inner 60s
    deadline capped to 1s fired at 1s and reported 60s -- a number that was
    never armed, which sends the developer debugging against the wrong limit.
    """
    uncapped = _timeout_message(60.0, 60)
    assert uncapped == "Timed out after 60s", (
        "the uncapped message must stay byte-identical to the pre-#2001 form,"
        f" because every existing report and test reads it; got {uncapped!r}"
    )
    capped = _timeout_message(1.0, 60)
    assert "60s" in capped and "1s" in capped, (
        "a capped message must name both numbers -- the limit enforced so the"
        " developer knows what bit, and the limit requested so the cap is"
        f" visible rather than looking like a typo; got {capped!r}"
    )


def test_a_capped_deadline_is_the_one_enforced() -> None:
    """A nested 60s deadline inside a 1s one is cut at ~1s, not at 60s.

    Timing is the assertion, deliberately. Whether the *report* comes back from
    the nested wrapper is platform-dependent: a capped deadline can only fire
    when the enclosing one is spent, so the restore re-arms a timer that fires
    within microseconds, and where that lands decides which frame catches it.
    On Linux the nested wrapper reported it 12/12; on macOS x86_64 it escaped
    to the enclosing wrapper and the nested result was never produced. Both are
    the cap working. Asserting that a result exists tested the race, not the
    cap, and it failed in CI on the platform this bug was reported from.

    ADR-0016 records the attribution limit. The message content is pinned by
    `test_timeout_message_names_the_armed_deadline_when_capped`, which is a
    unit test and cannot race.
    """
    if not hasattr(signal, "alarm"):
        return
    inner_wrapper = make_timeout_wrapper(60, context_cls=_UnixTimeoutContext)
    outer_wrapper = make_timeout_wrapper(1, context_cls=_UnixTimeoutContext)

    def slow_body() -> PassedResult:
        time.sleep(5)
        return PassedResult()

    def enclosing_body() -> PassedResult:
        inner_wrapper(slow_body)
        return PassedResult()

    started = time.monotonic()
    outer_wrapper(enclosing_body)
    elapsed = time.monotonic() - started

    assert elapsed < 3, (
        "the nested 60s deadline must be capped at the enclosing 1s remaining;"
        f" the 5s body ran {elapsed:.2f}s, and anything near 5s means the nested"
        " deadline was armed as asked and the enclosing promise was extended"
    )


def test_a_cancelled_slot_is_detected() -> None:
    """Code that cancels the timer voids the deadline, and that must be visible."""
    if not hasattr(signal, "alarm"):
        return
    ctx = _UnixTimeoutContext(seconds=5)
    ctx.__enter__()
    signal.setitimer(signal.ITIMER_REAL, 0)
    ctx.__exit__(None, None, None)
    assert ctx.deadline_taken, (
        "a cancelled timer means the test ran with no deadline; without detection"
        " it passes silently and the pass is unearned"
    )


def test_a_fired_deadline_is_not_reported_as_taken() -> None:
    """A fired deadline leaves the slot at 0 too -- the two must not be confused.

    This is why __exit__ reads the in-flight exception instead of deciding from
    arithmetic: a deadline that fired and one that was cancelled are identical
    at the slot.
    """
    if not hasattr(signal, "alarm"):
        return
    ctx = _UnixTimeoutContext(seconds=1)
    try:
        with ctx:
            time.sleep(3)
    except OxitestTimeoutError:
        pass
    assert not ctx.deadline_taken, (
        "a fired deadline is the timeout working, not interference; reporting it"
        " as taken would attach a warning to every genuine timeout"
    )


def test_an_undisturbed_context_is_not_reported_as_taken() -> None:
    """The ordinary case must stay clean, including close to the deadline."""
    if not hasattr(signal, "alarm"):
        return
    ctx = _UnixTimeoutContext(seconds=2)
    with ctx:
        time.sleep(1.8)
    assert not ctx.deadline_taken, (
        "finishing just inside the deadline must stay clean, or every slow test"
        " near its limit would be reported as interfered with"
    )


def test_a_taken_deadline_downgrades_a_pass_to_warned() -> None:
    """A test that ran unguarded must not report a clean pass.

    oxitest did not observe a failure. It observed that it could not observe,
    and that is what the report has to say (#2001).
    """
    if not hasattr(signal, "alarm"):
        return
    wrapper = make_timeout_wrapper(5, context_cls=_UnixTimeoutContext)

    def body_that_cancels_the_timer() -> PassedResult:
        signal.setitimer(signal.ITIMER_REAL, 0)
        return PassedResult()

    result = wrapper(body_that_cancels_the_timer)
    helpers.assert_result(
        result,
        WarnedResult,
        why="a pass earned without a live deadline is not the same claim as a pass,"
        " and only a result variant attributes that to the test in default output",
    )


def test_an_async_taken_deadline_downgrades_a_pass_to_warned() -> None:
    """The async path reports a voided deadline, exactly as the sync path does.

    ADR-0016 decision 4 rewrites a pass into ``warned`` when other code wrote
    the process timer. The sync path is covered by
    ``test_a_taken_deadline_downgrades_a_pass_to_warned``; the async path
    reached that same code only because the wrapper armed the context for both
    kinds. #2082 moves the async context inside the coroutine, and this test is
    what refuses to let the rewrite be left behind.
    """
    if not hasattr(signal, "alarm"):
        return

    # Arrange
    async def body_that_cancels_the_timer() -> None:
        signal.setitimer(signal.ITIMER_REAL, 0)

    plan = ExecutionPlan(
        fn=body_that_cancels_the_timer,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=True,
    )
    wrapper = make_timeout_wrapper(5, is_async=True, context_cls=_UnixTimeoutContext)

    # Act
    result = wrapper(
        lambda: asyncio.run(_async_test_core(plan, 5, context_cls=_UnixTimeoutContext))
    )

    # Assert
    helpers.assert_result(
        result,
        WarnedResult,
        why="an async test that clears the process timer did not run under the"
        " deadline it declared, and reporting it as a plain pass claims a"
        " guarantee oxitest did not deliver",
    )


def test_a_taken_deadline_does_not_rewrite_a_failure() -> None:
    """A test that failed on its own keeps its failure -- the deadline is moot."""
    if not hasattr(signal, "alarm"):
        return
    wrapper = make_timeout_wrapper(5, context_cls=_UnixTimeoutContext)

    def failing_body_that_cancels_the_timer() -> FailedResult:
        signal.setitimer(signal.ITIMER_REAL, 0)
        return FailedResult(message="boom")

    result = wrapper(failing_body_that_cancels_the_timer)
    helpers.assert_result(
        result,
        FailedResult,
        why="the deadline is irrelevant to a test that failed on its own; rewriting"
        " it to warned would hide a real failure behind a warning",
    )


def test_both_arms_enforce_the_shorter_of_two_live_deadlines() -> None:
    """The effective deadline is the minimum of the live deadlines, on both arms.

    The Unix arm reaches this by capping to one timer; the Windows arm by two
    independent timers where the first to fire wins. Pinning both means a later
    change cannot make the platforms disagree without a test saying so.

    Timing is the assertion. Whether the nested wrapper returns a result is
    platform-dependent -- see `test_a_capped_deadline_is_the_one_enforced` --
    and asserting that it does tests the race rather than the invariant. It
    failed on macOS arm64 for exactly that reason.

    WARNING: `context_cls` proves which arm is DISPATCHED, not that the arm
    delivers on its own OS. Passing here on Linux is not coverage for Windows.
    """
    for context_cls in (_UnixTimeoutContext, _WindowsTimeoutContext):
        if context_cls is _UnixTimeoutContext and not hasattr(signal, "alarm"):
            continue
        inner_results: list[TestResult] = []
        inner_wrapper = make_timeout_wrapper(60, context_cls=context_cls)
        outer_wrapper = make_timeout_wrapper(1, context_cls=context_cls)

        def slow_body() -> PassedResult:
            # A spin loop, not time.sleep: the ctypes arm fires only at a Python
            # bytecode boundary, so a blocking C call is not bounded there
            # (bounds_blocking_calls = False). Using sleep here would measure
            # that documented difference instead of the min invariant, and the
            # Windows arm would take the full 5s.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                pass
            return PassedResult()

        def enclosing_body(
            wrap: Any = inner_wrapper, sink: list[TestResult] = inner_results
        ) -> PassedResult:
            # Bound as defaults so each loop iteration captures its own wrapper.
            sink.append(wrap(slow_body))
            return PassedResult()

        started = time.monotonic()
        outer_wrapper(enclosing_body)
        elapsed = time.monotonic() - started

        assert elapsed < 3, (
            f"{context_cls.__name__} took {elapsed:.2f}s; the 1s enclosing deadline"
            " must cut the 5s body, so anything near 5s means the shorter deadline"
            " was not the one enforced"
        )
        if inner_results:
            # Only when the nested frame caught it. Where the enclosing frame
            # caught it instead, `elapsed` above is the whole proof, and it is
            # the same proof either way -- the body was cut at ~1s, not ~60s.
            helpers.assert_result(
                inner_results[0],
                TimeoutResult,
                why=f"{context_cls.__name__} must report the enclosing 1s deadline as"
                " a timeout, or nesting silently extends a running deadline",
            )


def test_a_cleared_slot_is_detected_when_the_test_overruns() -> None:
    """The overrunning test is the case detection exists for, and the hard one.

    A test that clears its deadline and then finishes early leaves an obvious
    gap between the slot and the prediction. A test that clears it and then
    *overruns* does not: the prediction, if clamped at zero, reads exactly like
    the cleared slot. That clamp made the only case that matters invisible, and
    an end-to-end run is what exposed it.
    """
    if not hasattr(signal, "alarm"):
        return
    ctx = _UnixTimeoutContext(seconds=1)
    ctx.__enter__()
    signal.setitimer(signal.ITIMER_REAL, 0)
    time.sleep(1.5)
    ctx.__exit__(None, None, None)
    assert ctx.deadline_taken, (
        "a test that cleared its deadline and then ran past it must be detected;"
        " this is the shape of the real defect, where the overrun is the harm"
    )


def test_a_periodic_enclosing_timer_keeps_its_interval() -> None:
    """``getitimer`` returns two values, and both belong to the enclosing timer.

    Restoring only the time left turns a repeating ITIMER_REAL into a one-shot.
    The module docstring and ADR-0016 both say the arm restores what it found,
    so restoring half of it would make those statements wider than the code.
    """
    if not hasattr(signal, "alarm"):
        return
    # This test writes the process timer, which is exactly what voids a
    # Deadline. Its own Deadline -- the suite's ambient one -- is therefore
    # borrowed, and it has to be handed back, or oxitest correctly reports this
    # test as `warned` for doing what the feature exists to detect.
    borrowed_value, borrowed_interval = signal.getitimer(signal.ITIMER_REAL)
    try:
        signal.setitimer(signal.ITIMER_REAL, 30.0, 5.0)
        with _UnixTimeoutContext(seconds=1):
            pass
        _, interval = signal.getitimer(signal.ITIMER_REAL)
        assert interval == 5.0, (
            "the enclosing timer repeats every 5s; a context that hands back only"
            f" the time left leaves it a one-shot. Interval is now {interval}"
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, borrowed_value, borrowed_interval)
