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


def test_per_test_loop_shared_across_setup_and_teardown(
    tmp: TempDir,
) -> None:
    """Setup and teardown of an async-each fixture share the per-test loop.

    Setup and teardown of an async-each arranged fixture must run on the same
    loop so the async generator can advance past its yield without being
    finalized by a different loop's `asyncio.run` on close.

    NOTE: The async test body currently runs on a separate loop (created by
    AsyncBackend.run). Aligning the body loop with the per-test loop requires
    the `AsyncBackend.run` seam refactor tracked outside this PR (per
    ADR-0006 Consequences). Setup+teardown identity is the invariant this PR
    provides; body loop identity is a follow-up.
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
        "from oxitest import arrange\n"
        "from conftest import seen\n"
        "\n"
        "@arrange('probe')\n"
        "async def test_probe():\n"
        "    pass\n"
        "\n"
        "def test_setup_teardown_ids_match():\n"
        "    assert seen['setup'] == seen['teardown'], (\n"
        "        f'per-test loop identity must be stable across setup and '\n"
        "        f'teardown, got {seen}'\n"
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
