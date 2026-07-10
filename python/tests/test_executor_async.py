"""Tests for async test execution: async fixtures, yield teardown, shared sessions."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from oxitest import (
    Fixture,
    FixtureTeardownWarning,
    TempDir,
    WarnCapture,
    helpers,
)
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession


def test_async_test_passes(tmp: TempDir) -> None:
    """A passing async test is run via asyncio.run() and produces status='passed'."""
    result = helpers.common.exec_inline(
        tmp, "async def test_ok():\n    assert 1 == 1\n", "test_ok"
    )
    assert result.status == "passed", (
        f"passing async test should have status='passed', got {result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_test_fails(tmp: TempDir) -> None:
    """A failing assertion inside an async test produces status='failed'."""
    result = helpers.common.exec_inline(
        tmp, 'async def test_bad():\n    assert 1 == 2, "nope"\n', "test_bad"
    )
    assert result.status == "failed", (
        f"failing async test should have status='failed', got {result.status!r}"
    )
    assert "nope" in result.message, (
        f"failure message should contain 'nope', got {result.message!r}"
    )


def test_async_test_error(tmp: TempDir) -> None:
    """An uncaught exception inside an async test produces status='error'."""
    result = helpers.common.exec_inline(
        tmp, "async def test_err():\n    raise ValueError('boom')\n", "test_err"
    )
    assert result.status == "error", (
        f"async error should produce status='error', got {result.status!r}"
    )
    assert "ValueError" in result.message, (
        f"error message should contain 'ValueError', got {result.message!r}"
    )
    assert "boom" in result.message, (
        f"error message should contain 'boom', got {result.message!r}"
    )


def test_async_test_warning(tmp: TempDir) -> None:
    """A warning emitted inside an async test produces status='warned'."""
    result = helpers.common.exec_inline(
        tmp,
        "import warnings\n"
        "async def test_warn():\n"
        "    warnings.warn('old api', DeprecationWarning)\n"
        "    assert 1 == 1\n",
        "test_warn",
    )
    assert result.status == "warned", (
        f"async test with warning should produce status='warned', got {result.status!r}"
    )
    assert "DeprecationWarning" in result.message, (
        f"warned message should mention 'DeprecationWarning', got {result.message!r}"
    )


def test_async_test_skip(tmp: TempDir) -> None:
    """@mark.skip on an async test produces status='skipped' with the skip reason."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "async def test_skip():\n"
        "    pass\n",
        "test_skip",
    )
    assert result.status == "skipped", (
        f"skipped async test should have status='skipped', got {result.status!r}"
    )
    assert "not ready" in result.message, (
        f"skip message should contain 'not ready', got {result.message!r}"
    )


def test_async_test_xfail(tmp: TempDir) -> None:
    """An expected-to-fail async test that fails produces status='xfailed'."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.xfail(reason='known bug')\n"
        "async def test_xfail():\n"
        "    assert 1 == 2\n",
        "test_xfail",
    )
    assert result.status == "xfailed", (
        f"xfail async test should have status='xfailed', got {result.status!r}"
    )


def test_async_test_xpass(tmp: TempDir) -> None:
    """An expected-to-fail async test that passes produces status='xpassed'."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.xfail(reason='expected to fail')\n"
        "async def test_xpass():\n"
        "    assert 1 == 1\n",
        "test_xpass",
    )
    assert result.status == "xpassed", (
        f"xpass async test should have status='xpassed', got {result.status!r}"
    )


def test_async_test_with_async_fixture(tmp: TempDir) -> None:
    """An async fixture is awaited correctly when injected into an async test."""

    async def async_factory() -> int:
        return 99

    session = helpers.common.make_session_with("val", async_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 99\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "passed", (
        f"async test with async fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_test_with_sync_fixture(tmp: TempDir) -> None:
    """A sync fixture can be injected into an async test without errors."""
    session = helpers.common.make_session_with("val", lambda: 42)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "passed", (
        f"async test with sync fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_fixture_setup_error(tmp: TempDir) -> None:
    """An async fixture factory that raises propagates as status='error'."""

    async def bad_factory() -> None:
        msg = "db is down"
        raise RuntimeError(msg)

    session = helpers.common.make_session_with("bad", bad_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_bad(bad: Fixture[None]) -> None:\n"
        "    pass\n",
        "test_uses_bad",
        session=session,
    )
    assert result.status == "error", (
        f"async fixture setup error should produce status='error', "
        f"got {result.status!r}"
    )
    assert "db is down" in result.message, (
        f"error message should contain 'db is down', got {result.message!r}"
    )


def test_sync_test_with_async_fixture_produces_error(tmp: TempDir) -> None:
    """Using an async fixture in a sync test produces status='error' naming it."""

    async def async_factory() -> int:
        return 99

    session = helpers.common.make_session_with("val", async_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 99\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "error", (
        f"sync test with async fixture should produce error, got {result.status!r}, "
        f"msg={result.message!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"error message should mention 'async fixture', got {result.message!r}"
    )
    assert "val" in result.message, (
        f"error message should mention fixture name 'val', got {result.message!r}"
    )


# ── Async yield fixtures ─────────────────────────────────────────────────────


def test_async_yield_fixture_provides_value(tmp: TempDir) -> None:
    """An async yield fixture provides the yielded value to the async test."""

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "passed", (
        f"async yield fixture should provide value, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_async_yield_fixture_teardown_runs(tmp: TempDir) -> None:
    """Teardown code after yield must execute even when test passes."""
    log: list[str] = []

    async def async_yield_factory() -> AsyncGenerator[list[str], None]:
        log.append("setup")
        yield log
        log.append("teardown")

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_ok(val: Fixture[list]) -> None:\n"
        "    val.append('test_ran')\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"expected passed, got status={result.status!r}, msg={result.message!r}"
    )
    assert log == ["setup", "test_ran", "teardown"], (
        f"expected setup->test->teardown order, got {log!r}"
    )


def test_async_yield_fixture_teardown_runs_on_failure(tmp: TempDir) -> None:
    """Teardown must run even when the test fails."""
    torn_down: list[bool] = []

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        torn_down.append(True)

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_fail(val: Fixture[int]) -> None:\n"
        '    assert val == 0, "not zero"\n',
        "test_fail",
        session=session,
    )
    assert result.status == "failed", f"test should fail, got status={result.status!r}"
    assert torn_down == [True], (
        f"async yield fixture teardown should run on test failure, got {torn_down!r}"
    )


def test_async_yield_fixture_teardown_runs_on_error(tmp: TempDir) -> None:
    """Teardown must run even when the test errors."""
    torn_down: list[bool] = []

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        torn_down.append(True)

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_err(val: Fixture[int]) -> None:\n"
        "    raise ValueError('boom')\n",
        "test_err",
        session=session,
    )
    assert result.status == "error", f"test should error, got status={result.status!r}"
    assert torn_down == [True], (
        f"async yield fixture teardown should run on test error, got {torn_down!r}"
    )


def test_async_yield_fixture_teardown_reverse_order(tmp: TempDir) -> None:
    """Multiple async yield fixtures tear down in reverse order."""
    log: list[str] = []

    async def factory_a() -> AsyncGenerator[str, None]:
        log.append("setup_a")
        yield "A"
        log.append("teardown_a")

    async def factory_b() -> AsyncGenerator[str, None]:
        log.append("setup_b")
        yield "B"
        log.append("teardown_b")

    reg = FixtureRegistry()
    reg.register(helpers.common.make_fixture_def("a", factory_a, conftest_path="/c.py"))
    reg.register(helpers.common.make_fixture_def("b", factory_b, conftest_path="/c.py"))
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_ok(a: Fixture[str], b: Fixture[str]) -> None:\n"
        "    assert a == 'A'\n"
        "    assert b == 'B'\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"expected passed, got status={result.status!r}, msg={result.message!r}"
    )
    assert log[:2] == ["setup_a", "setup_b"], f"setup should be in order, got {log!r}"
    assert log[2:] == ["teardown_b", "teardown_a"], (
        f"teardown should be in reverse order, got {log!r}"
    )


def test_async_yield_fixture_teardown_error_warns(
    tmp: TempDir, warn: WarnCapture
) -> None:
    """Teardown exception should warn, not crash."""

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        msg = "teardown exploded"
        raise RuntimeError(msg)

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_ok(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n",
        "test_ok",
        session=session,
    )
    assert result.status == "passed", (
        f"teardown error should not affect test result, got status={result.status!r}, "
        f"msg={result.message!r}"
    )
    assert any(issubclass(w.category, FixtureTeardownWarning) for w in warn.warnings), (
        f"expected a FixtureTeardownWarning, got {warn.warnings!r}"
    )


def test_async_yield_fixture_setup_error(tmp: TempDir) -> None:
    """Error during async yield fixture setup should produce error result."""

    async def bad_factory() -> AsyncGenerator[None, None]:
        msg = "setup failed"
        raise RuntimeError(msg)
        yield

    session = helpers.common.make_session_with("bad", bad_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_bad(bad: Fixture[None]) -> None:\n"
        "    pass\n",
        "test_uses_bad",
        session=session,
    )
    assert result.status == "error", (
        f"async yield fixture setup error should produce error, got {result.status!r}"
    )
    assert "setup failed" in result.message, (
        f"error message should contain 'setup failed', got {result.message!r}"
    )


def test_sync_test_with_async_yield_fixture_produces_error(tmp: TempDir) -> None:
    """Using an async yield fixture in a sync test produces status='error'."""

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42

    session = helpers.common.make_session_with("val", async_yield_factory)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "def test_uses_val(val: Fixture[int]) -> None:\n"
        "    assert val == 42\n",
        "test_uses_val",
        session=session,
    )
    assert result.status == "error", (
        f"sync test with async yield fixture should produce error, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"error message should mention 'async fixture', got {result.message!r}"
    )
    assert "val" in result.message, (
        f"error message should mention fixture name 'val', got {result.message!r}"
    )


# ── Shared async fixtures ────────────────────────────────────────────────────


def test_shared_async_fixture_provides_value(tmp: TempDir) -> None:
    """A shared async fixture is resolved and its value injected into the async test."""

    async def async_pool_factory() -> int:
        return 99

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "pool",
            async_pool_factory,
            conftest_path="/c.py",
            shared=True,
            is_async=True,
        )
    )
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_pool(pool: Fixture[int]) -> None:\n"
        "    assert pool == 99\n",
        "test_uses_pool",
        session=session,
    )
    assert result.status == "passed", (
        f"shared async fixture should provide value, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_shared_async_fixture_cached_across_tests(tmp: TempDir) -> None:
    """A shared async fixture factory is called once and cached across tests."""
    f = tmp / "test_shared_cached.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "async def test_a(pool: Fixture[int]) -> None:\n"
        "    assert pool == 1\n"
        "async def test_b(pool: Fixture[int]) -> None:\n"
        "    assert pool == 1\n"
    )
    call_count = 0

    async def async_pool_factory() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "pool",
            async_pool_factory,
            conftest_path="/c.py",
            shared=True,
            is_async=True,
        )
    )
    session = FixtureSession(reg)
    r1 = helpers.common.run_test(str(f), "test_a", session)
    r2 = helpers.common.run_test(str(f), "test_b", session)
    assert r1.status == "passed", f"test_a: {r1.status!r}, {r1.message!r}"
    assert r2.status == "passed", f"test_b: {r2.status!r}, {r2.message!r}"
    assert call_count == 1, (
        f"shared fixture factory should be called exactly once, got {call_count}"
    )


def test_shared_async_stray_task_cleanup(tmp: TempDir, warn: WarnCapture) -> None:
    """Stray tasks from one test should not affect the next test."""
    f = tmp / "test_shared_stray.py"
    f.write_text(
        "import asyncio\n"
        "from oxitest import Fixture\n"
        "async def test_leaker(pool: Fixture[int]) -> None:\n"
        "    asyncio.get_event_loop().create_task(asyncio.sleep(999))\n"
        "async def test_clean(pool: Fixture[int]) -> None:\n"
        "    assert pool == 42\n"
    )

    async def async_pool_factory() -> int:
        return 42

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "pool",
            async_pool_factory,
            conftest_path="/c.py",
            shared=True,
            is_async=True,
        )
    )
    session = FixtureSession(reg)
    r1 = helpers.common.run_test(str(f), "test_leaker", session)
    r2 = helpers.common.run_test(str(f), "test_clean", session)
    assert r1.status == "passed", f"test_leaker: {r1.status!r}, {r1.message!r}"
    assert r2.status == "passed", f"test_clean: {r2.status!r}, {r2.message!r}"
    leaked_warns = [w for w in warn.warnings if "leaked" in str(w.message).lower()]
    assert len(leaked_warns) >= 1, (
        f"expected leaked task warning, got {[str(w.message) for w in warn.warnings]}"
    )
    session.end_session()


def test_shared_async_yield_fixture_teardown_at_session_end(tmp: TempDir) -> None:
    """Shared async yield fixture teardown runs when end_session is called."""
    f = tmp / "test_shared_td.py"
    f.write_text(
        "from oxitest import Fixture\n"
        "async def test_ok(pool: Fixture[int]) -> None:\n"
        "    assert pool == 42\n"
    )
    log: list[str] = []

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        log.append("setup")
        yield 42
        log.append("teardown")

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "pool",
            async_yield_factory,
            conftest_path="/c.py",
            shared=True,
            is_async=True,
        )
    )
    session = FixtureSession(reg)
    result = helpers.common.run_test(str(f), "test_ok", session)
    assert result.status == "passed", (
        f"expected passed, got status={result.status!r}, msg={result.message!r}"
    )
    assert log == ["setup"], f"only setup should have run, got {log!r}"

    session.end_session()
    assert log == ["setup", "teardown"], (
        f"teardown should run at end_session, got {log!r}"
    )


def test_non_shared_async_test_gets_own_loop(tmp: TempDir) -> None:
    """Async test without shared fixtures uses asyncio.run() even with shared loops."""
    f = tmp / "test_isolation.py"
    f.write_text(
        "import asyncio\n"
        "from oxitest import Fixture\n"
        "async def test_shared(pool: Fixture[int]) -> None:\n"
        "    assert pool == 42\n"
        "async def test_independent() -> None:\n"
        "    loop = asyncio.get_running_loop()\n"
        "    assert loop is not None\n"
    )

    async def async_pool_factory() -> int:
        return 42

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "pool",
            async_pool_factory,
            conftest_path="/c.py",
            shared=True,
            is_async=True,
        )
    )
    session = FixtureSession(reg)
    r1 = helpers.common.run_test(str(f), "test_shared", session)
    r2 = helpers.common.run_test(str(f), "test_independent", session)
    assert r1.status == "passed", f"test_shared: {r1.status!r}, {r1.message!r}"
    assert r2.status == "passed", f"test_independent: {r2.status!r}, {r2.message!r}"
    session.end_session()


# ── Built-in task_group fixture ──────────────────────────────────────────────


def test_task_group_fixture_basic(tmp: TempDir) -> None:
    """task_group fixture provides an asyncio.TaskGroup that spawns and awaits tasks."""
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio\n"
        "from oxitest import Fixture\n"
        "async def test_spawn(task_group: Fixture[asyncio.TaskGroup]) -> None:\n"
        "    results: list[int] = []\n"
        "    async def worker(n: int) -> None:\n"
        "        results.append(n)\n"
        "    task_group.create_task(worker(1))\n"
        "    task_group.create_task(worker(2))\n"
        "    await asyncio.sleep(0)  # let tasks run\n"
        "    assert sorted(results) == [1, 2]\n",
        "test_spawn",
    )
    assert result.status == "passed", (
        f"task_group fixture should allow spawning tasks, "
        f"got status={result.status!r}, msg={result.message!r}"
    )


def test_task_group_fixture_cancels_on_test_end(tmp: TempDir) -> None:
    """Tasks still running when the test ends should be cancelled by TaskGroup exit."""
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio\n"
        "from oxitest import Fixture\n"
        "async def test_leak(task_group: Fixture[asyncio.TaskGroup]) -> None:\n"
        "    task_group.create_task(asyncio.sleep(999))\n"
        "    # Test ends without awaiting — TaskGroup.__aexit__ cancels it\n",
        "test_leak",
    )
    assert result.status in ("passed", "warned"), (
        f"task_group should handle leftover tasks gracefully, "
        f"got status={result.status!r}, msg={result.message!r}"
    )


def test_task_group_fixture_sync_test_error(tmp: TempDir) -> None:
    """Sync test requesting task_group should get a clear error."""
    result = helpers.common.exec_inline(
        tmp,
        "import asyncio\n"
        "from oxitest import Fixture\n"
        "def test_sync(task_group: Fixture[asyncio.TaskGroup]) -> None:\n"
        "    pass\n",
        "test_sync",
    )
    assert result.status == "error", (
        f"sync test with task_group should produce error, got {result.status!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"error should mention 'async fixture', got {result.message!r}"
    )


# ── Async fixture dependency errors ──────────────────────────────────────────


def test_sync_fixture_depending_on_async_fixture_error(tmp: TempDir) -> None:
    """A sync fixture that depends on a non-shared async fixture should error."""

    async def async_factory() -> int:
        return 42

    def sync_factory(dep: Fixture[int]) -> str:
        return f"got {dep}"

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "dep", async_factory, conftest_path="/c.py", is_async=True
        )
    )
    reg.register(
        helpers.common.make_fixture_def("combo", sync_factory, conftest_path="/c.py")
    )
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_combo(combo: Fixture[str]) -> None:\n"
        "    pass\n",
        "test_uses_combo",
        session=session,
    )
    assert result.status == "error", (
        f"sync fixture depending on async fixture should error, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    msg_lower = result.message.lower()
    assert "sync fixture" in msg_lower or "cannot depend" in msg_lower, (
        f"error should mention sync/async dependency issue, got {result.message!r}"
    )


def test_shared_async_depending_on_non_shared_async_error(tmp: TempDir) -> None:
    """A shared async fixture cannot depend on a non-shared async fixture."""

    async def non_shared_async() -> int:
        return 42

    async def shared_async(dep: Fixture[int]) -> str:
        return f"got {dep}"

    reg = FixtureRegistry()
    reg.register(
        helpers.common.make_fixture_def(
            "dep", non_shared_async, conftest_path="/c.py", is_async=True
        )
    )
    reg.register(
        helpers.common.make_fixture_def(
            "pool", shared_async, conftest_path="/c.py", shared=True, is_async=True
        )
    )
    session = FixtureSession(reg)
    result = helpers.common.exec_inline(
        tmp,
        "from oxitest import Fixture\n"
        "async def test_uses_pool(pool: Fixture[str]) -> None:\n"
        "    pass\n",
        "test_uses_pool",
        session=session,
    )
    assert result.status == "error", (
        f"shared async depending on non-shared async should error, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    msg_lower = result.message.lower()
    assert "lifetime" in msg_lower or "non-shared" in msg_lower, (
        f"error should mention lifetime mismatch, got {result.message!r}"
    )
