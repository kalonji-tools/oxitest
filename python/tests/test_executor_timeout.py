"""Tests for timeout mark execution during sync and async tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from oxitest import TempDir, helpers


def test_run_test_timeout_mark_fires(tmp: TempDir) -> None:
    """@mark.timeout on a slow sync test produces status='timeout' with limit value."""
    result = helpers.common.exec_inline(
        tmp,
        "import time, oxitest\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "def test_slow():\n"
        "    time.sleep(5)\n",
        "test_slow",
    )
    assert result.status == "timeout", (
        f"@mark.timeout on a slow test should produce status='timeout', got "
        f"{result.status!r}"
    )
    assert "1s" in result.message, (
        f"timeout message should mention the limit '1s', got {result.message!r}"
    )


def test_run_test_timeout_passes_fast_test(tmp: TempDir) -> None:
    """A fast sync test that finishes within the @mark.timeout limit still passes."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.timeout(seconds=5)\n"
        "def test_fast():\n"
        "    pass\n",
        "test_fast",
    )
    assert result.status == "passed", (
        f"fast test under timeout limit should produce status='passed', got "
        f"{result.status!r}"
    )


def test_run_test_default_timeout_fires(tmp: TempDir) -> None:
    """The default_timeout parameter enforces a timeout even without @mark.timeout."""
    result = helpers.common.exec_inline(
        tmp,
        "import time\ndef test_slow():\n    time.sleep(5)\n",
        "test_slow",
        default_timeout=1,
    )
    assert result.status == "timeout", (
        f"default_timeout=1 should fire on a slow test, got status={result.status!r}"
    )


def test_run_test_no_timeout_by_default(tmp: TempDir) -> None:
    """Tests run without timeout when no timeout mark or default_timeout is given."""
    result = helpers.common.exec_inline(tmp, "def test_ok():\n    pass\n", "test_ok")
    assert result.status == "passed", (
        f"test without timeout mark should pass normally, got {result.status!r}"
    )


# ── Async timeouts ───────────────────────────────────────────────────────────


def test_async_test_timeout_mark_fires(tmp: TempDir) -> None:
    """@mark.timeout on a slow async test produces status='timeout' with limit value."""
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio, oxitest\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "async def test_slow():\n"
        "    await asyncio.sleep(10)\n",
        "test_slow",
    )
    assert result.status == "timeout", (
        f"@mark.timeout on slow async test should produce status='timeout', "
        f"got {result.status!r}, msg={result.message!r}"
    )
    assert "1s" in result.message, (
        f"timeout message should mention the limit '1s', got {result.message!r}"
    )


def test_async_test_default_timeout_fires(tmp: TempDir) -> None:
    """The default_timeout parameter enforces a timeout on slow async tests too."""
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio\nasync def test_slow():\n    await asyncio.sleep(10)\n",
        "test_slow",
        default_timeout=1,
    )
    assert result.status == "timeout", (
        f"default_timeout=1 should fire on slow async test, "
        f"got status={result.status!r}, msg={result.message!r}"
    )


def test_async_test_timeout_passes_fast_test(tmp: TempDir) -> None:
    """A fast async test that finishes within the @mark.timeout limit still passes."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.timeout(seconds=5)\n"
        "async def test_fast():\n"
        "    pass\n",
        "test_fast",
    )
    assert result.status == "passed", (
        f"fast async test under timeout should pass, got {result.status!r}"
    )


def test_async_yield_fixture_teardown_runs_on_timeout(tmp: TempDir) -> None:
    """Async yield fixture teardown must run even when test times out."""
    torn_down: list[bool] = []

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        torn_down.append(True)

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio, oxitest\n"
        "from oxitest import Fixture\n"
        "@oxitest.mark.timeout(seconds=1)\n"
        "async def test_slow(val: Fixture[int]) -> None:\n"
        "    await asyncio.sleep(10)\n",
        "test_slow",
        session=session,
    )
    assert result.status == "timeout", (
        f"test should timeout, got status={result.status!r}"
    )
    assert torn_down == [True], (
        f"async yield fixture teardown should run on timeout, got {torn_down!r}"
    )
