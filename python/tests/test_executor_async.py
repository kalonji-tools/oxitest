"""Tests for async test execution: async fixtures, yield teardown, shared sessions."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from oxitest import (
    Fixture,
    TempDir,
    helpers,
)
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession


def test_async_test_passes(tmp: TempDir) -> None:
    """A passing async test is run via asyncio.run() and produces status='passed'."""
    result = helpers.common.exec_inline(
        tmp, "async def test_ok():\n    assert 1 == 1\n", "test_ok"
    )
    assert result.status == "passed", (
        f"async tests must go through asyncio.run() transparently -- passing is the"
        f" baseline contract, "
        f"got {result.status!r}, msg={result.message!r}"
    )


def test_async_test_fails(tmp: TempDir) -> None:
    """A failing assertion inside an async test produces status='failed'."""
    result = helpers.common.exec_inline(
        tmp, 'async def test_bad():\n    assert 1 == 2, "nope"\n', "test_bad"
    )
    assert result.status == "failed", (
        f"assertion failures inside async tests must surface identically to sync tests"
        f" -- "
        f"the event loop must not swallow them, got {result.status!r}"
    )
    assert "nope" in result.message, (
        f"the user's assertion message is their primary debugging clue -- "
        f"it must survive the async-to-sync boundary, got {result.message!r}"
    )


def test_async_test_error(tmp: TempDir) -> None:
    """An uncaught exception inside an async test produces status='error'."""
    result = helpers.common.exec_inline(
        tmp, "async def test_err():\n    raise ValueError('boom')\n", "test_err"
    )
    assert result.status == "error", (
        f"uncaught exceptions must be distinguished from assertion failures -- "
        f"'error' signals infrastructure problems, not test logic, got"
        f" {result.status!r}"
    )
    assert "ValueError" in result.message, (
        f"exception type is essential for debugging -- without it the user cannot "
        f"narrow the root cause, got {result.message!r}"
    )
    assert "boom" in result.message, (
        f"exception payload carries the developer's context about what went wrong -- "
        f"dropping it forces re-running with a debugger, got {result.message!r}"
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
        f"warnings inside async tests must propagate to the result -- "
        f"silently dropping them hides deprecation regressions, got {result.status!r}"
    )
    assert "DeprecationWarning" in result.message, (
        f"warning category identifies the class of problem -- without it users "
        f"cannot filter or suppress selectively, got {result.message!r}"
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
        f"@mark.skip must bypass the event loop entirely --"
        f" running and then discarding wastes time and risks"
        f" side effects, got {result.status!r}"
    )
    assert "not ready" in result.message, (
        f"skip reason tells the team when the test can be re-enabled -- "
        f"losing it turns a deliberate skip into a mystery, got {result.message!r}"
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
        f"xfail marks a known bug -- if the status is wrong the suite either hides "
        f"regressions or reports false failures, got {result.status!r}"
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
        f"unexpected pass must be visible so the team knows the bug is fixed "
        f"and can remove the xfail marker, got {result.status!r}"
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
        f"the executor must await async fixtures before injecting -- if it hands a"
        f" coroutine "
        f"to the test instead, the assertion inside will fail on type, got"
        f" {result.status!r}, "
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
        f"sync fixtures must work inside async tests -- fixture authors should not be"
        f" forced "
        f"to rewrite factories as async just because a consumer is async, got"
        f" {result.status!r}, "
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
        f"fixture setup failures are infrastructure problems, not test logic failures"
        f" -- "
        f"'error' status tells the user to fix the environment, not the test, got"
        f" {result.status!r}"
    )
    assert "db is down" in result.message, (
        f"the original exception message pinpoints the infrastructure issue -- "
        f"without it the user only knows 'something broke' but not what, got"
        f" {result.message!r}"
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
        f"sync tests cannot host an event loop -- silently injecting a coroutine would"
        f" cause "
        f"a confusing runtime TypeError instead of a clear diagnostic, got"
        f" {result.status!r}, "
        f"msg={result.message!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"naming the root cause (async fixture in sync context) lets the user fix the"
        f" test "
        f"signature or the fixture -- a generic error forces guesswork, got"
        f" {result.message!r}"
    )
    assert "val" in result.message, (
        f"the fixture name is the user's handle into conftest.py -- without it they"
        f" must "
        f"inspect every fixture to find the async one, got {result.message!r}"
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
        f"yield fixtures must deliver the yielded value, not the generator itself -- "
        f"the executor must drive the generator to its first yield, got"
        f" {result.status!r}, "
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
        f"test must pass so the teardown-order assertion below is meaningful -- "
        f"a failing test could short-circuit teardown, got {result.status!r},"
        f" msg={result.message!r}"
    )
    assert log == ["setup", "test_ran", "teardown"], (
        f"teardown after yield is the fixture's resource cleanup -- skipping it leaks "
        f"connections, files, or locks, got {log!r}"
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
    assert result.status == "failed", (
        f"the test must actually fail to validate that teardown still runs on failure"
        f" -- "
        f"a pass would make the teardown assertion meaningless, got {result.status!r}"
    )
    assert torn_down == [True], (
        f"teardown isolation: yield-fixture cleanup must run even when the test fails"
        f" -- "
        f"skipping it leaks resources and poisons subsequent tests, got {torn_down!r}"
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
    assert result.status == "error", (
        f"the test must actually error to validate that teardown runs on error -- "
        f"a different status would invalidate the teardown check below, got"
        f" {result.status!r}"
    )
    assert torn_down == [True], (
        f"teardown isolation: yield-fixture cleanup must run even on unhandled"
        f" exceptions -- "
        f"errors are more disruptive than failures, so cleanup is even more critical,"
        f" got {torn_down!r}"
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
        f"both fixtures must resolve successfully to validate the ordering assertions "
        f"below, got {result.status!r}, msg={result.message!r}"
    )
    assert log[:2] == ["setup_a", "setup_b"], (
        f"setup must follow dependency order -- fixture B may depend on A being ready,"
        f" got {log!r}"
    )
    assert log[2:] == ["teardown_b", "teardown_a"], (
        f"reverse teardown order mirrors try/finally nesting -- last setup is first"
        f" teardown "
        f"so dependent fixtures never outlive their dependencies, got {log!r}"
    )


def test_async_yield_fixture_teardown_error_warns(
    tmp: TempDir,
) -> None:
    """Teardown exception should emit a diagnostic, not crash."""

    async def async_yield_factory() -> AsyncGenerator[int, None]:
        yield 42
        msg = "teardown exploded"
        raise RuntimeError(msg)

    session = helpers.common.make_session_with("val", async_yield_factory)
    diag_token = _diagnostic_collector_var.set(session.diagnostics)
    try:
        result = helpers.common.exec_inline(
            tmp,
            "from oxitest import Fixture\n"
            "async def test_ok(val: Fixture[int]) -> None:\n"
            "    assert val == 42\n",
            "test_ok",
            session=session,
        )
    finally:
        _diagnostic_collector_var.reset(diag_token)
    assert result.status == "passed", (
        f"teardown errors must not retroactively fail a passing test -- the test body "
        f"already succeeded and its assertions are valid, got {result.status!r}, "
        f"msg={result.message!r}"
    )
    assert any(d.context == "fixture teardown" for d in session.diagnostics), (
        f"teardown failures must surface as diagnostics so they are visible in the"
        f" report -- silently swallowing them hides resource leaks and broken cleanup,"
        f" got {session.diagnostics!r}"
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
        f"setup-phase exceptions in yield fixtures must be reported as errors -- the"
        f" test "
        f"body never ran, so 'failed' would be misleading, got {result.status!r}"
    )
    assert "setup failed" in result.message, (
        f"the original exception message directs the user to the broken setup logic -- "
        f"a generic 'fixture error' forces them to re-run with --debug, got"
        f" {result.message!r}"
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
        f"async yield fixtures need an event loop to drive"
        f" the generator -- a sync test "
        f"has no loop, so the executor must reject this combination early, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"the error must explain the async/sync mismatch so the user knows whether to "
        f"make the test async or the fixture sync, got {result.message!r}"
    )
    assert "val" in result.message, (
        f"naming the offending fixture lets the user jump straight to conftest.py and "
        f"fix the right factory, got {result.message!r}"
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
        f"shared async fixtures amortize expensive setup across tests -- if the value "
        f"does not arrive, the entire sharing mechanism is broken, got"
        f" {result.status!r}, "
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
    assert r1.status == "passed", (
        f"test_a must pass to prove the shared fixture delivered the correct value on "
        f"first access: {r1.status!r}, {r1.message!r}"
    )
    assert r2.status == "passed", (
        f"test_b must pass to prove the cached value is identical -- a stale or "
        f"recreated fixture would yield a different call_count: {r2.status!r},"
        f" {r2.message!r}"
    )
    assert call_count == 1, (
        f"shared fixtures amortize expensive setup --"
        f" calling the factory twice defeats "
        f"the purpose and doubles setup cost, got {call_count}"
    )


def test_shared_async_stray_task_cleanup(tmp: TempDir) -> None:
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
    diag_token = _diagnostic_collector_var.set(session.diagnostics)
    try:
        r1 = helpers.common.run_test(str(f), "test_leaker", session)
        r2 = helpers.common.run_test(str(f), "test_clean", session)
    finally:
        _diagnostic_collector_var.reset(diag_token)
    assert r1.status == "passed", (
        f"the leaker test must pass so we can verify its stray tasks are cleaned up "
        f"before the next test runs: {r1.status!r}, {r1.message!r}"
    )
    assert r2.status == "passed", (
        f"stray tasks from test_leaker must not poison test_clean -- test isolation "
        f"requires each test to start with a clean task set: {r2.status!r},"
        f" {r2.message!r}"
    )
    leaked_diags = [
        d
        for d in session.diagnostics
        if d.context == "async session" and "leaked" in d.message.lower()
    ]
    assert len(leaked_diags) >= 1, (
        f"leaked tasks must be surfaced as diagnostics so developers know to await or"
        f" cancel them -- silent cleanup hides concurrency bugs, got"
        f" {session.diagnostics!r}"
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
        f"the test must pass to confirm the shared fixture was set up and injected -- "
        f"failure here invalidates the teardown-timing assertions below, got"
        f" {result.status!r}, "
        f"msg={result.message!r}"
    )
    assert log == ["setup"], (
        f"shared fixture teardown must be deferred until end_session -- running it"
        f" after "
        f"individual tests would destroy the value other tests still need, got {log!r}"
    )

    session.end_session()
    assert log == ["setup", "teardown"], (
        f"end_session is the last chance to release shared resources -- skipping"
        f" teardown "
        f"here leaks connections, temp files, or background tasks, got {log!r}"
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
    assert r1.status == "passed", (
        f"shared-fixture test must pass to confirm the session loop is running -- "
        f"failure here means the shared loop itself is broken: {r1.status!r},"
        f" {r1.message!r}"
    )
    assert r2.status == "passed", (
        f"tests without shared fixtures must get their own event loop via asyncio.run()"
        f" -- "
        f"reusing the shared session loop would leak state between unrelated tests: "
        f"{r2.status!r}, {r2.message!r}"
    )
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
        f"task_group is the structured-concurrency primitive for async tests -- if"
        f" basic "
        f"spawn-and-collect fails, no concurrent test patterns work, "
        f"got {result.status!r}, msg={result.message!r}"
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
        f"TaskGroup.__aexit__ must cancel orphaned tasks automatically -- if it does"
        f" not, "
        f"stray tasks leak into the next test or block event-loop shutdown, "
        f"got {result.status!r}, msg={result.message!r}"
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
        f"task_group requires an event loop -- a sync test has none, so the executor"
        f" must "
        f"reject the request upfront instead of crashing at runtime, got"
        f" {result.status!r}"
    )
    assert "async fixture" in result.message.lower(), (
        f"the error must explain that task_group is async-only so the user knows to"
        f" make "
        f"the test async rather than hunting for a missing import, got"
        f" {result.message!r}"
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
        f"a sync fixture cannot await its async dependency -- allowing this silently "
        f"injects a coroutine object instead of the resolved value, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    msg_lower = result.message.lower()
    assert "sync fixture" in msg_lower or "cannot depend" in msg_lower, (
        f"the error must explain the sync-depends-on-async violation so the user knows "
        f"to either make the consumer async or the dependency sync, got"
        f" {result.message!r}"
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
        f"a shared fixture outlives individual tests -- depending on a non-shared"
        f" fixture "
        f"would use a value that was torn down after the first test, "
        f"got {result.status!r}, msg={result.message!r}"
    )
    msg_lower = result.message.lower()
    assert "lifetime" in msg_lower or "non-shared" in msg_lower, (
        f"the error must call out the lifetime mismatch so the user knows the"
        f" dependency's "
        f"scope is too narrow, not that the fixture itself is broken, got"
        f" {result.message!r}"
    )
