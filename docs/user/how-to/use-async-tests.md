# Use async tests

!!! abstract "How-to"
    Write and run async test functions with oxitest, using async fixtures and
    the built-in asyncio backend.

## Writing async test functions

Declare a test as `async def` and oxitest runs it on the asyncio event loop
automatically. No decorator or import is required.

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:basic-async"
```

All the same features available to sync tests work with async tests: marks,
parametrize, timeouts, and fixture injection.

## Default backend — asyncio

The built-in backend is `asyncio`. It is always available and requires no
configuration. Each test gets a fresh event loop, and the loop stays open
across the whole test so an async fixture's teardown can still run on it.

If your tests are already async and everything works, you do not need to read
any further.

## Async fixtures

Declare a fixture as `async def` in a `__fixtures__.py` beside the tests that
use it. Every lifetime tier accepts an async factory — there is no async-only
subset. Use `yield` for teardown, exactly as with sync yield fixtures: the
teardown half runs after the scope exits, even if a test failed.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:async-function-lifetime"
```

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:async-module-lifetime"
```

### One task, at function lifetime

At `lifetime="function"`, a fixture's setup, the test body and the fixture's
teardown run in **one asyncio task**. That matters for anything whose identity
is per-task rather than per-loop:

- an `anyio.CancelScope` entered before the `yield` and exited after it
- an `asyncio.TaskGroup` held across the `yield`
- a `contextvars.ContextVar` set in setup and read from the test

This holds however the fixture is reached — a `Fixture[T]` parameter, the
`fx.` proxy, or `@oxi.arrange`.

!!! warning "A sync `@oxi.arrange` teardown runs on the test's loop"
    When an async test arranges both a sync fixture and an async one, the two
    share a teardown pass so that disposal stays strictly last-in-first-out.
    That pass runs inside the test's event loop, so a **sync** arranged
    fixture's teardown must not drive its own:

    ```python
    @oxi.fixture(lifetime="function")
    def connection() -> Iterator[Conn]:
        conn = connect()
        yield conn
        asyncio.run(conn.aclose())   # fails — a loop is already running
    ```

    oxitest reports it as a teardown warning and the test still passes:
    `error in teardown of fixture 'connection': asyncio.run() cannot be called
    from a running event loop`. Make the fixture `async def` and `await` the
    call instead. A sync test, or an async test that arranges nothing async, is
    unaffected.

!!! warning "Wider lifetimes are exempt"
    At `lifetime="module"` and above, one setup serves many test bodies, so no
    single task can span them. A fixture that holds a `CancelScope`, a
    `TaskGroup`, or a `ContextVar` across its `yield` belongs at
    `lifetime="function"`. Declared wider, anyio reports
    `Attempted to exit cancel scope in a different task than it was entered in`.

Where a declaration file may live, what each lifetime tier means, and how a
namespace is derived are in
[Fixture declaration](../reference/python-api/fixture-declaration.md) — none of
that differs for async.

### Reach an async fixture with `await`

Ask for the `fx` proxy and await the attribute:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:async-proxy-access"
```

The `await` is not decoration — `fx.api.request_id` is an attribute access, so
there is no earlier point at which oxitest could have awaited anything for you.
A sync fixture is reached without it; an async one with it. The syntax says
which you have.

Awaiting the same fixture twice inside one test returns the same value — the
result is memoised, not the coroutine, so there is no
`cannot reuse already awaited coroutine` to work around.

Parameter injection also works for async fixtures at `function` lifetime, and
needs no `await` — the framework resolves those before the test body starts:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:async-injection"
```

!!! note "One async fixture, several access routes — one instance"
    Reaching the same `function`-lifetime **async** fixture through more than
    one route in a single test builds it **once**. Every route resolves
    through the same per-test cache, so each one observes the same object:

    ```python
    from oxitest import Fixture, Fixtures

    from myapp import Channel  # whatever the fixture returns


    async def test_mixed(channel: Fixture[Channel], fx: Fixtures) -> None:
        proxied = await fx.channel
        assert proxied is channel, "one build per test, whatever route"
    ```

    The same holds for `@oxi.arrange` together with `await fx.<name>` — which
    is the only way to arrange a fixture *and* read its value, because
    `@oxi.arrange` and a `Fixture[T]` parameter for one fixture are refused at
    collection.

    Sync fixtures behave the same way. This converged in
    [#2093](https://github.com/kalonji-tools/oxitest/issues/2093); before that
    the parameter route built its own instance.

### Disposal

When a value is built and disposed is set by its lifetime tier and is the same
for sync and async factories — the tiers are tabulated in
[Fixture declaration](../reference/python-api/fixture-declaration.md#lifetime-tiers).

One rule is async-specific: an async generator's post-`yield` half always runs
on the loop that started it, and never after that loop has closed. If a scope
somehow never exits, its teardown is still drained at session end rather than
being finalised silently.

### Loop selection

When any async fixture wider than `function` lifetime is registered, async
test bodies run on the shared session loop rather than a fresh per-test loop.
That is what lets a module-lifetime value outlive the test that built it: a
value bound to a loop cannot move to another one.

The check is deliberately conservative — a test that *could* reach such a
fixture runs on the shared loop even if it never touches one.

### Sync tests and the `fx` proxy

A sync test has no loop to await on, so the `fx` proxy refuses **any** async
fixture it is asked for, at every lifetime. The error fires at the access, not
at the end of the run:

```python
def test_sync(fx: Fixtures) -> None:
    conn = fx.api.client  # raises at this line
```

Produces:

```text
async fixture 'client' cannot be used by a sync test.
  Accessed as: fx.api.client
  Test kind:   sync (`def test_...`)
  Lifetime:    module
  Three ways forward:
    1. Make the test async — `async def test_...`, then `await fx.api.client`
    2. Raise the fixture's lifetime so it is built outside the test
    3. Convert fixture to sync — remove `async` from def
```

It fires before the fixture factory runs, so the traceback points at your line
rather than into the fixture body. See `AsyncFixtureAccessError` in the
[error reference](../reference/errors.md).

Forgetting the `await` is caught too:

```text
AttributeError: 'client' is an async fixture — await it before use:
`value = await fx....client`, then `value.execute`
```

### `@arrange` with async fixtures

`@oxi.arrange` declares side-effect fixtures that should run around a test
without binding their values. It composes with async fixtures with one
constraint — the test must be async if the fixture is at `function` lifetime.

#### What's legal

| Test kind | Fixture kind | Result |
| --- | --- | --- |
| `def test_...` | sync fixture | works |
| `async def test_...` | sync fixture | works |
| any test kind | async fixture wider than `function` lifetime | works — built on the shared session loop |
| `async def test_...` | `function`-lifetime async fixture | works — runs on the per-test loop |
| `def test_...` | `function`-lifetime async fixture | **ArrangeError** at test-start |

#### Example — the async case

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:async-arrange-fixture"
```

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:async-arrange"
```

#### Example — the refused case

```python
@arrange("each_txn")  # each_txn is async, this test is sync
def test_sync_read():
    ...
```

Produces:

```text
cannot arrange async fixture(s) on a sync test — 1 illegal entry.
  Arranged at:  test_foo.py:5
  Test kind:    sync (`def test_...`)
  Illegal:
    - 'each_txn' (function scope) — defined at __fixtures__.py:6
  Three ways forward:
    1. Make the test async — `async def test_...`
    2. Widen the fixture lifetime to 'module', 'package' or 'process'
    3. Convert fixture to sync — remove `async` from def
```

Multiple illegal entries in the same `@arrange` are reported in one diagnostic
— the scan is all-or-nothing.

See `ArrangeError` in the [error reference](../reference/errors.md).

### Built-in `task_group` fixture

oxitest provides a built-in `task_group` fixture (type: `asyncio.TaskGroup`)
for tests that need to spawn concurrent tasks. Tasks still running when the
test body returns are cancelled automatically.

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:task-group"
```

## See also

- [Use fixtures](use-fixtures.md) — declaring fixtures, lifetimes, and teardown
- [Fixture declaration](../reference/python-api/fixture-declaration.md) — where a declaration may live and what each lifetime tier means
- [Use built-in fixtures](use-builtin-fixtures.md) — TempDir, LogCapture, and other built-ins
- [Write plugins](write-plugins.md#async-backend) — implementing a custom async backend
- [Error reference](../reference/errors.md#async-backend-errors) — async backend error messages
