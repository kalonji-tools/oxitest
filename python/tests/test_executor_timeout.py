"""Tests for timeout mark execution during sync and async tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from oxitest import TempDir
from oxitest._bridge._timeout import (
    _IdleHandler,
    _IdleTimer,
    _UnixTimeoutContext,
    _WindowsTimeoutContext,
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
