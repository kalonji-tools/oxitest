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

Declare a fixture as `async def` to perform async setup. Use `yield` for
teardown, exactly as with sync yield fixtures. The teardown code runs after the
test completes, even if the test fails.

```python
--8<-- "python/tests/docs/how-to/conftest.py:async-fixture"
```

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:async-fixture-test"
```

An async fixture can only be injected into an async test. Injecting an async
fixture into a sync test is an error (see [Common errors](#common-errors)).

### `@arrange` with async fixtures

`@oxi.arrange` declares side-effect fixtures that should run around a test
without binding their values. It composes with async fixtures with one
constraint — the test must be async if the fixture is function-scope.

#### What's legal

| Test kind | Fixture kind | Result |
| --- | --- | --- |
| `def test_...` | sync fixture | works today |
| `async def test_...` | sync fixture | works today |
| any test kind | shared-scope async fixture | works today (session loop) |
| `async def test_...` | function-scope async fixture | **new** — runs on per-test loop |
| `def test_...` | function-scope async fixture | **ArrangeError** at test-start |

#### Example — the newly-supported case

```python
from oxitest import Fixtures, arrange

fx = Fixtures()


@fx.fixture
async def each_txn():
    # Setup runs on the per-test loop.
    yield
    # Teardown runs on the same loop, after the test body.


@arrange("each_txn")
async def test_async_write():
    ...
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
    - 'each_txn' (function scope) — defined at conftest.py:6
  Three ways forward:
    1. Make the test async — `async def test_...`
    2. Change fixture scope to 'shared' or 'session'
    3. Convert fixture to sync — remove `async` from def
```

Multiple illegal entries in the same `@arrange` are reported in one diagnostic
— the scan is all-or-nothing.

See `ArrangeError` in the [error reference](../reference/errors.md).

### Shared async fixtures

Add `shared=True` to cache the fixture value across all tests in a run. Shared
async fixtures are resolved once on a dedicated persistent event loop and torn
down at the end of the session.

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:shared-async-fixture"
```

A shared async fixture can only depend on sync fixtures or other shared async
fixtures. Depending on a non-shared async fixture is an error because their
lifetimes are incompatible.

### Built-in task_group fixture

oxitest provides a built-in `task_group` fixture (type: `asyncio.TaskGroup`)
for tests that need to spawn concurrent tasks. Tasks still running when the
test body returns are cancelled automatically.

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:task-group"
```

## Async marks and timeouts

All standard marks work on async tests:

```python
--8<-- "python/tests/docs/how-to/test_async_tests.py:async-marks"
```

The `@mark.timeout` decorator and the global `timeout` config key both apply to
async tests. When the timeout fires, the test is cancelled and reported with
status `timeout`.

## Async/sync compatibility

| Combination | Supported |
|---|---|
| `async def test` + async fixture | Yes |
| `async def test` + sync fixture | Yes |
| `def test` + sync fixture | Yes |
| `def test` + async fixture | No — error |

A sync fixture may not depend on an async fixture. A shared async fixture may
not depend on a non-shared async fixture. All other dependency directions are
valid.

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

The fixture `X` is declared as `async def` but the test `Y` is a plain `def`.
Change the test to `async def`, or change the fixture to a sync fixture.

**`sync fixture 'X' cannot depend on async fixture 'Y'`**

The sync fixture `X` depends on an async fixture `Y`. Move the async logic into
the test, or restructure so the async fixture wraps the sync one rather than
the reverse.

**`shared fixture 'X' cannot depend on non-shared async fixture 'Y' — lifetime mismatch`**

A shared async fixture can only depend on fixtures that outlive it. Declare `Y`
with `shared=True`, or remove the dependency.

**`async backend '<name>' not found — is the plugin installed?`**

The `async_backend` key in `pyproject.toml` names a backend that no installed
plugin provides. Install the plugin or remove the `async_backend` key to use
the default asyncio backend. See the
[error reference](../reference/errors.md#async-backend-errors) for details.

## See also

- [Use fixtures](use-fixtures.md) — fixture declaration, scopes, and teardown
- [Use built-in fixtures](use-builtin-fixtures.md) — TempDir, LogCapture, and other built-ins
- [Write plugins](write-plugins.md#async-backend) — implementing a custom async backend
- [Error reference](../reference/errors.md#async-backend-errors) — async backend error messages
