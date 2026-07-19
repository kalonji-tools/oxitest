"""@arrange async support — issue #1540.

Applies ADR-0006 (polymorphic-unified + loud rejection) to @oxi.arrange:
async function-scope fixtures on sync tests raise ArrangeError; async
function-scope fixtures on async tests run correctly (fixing today's silent
no-op at _fixture_instantiator.py:138-151).
"""

from oxitest import TempDir, helpers
from oxitest._bridge._fixture_instantiator import _unpack_sync


def test_async_each_fixture_on_sync_test_raises_arrange_error(
    tmp: TempDir,
) -> None:
    """@arrange(async_fixture) on a sync test raises ArrangeError.

    Uses helpers.common.run_oxitest to drive a real oxitest run so the arrange
    phase actually executes.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "\n"
        "@fx.fixture\n"
        "async def each_txn():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "\n"
        "@arrange('each_txn')\n"
        "def test_sync_read():\n"
        "    pass\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert "cannot arrange async fixture(s) on a sync test" in stdout, (
        f"expected the async_mismatch template (proxy for ArrangeError), got:\n{stdout}"
    )
    assert "'each_txn'" in stdout, (
        f"expected the illegal fixture name to appear, got:\n{stdout}"
    )
    assert "Three ways forward" in stdout, (
        f"expected the three-ways-forward escape hatch block, got:\n{stdout}"
    )
    assert rc != 0, (
        f"illegal @arrange must produce non-zero exit code, got rc={rc}\n"
        f"stdout:\n{stdout}"
    )


def test_each_loop_created_lazily_and_closed_after_test(
    tmp: TempDir,
) -> None:
    """Per-test loop is created lazily and closed in executor's finally.

    Only created if an async-each fixture is used; closed in the executor's
    finally block so no loop leaks.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "\n"
        "@fx.fixture\n"
        "async def each_txn():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "\n"
        "@arrange('each_txn')\n"
        "async def test_async_write():\n"
        "    pass\n"
    )

    stdout, stderr, _rc = helpers.common.run_oxitest(tmp)

    assert "1 passed" in stdout, (
        f"async test arranging an async-each fixture must run to green, got:\n{stdout}"
    )
    assert "unclosed" not in stderr, (
        f"per-test loop must be closed in finally — no `unclosed event loop` "
        f"warning, got stderr:\n{stderr}"
    )


def test_unpack_sync_passes_coroutine_through() -> None:
    """`_unpack_sync` passes coroutines through unchanged.

    This is the established handoff for non-arranged async fixtures injected
    as parameters: the async execution middleware (`_unpack_async_fixtures`
    in _middleware.py) awaits/advances the coroutine inside the test's event
    loop. A hard TypeError guard here would break every parameter-injected
    async fixture — the belt-and-braces originally proposed in the plan was
    dropped for this reason.

    For arranged async-each fixtures, the executor's `_drive_arrange_async_each`
    handles unwrapping on the per-test loop; the pass-through here is the
    fallback for the injection path.
    """

    async def coro() -> int:
        return 42

    coro_obj = coro()
    try:
        outcome = _unpack_sync(coro_obj, "leaky_fixture")
        assert outcome.value is coro_obj, (
            "_unpack_sync must return the coroutine unchanged so the async "
            "middleware can await it on the test's loop"
        )
    finally:
        coro_obj.close()  # avoid "coroutine was never awaited" warning


def test_multiple_illegal_entries_listed_in_one_diagnostic(
    tmp: TempDir,
) -> None:
    """N illegal @arrange entries → one raise, N bullets under `Illegal:`."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "\n"
        "@fx.fixture\n"
        "async def a():\n"
        "    yield\n"
        "\n"
        "@fx.fixture\n"
        "async def b():\n"
        "    yield\n"
        "\n"
        "@fx.fixture\n"
        "async def c():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "\n"
        "@arrange('a', 'b', 'c')\n"
        "def test_sync():\n"
        "    pass\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)

    assert "3 illegal entries" in stdout, (
        f"expected the diagnostic to say `3 illegal entries`, got:\n{stdout}"
    )
    for name in ("'a'", "'b'", "'c'"):
        assert name in stdout, (
            f"every illegal entry must appear in the diagnostic; missing "
            f"{name} in:\n{stdout}"
        )


def test_scan_is_all_or_nothing_no_partial_setup(tmp: TempDir) -> None:
    """When the scan raises, none of the arranged fixtures should have run.

    Mix legal + illegal entries. Verify a global counter incremented by the
    legal fixture stays at 0 — proving the scan ran before any instantiation.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "counter = {'setup_calls': 0}\n"
        "\n"
        "@fx.fixture\n"
        "def legal_sync():\n"
        "    counter['setup_calls'] += 1\n"
        "    yield\n"
        "\n"
        "@fx.fixture\n"
        "async def illegal_async():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "from conftest import counter\n"
        "\n"
        "@arrange('legal_sync', 'illegal_async')\n"
        "def test_mixed():\n"
        "    pass\n"
        "\n"
        "def test_counter_stayed_zero():\n"
        "    assert counter['setup_calls'] == 0, (\n"
        "        f'legal_sync must not have run before the scan raised, '\n"
        "        f'counter={counter}'\n"
        "    )\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)

    assert "cannot arrange async fixture(s) on a sync test" in stdout, (
        f"scan must raise on the illegal entry, got:\n{stdout}"
    )
    assert "1 error" in stdout and "1 passed" in stdout, (
        f"the follow-up test proves legal_sync did not run — test_mixed errors "
        f"and test_counter_stayed_zero passes, got:\n{stdout}"
    )


def test_mixed_sync_async_teardown_lifo(tmp: TempDir) -> None:
    """@arrange('sync_a', 'async_b', 'sync_c') → teardown sync_c → async_b → sync_a."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "order = []\n"
        "\n"
        "@fx.fixture\n"
        "def sync_a():\n"
        "    yield\n"
        "    order.append('sync_a')\n"
        "\n"
        "@fx.fixture\n"
        "async def async_b():\n"
        "    yield\n"
        "    order.append('async_b')\n"
        "\n"
        "@fx.fixture\n"
        "def sync_c():\n"
        "    yield\n"
        "    order.append('sync_c')\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "from conftest import order\n"
        "\n"
        "@arrange('sync_a', 'async_b', 'sync_c')\n"
        "async def test_lifo():\n"
        "    pass\n"
        "\n"
        "def test_order_is_lifo():\n"
        "    assert order == ['sync_c', 'async_b', 'sync_a'], (\n"
        "        f'teardown must be LIFO across mixed sync/async @arrange, '\n"
        "        f'got {order}'\n"
        "    )\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)
    assert "2 passed" in stdout, f"both tests must pass, got:\n{stdout}"


def test_per_test_loop_shared_across_setup_body_and_teardown(
    tmp: TempDir,
) -> None:
    """Setup, body, and teardown of an async-each fixture share one loop.

    Setup and teardown of an async-each arranged fixture must run on the same
    loop so the async generator can advance past its yield without being
    finalized by a different loop's `asyncio.run` on close. The async test
    body must also run on the same loop — ADR-0006 §Decision requires setup,
    body, and teardown share one loop identity so loop-bound resources
    yielded by the fixture (Events, Queues, aiohttp sessions) stay valid in
    the body. Body-loop identity is the invariant this test guards.
    """
    (tmp / "conftest.py").write_text(
        "import asyncio\n"
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "seen = {}\n"
        "\n"
        "@fx.fixture\n"
        "async def probe():\n"
        "    seen['setup'] = id(asyncio.get_running_loop())\n"
        "    yield\n"
        "    seen['teardown'] = id(asyncio.get_running_loop())\n"
    )
    (tmp / "test_sample.py").write_text(
        "import asyncio\n"
        "from oxitest import arrange\n"
        "from conftest import seen\n"
        "\n"
        "@arrange('probe')\n"
        "async def test_probe():\n"
        "    seen['body'] = id(asyncio.get_running_loop())\n"
        "\n"
        "def test_setup_body_teardown_ids_match():\n"
        "    assert (\n"
        "        seen['setup'] == seen['body'] == seen['teardown']\n"
        "    ), (\n"
        "        f'per-test loop identity must be stable across setup, body, '\n"
        "        f'and teardown so loop-bound arrange resources are usable '\n"
        "        f'in the body (ADR-0006), got {seen}'\n"
        "    )\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)
    assert "2 passed" in stdout, (
        f"probe test + verification test must pass, got:\n{stdout}"
    )


def test_teardown_raise_does_not_halt_draining(tmp: TempDir) -> None:
    """A raise in one teardown must not stop later teardowns from firing.

    Matches existing sync convention (`_warn_teardown` → continue).
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "log = []\n"
        "\n"
        "@fx.fixture\n"
        "async def outer():\n"
        "    yield\n"
        "    log.append('outer_teardown')\n"
        "\n"
        "@fx.fixture\n"
        "async def raising():\n"
        "    yield\n"
        "    log.append('raising_teardown_start')\n"
        "    raise RuntimeError('teardown boom')\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "from conftest import log\n"
        "\n"
        "@arrange('outer', 'raising')\n"
        "async def test_raise():\n"
        "    pass\n"
        "\n"
        "def test_outer_teardown_still_fired():\n"
        "    assert 'outer_teardown' in log, (\n"
        "        f'earlier-registered teardown must fire even after later one '\n"
        "        f'raises, got log={log}'\n"
        "    )\n"
    )

    # `--warnings` expands the reporter's collapsed warning summary so the
    # teardown diagnostic's text appears in stdout for assertion.
    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    # Both tests must pass: test_raise runs (fixtures set up, body executes,
    # teardown for `raising` fires and raises but is caught by safe_teardown,
    # then `outer`'s teardown still fires); test_outer_teardown_still_fired
    # verifies the log by asserting `outer_teardown` is present.
    assert "2 passed" in stdout, (
        f"outer teardown must still fire after later teardown raises, got:\n{stdout}"
    )
    # And the diagnostic for the swallowed teardown failure must surface —
    # sync convention (`_warn_teardown`) requires emitting a WARNING so the
    # user knows a teardown failed instead of silently swallowing it.
    assert "raising" in stdout and "teardown boom" in stdout, (
        f"async teardown failure must emit a diagnostic naming the fixture "
        f"and the exception message (sync convention), got:\n{stdout}"
    )


def test_arrange_missing_fixture_surfaces_as_error(tmp: TempDir) -> None:
    """@arrange('nonexistent') surfaces as a not-found error.

    Missing arranged fixtures are caught upstream by the Rust
    FixtureValidationPhase and reported to the user before the executor's
    arrange loop runs. This test asserts the observable behavior: the
    missing fixture name appears in a "not found" diagnostic.
    """
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "\n"
        "@arrange('nonexistent')\n"
        "def test_missing():\n"
        "    pass\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)

    assert "'nonexistent'" in stdout and "not found" in stdout, (
        f"expected the missing fixture name and 'not found' in diagnostic, "
        f"got:\n{stdout}"
    )


def test_arrange_scan_runs_once_per_test_not_per_parametrize_case(
    tmp: TempDir,
) -> None:
    """@arrange + @parametrize: the scan is orthogonal to parametrize.

    A parametrized sync test that arranges an illegal async-each fixture
    should trigger the scan for each case. Reporter may dedupe identical
    diagnostics; either 1 or 3 message copies is acceptable — what matters
    is that all three cases hit the scan (proven by 3 errored cases).
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "\n"
        "@fx.fixture\n"
        "async def illegal_async():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange, parametrize\n"
        "\n"
        "@arrange('illegal_async')\n"
        "@parametrize(\n"
        "    one={'x': 1},\n"
        "    two={'x': 2},\n"
        "    three={'x': 3},\n"
        ")\n"
        "def test_sync_cases(x: int) -> None:\n"
        "    pass\n"
    )

    stdout, _stderr, _rc = helpers.common.run_oxitest(tmp)

    assert "3 errors" in stdout or "3 error" in stdout, (
        f"one error per parametrize case (3 cases), got:\n{stdout}"
    )
    # The scan message must appear at least once (dedup is a reporter concern).
    assert "cannot arrange async fixture(s)" in stdout, (
        f"scan diagnostic must appear at least once, got:\n{stdout}"
    )


def test_arrange_type_based_entry_resolves_via_get_by_type(tmp: TempDir) -> None:
    """@arrange(SomeInjectable) resolves through the type branch.

    Covers the `isinstance(entry, type)` branch of
    `_scan_arrange_entries_for_async_mismatch` and `_resolve_arranged_entry`
    — the string-name path is already exercised by every other test in
    this file, but a type-based @arrange is the second half of the API and
    was untested here.
    """
    (tmp / "test_sample.py").write_text(
        "from oxitest import TempDir, arrange\n"
        "\n"
        "@arrange(TempDir)\n"
        "def test_type_arrange() -> None:\n"
        "    pass\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert rc == 0, (
        f"@arrange(TempDir) is a legal sync-type arrange, got rc={rc}\n"
        f"stdout:\n{stdout}"
    )
    assert "1 passed" in stdout, (
        f"@arrange(TempDir) must resolve the builtin fixture and let the "
        f"test pass, got:\n{stdout}"
    )


def test_async_each_coroutine_only_fixture(tmp: TempDir) -> None:
    """@arrange on an `async def` fixture that `return`s (no yield) runs.

    Covers the `inspect.iscoroutine` branch of `_drive_arrange_async_each`
    — every other async test in this file uses `yield` fixtures
    (asyncgens), so the plain-coroutine path had no coverage.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "calls: list[str] = []\n"
        "\n"
        "@fx.fixture\n"
        "async def coro_only():\n"
        "    calls.append('setup')\n"
        "    # no yield — pure coroutine, awaited for its side effect\n"
    )
    (tmp / "test_sample.py").write_text(
        "from oxitest import arrange\n"
        "from conftest import calls\n"
        "\n"
        "@arrange('coro_only')\n"
        "async def test_body() -> None:\n"
        "    pass\n"
        "\n"
        "def test_setup_ran() -> None:\n"
        "    assert calls == ['setup'], (\n"
        "        f'coroutine-only async fixture must be awaited on the '\n"
        "        f'per-test loop, got calls={calls}'\n"
        "    )\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert rc == 0, (
        f"coroutine-only async fixture must run to green, got rc={rc}\n"
        f"stdout:\n{stdout}"
    )
    assert "2 passed" in stdout, (
        f"both the async body test and the setup-ran verifier must pass, got:\n{stdout}"
    )


def test_cross_loop_asyncio_task_usable_in_body(tmp: TempDir) -> None:
    """Loop-bound Task created by @arrange is awaitable in the async body.

    Concrete bug ADR-0006 called out: an ``asyncio.Task`` is bound to the
    loop that scheduled it. Before this refactor, the arrange fixture ran
    on the executor's per-test loop while the body ran on a fresh loop
    from ``AsyncBackend.acquire_session()``. Awaiting the Task in the
    body raised
    ``RuntimeError: Task ... got Future ... attached to a different loop``.
    (Verified against pre-refactor code — the exact error surface.)

    The fixture publishes the pending Task into a module-global
    ``shared_state`` dict (arrange fixtures are side-effect-only — they
    do not inject values), and the async body cancels and awaits it.
    After sharing ``arrange_session`` with the middleware, setup, body,
    and teardown share one loop, so the Task stays awaitable throughout.
    """
    (tmp / "conftest.py").write_text(
        "import asyncio\n"
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "shared_state: dict = {}\n"
        "\n"
        "\n"
        "async def _idle() -> None:\n"
        "    # Long sleep — the test cancels this before it wakes.\n"
        "    await asyncio.sleep(60)\n"
        "\n"
        "\n"
        "@fx.fixture\n"
        "async def publish_task():\n"
        "    # create_task binds this Task to whichever loop is running here.\n"
        "    task = asyncio.create_task(_idle())\n"
        "    shared_state['task'] = task\n"
        "    yield\n"
        "    shared_state.clear()\n"
    )
    (tmp / "test_sample.py").write_text(
        "import asyncio\n"
        "from oxitest import arrange\n"
        "from conftest import shared_state\n"
        "\n"
        "@arrange('publish_task')\n"
        "async def test_cross_loop_task() -> None:\n"
        "    task = shared_state['task']\n"
        "    # Pre-refactor raises 'got Future attached to a different loop'.\n"
        "    task.cancel()\n"
        "    try:\n"
        "        await task\n"
        "    except asyncio.CancelledError:\n"
        "        pass\n"
        "    assert task.done(), (\n"
        "        'the arrange fixture created this Task on the arrange loop; '\n"
        "        'awaiting it here without a cross-loop RuntimeError proves '\n"
        "        'body and arrange share one loop (ADR-0006 body-loop identity)'\n"
        "    )\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert rc == 0, (
        f"cross-loop Task usage must succeed once arrange_session is "
        f"shared with the middleware, got rc={rc}\nstdout:\n{stdout}"
    )
    assert "1 passed" in stdout, (
        f"the async body must await the loop-bound Task without a "
        f"cross-loop RuntimeError, got:\n{stdout}"
    )
    assert "attached to a different loop" not in stdout, (
        f"cross-loop RuntimeError must not appear — that is the exact "
        f"bug this refactor fixes, got:\n{stdout}"
    )


def test_async_test_without_arrange_uses_fresh_session_fallback(
    tmp: TempDir,
) -> None:
    """Async test with no ``@arrange`` still runs via the fresh-session fallback.

    The middleware's ``_base`` has three branches: shared_session (longest-
    lived), arrange_session (per-test), and — when neither is present —
    a fresh ``acquire_session_guarded(backend)`` for just this test. When
    an async test does not use ``@arrange``, ``plan.arrange_session`` is
    ``None`` and the fallback path must still produce a working session.
    """
    (tmp / "test_sample.py").write_text(
        "import asyncio\n"
        "\n"
        "async def test_no_arrange() -> None:\n"
        "    # A trivial async operation — proves a working loop is available\n"
        "    # via the fallback path even without any arrange fixtures.\n"
        "    await asyncio.sleep(0)\n"
        "    assert asyncio.get_running_loop() is not None, (\n"
        "        'the fallback acquire_session_guarded path must supply a '\n"
        "        'running loop for async tests with no @arrange fixtures'\n"
        "    )\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert rc == 0, (
        f"async test without @arrange must pass via the fresh-session "
        f"fallback, got rc={rc}\nstdout:\n{stdout}"
    )
    assert "1 passed" in stdout, (
        f"the fallback path is exercised whenever no shared_session or "
        f"arrange_session exists, got:\n{stdout}"
    )


def test_shared_session_precedence_over_arrange_session(tmp: TempDir) -> None:
    """When both are present, ``shared_session`` wins for the body loop.

    Pins the middleware's documented precedence in ``_middleware.py::
    AsyncBridgeMiddleware.apply``: if both ``plan.shared_session`` and
    ``plan.arrange_session`` are populated, the body runs on the shared
    session's loop. Rationale: shared fixtures may yield loop-bound
    resources on that session; running the body elsewhere would break
    them.

    Known limitation (pre-existing before #1545): when both are present,
    an @arrange fixture that yields a loop-bound resource on the arrange
    session's loop cannot be used from the body — same cross-loop issue
    as pre-refactor code. Fixing the mixed case is a bigger design
    question about coalescing session lifetimes; out of scope for #1545.
    """
    (tmp / "conftest.py").write_text(
        "import asyncio\n"
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "seen = {}\n"
        "\n"
        "@fx.fixture(shared=True)\n"
        "async def shared_ping() -> int:\n"
        "    seen['shared'] = id(asyncio.get_running_loop())\n"
        "    return 1\n"
        "\n"
        "@fx.fixture\n"
        "async def arrange_ping():\n"
        "    seen['arrange'] = id(asyncio.get_running_loop())\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text(
        "import asyncio\n"
        "from oxitest import Fixture, arrange\n"
        "from conftest import seen\n"
        "\n"
        "@arrange('arrange_ping')\n"
        "async def test_body(shared_ping: Fixture[int]) -> None:\n"
        "    seen['body'] = id(asyncio.get_running_loop())\n"
        "\n"
        "def test_body_ran_on_shared_loop_not_arrange_loop():\n"
        "    assert seen.get('shared') is not None, (\n"
        "        'shared fixture must run so we can compare loops; '\n"
        "        f'got seen={seen}'\n"
        "    )\n"
        "    assert seen.get('arrange') is not None, (\n"
        "        'arrange fixture must run so we can compare loops; '\n"
        "        f'got seen={seen}'\n"
        "    )\n"
        "    assert seen.get('body') is not None, (\n"
        "        'async body must run so we can compare loops; '\n"
        "        f'got seen={seen}'\n"
        "    )\n"
        "    assert seen['body'] == seen['shared'], (\n"
        "        'body loop must equal shared session loop when both '\n"
        "        'shared_session and arrange_session are present — the '\n"
        "        'middleware picks shared_session first because shared '\n"
        "        f'fixtures may yield loop-bound resources on it; got {seen}'\n"
        "    )\n"
        "    assert seen['body'] != seen['arrange'], (\n"
        "        'sanity: arrange session should be a different loop from '\n"
        "        'shared session (otherwise the precedence test proves '\n"
        "        f'nothing); got {seen}'\n"
        "    )\n"
    )

    stdout, _stderr, rc = helpers.common.run_oxitest(tmp)

    assert rc == 0, (
        f"both tests must pass to prove shared_session wins the body-loop "
        f"race when both are present, got rc={rc}\nstdout:\n{stdout}"
    )
    assert "2 passed" in stdout, (
        f"async body + precedence-check test must both pass, got:\n{stdout}"
    )
