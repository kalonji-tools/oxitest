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
configuration. Each test gets a fresh event loop via `asyncio.run()`.

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

!!! warning "Known limitation: one async fixture, two access routes"
    Reaching the same `function`-lifetime **async** fixture through both a
    `Fixture[T]` parameter and `await fx.<name>` in one test builds it
    **twice**. The parameter route hands an un-awaited coroutine to the
    execution middleware, which cannot share the per-test cache the `fx.`
    proxy uses.

    Use one route per test until this converges:

    ```python
    from oxitest import Fixture, Fixtures

    from myapp import Channel  # whatever the fixture returns


    # Two instances — avoid.
    async def test_mixed(channel: Fixture[Channel], fx: Fixtures) -> None:
        proxied = await fx.channel  # not the same object as `channel`


    # One instance — prefer either route on its own.
    async def test_param(channel: Fixture[Channel]) -> None: ...


    async def test_proxy(fx: Fixtures) -> None:
        channel = await fx.channel
    ```

    Sync fixtures are unaffected — every route converges on one instance
    there. Tracked in
    [#1805](https://github.com/kalonji-tools/oxitest/issues/1805).

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
    2. Change fixture scope to 'shared' or 'session'
    3. Convert fixture to sync — remove `async` from def
```

Way forward 2 still speaks in the legacy `shared` / `session` scope vocabulary.
On a `@oxi.fixture` declaration the equivalent is raising `lifetime` to
`"module"` or wider.

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

### Legacy: async fixtures on a `Fixtures()` registrar

!!! warning "Supported, but no longer the primary route"
    Everything in this section still works and is not deprecated. It is
    scheduled for removal in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), at which
    point `Fixtures()` and `conftest.py` discovery both go away together. New
    async fixtures belong in a `__fixtures__.py`.

An `async def` decorated with a `Fixtures()` instance's `.fixture` is reached
through a `Fixture[T]` parameter only — there is no namespace to await through:

```python
# conftest.py
import asyncio

from oxitest import Fixtures

fx = Fixtures()


@fx.fixture
async def async_client():
    conn = await connect()
    yield conn
    await conn.aclose()
```

```python
# test_client.py
from oxitest import Fixture


async def test_client_connected(async_client: Fixture[dict]) -> None:
    assert async_client["connected"] is True, "the fixture connected"
```

#### `shared=True`

!!! warning "Legacy scope, not a lifetime tier"
    `shared` is the legacy registrar's own caching kwarg. It has no
    `@oxi.fixture` equivalent and no `Lifetime` maps to it; it goes away with
    the rest of the registrar in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720). Use
    `lifetime="module"` or wider on a declaration instead.

Add `shared=True` to cache the fixture value across all tests in a run. Shared
async fixtures are resolved once on a dedicated persistent event loop and torn
down at the end of the session.

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:shared-async-fixture"
```

A shared async fixture can only depend on sync fixtures or other shared async
fixtures. Depending on a non-shared async fixture is an error because their
lifetimes are incompatible.


## Async marks and timeouts

All standard marks work on async tests:

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:async-marks"
```

The `@mark.timeout` decorator and the global `timeout` config key both apply to
async tests. When the timeout fires, the test is cancelled and reported with
status `timeout`.

The deadline is enforced by the event loop, so it bites whenever your coroutine
`await`s. A coroutine that *blocks* instead — calling `time.sleep` rather than
`await asyncio.sleep`, or making a synchronous network call — never yields, so
the loop cannot cancel it. On Linux and macOS a signal still interrupts such a
call; on Windows nothing can, and the timeout is reported only once the blocking
call returns. See [the platform note on timeouts](use-markers.md#set-a-per-test-timeout).

## Async/sync compatibility

An async test can reach anything. A **sync** test's answer depends on the route
and the fixture's lifetime:

| Sync test reaches an async fixture via | `function` lifetime | wider than `function` |
|---|---|---|
| `fx.<ns>.<name>` | `AsyncFixtureAccessError` | `AsyncFixtureAccessError` |
| `Fixture[T]` parameter | error at test start | works — the value is built outside the test |
| `@oxi.arrange("<name>")` | `ArrangeError` | works |

The proxy is the strict route because a sync test has no loop to await on. The
other two admit whatever was already built before the test began.

A sync fixture may not depend on an async fixture. All other dependency
directions are valid. On the legacy registrar there is one further restriction
— see [`shared=True`](#sharedtrue).

## Configuring an alternative backend

The default asyncio backend covers most use cases. If you need a different
async runtime (e.g. trio), install a plugin that provides it and set
`async_backend` in `pyproject.toml`:

```toml
[tool.oxitest]
plugins = ["oxitest_trio"]
async_backend = "trio"
```

See [Write plugins](write-plugins.md#async-backend) for instructions on
implementing a custom backend.

## Common errors

**`async fixture 'X' cannot be used by sync test 'Y' — make the test async def`**

The `Fixture[T]` parameter route, for a `function`-lifetime async fixture: `X`
is declared `async def` but the test `Y` is a plain `def`, and nothing built
the value before the test started. Change the test to `async def`, raise the
fixture's lifetime, or change the fixture to a sync fixture.

**`async fixture 'X' cannot be used by a sync test.`** (multi-line, with
`Accessed as:` / `Test kind:` / `Lifetime:`)

The `fx` proxy route. This one fires at **any** lifetime — see
[Sync tests and the `fx` proxy](#sync-tests-and-the-fx-proxy).

**`sync fixture 'X' cannot depend on async fixture 'Y'`**

The sync fixture `X` depends on an async fixture `Y`. Move the async logic into
the test, or restructure so the async fixture wraps the sync one rather than
the reverse.

**`shared fixture 'X' cannot depend on non-shared async fixture 'Y' — lifetime mismatch`**

Legacy registrar only — `shared` goes away with it in
[#1720](https://github.com/kalonji-tools/oxitest/issues/1720). A shared async
fixture can only depend on fixtures that outlive it. Declare `Y` with
`shared=True`, or remove the dependency.

**`async backend '<name>' not found — is the plugin installed?`**

The `async_backend` key in `pyproject.toml` names a backend that no installed
plugin provides. Install the plugin or remove the `async_backend` key to use
the default asyncio backend. See the
[error reference](../reference/errors.md#async-backend-errors) for details.

## See also

- [Use fixtures](use-fixtures.md) — declaring fixtures, lifetimes, and teardown
- [Fixture declaration](../reference/python-api/fixture-declaration.md) — where a declaration may live and what each lifetime tier means
- [Use built-in fixtures](use-builtin-fixtures.md) — TempDir, LogCapture, and other built-ins
- [Write plugins](write-plugins.md#async-backend) — implementing a custom async backend
- [Error reference](../reference/errors.md#async-backend-errors) — async backend error messages
