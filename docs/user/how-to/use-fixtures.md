# Use fixtures

!!! abstract "How-to"
    Share setup and teardown across tests using oxitest's typed fixture system.

!!! info "Deep dive"
    See [PyO3 Bridge Contract](../../../internals/book/bridge.html) for how fixture lifecycle is managed across the Rust/Python boundary.

## Declare a Fixtures registry

Fixtures are declared via a `Fixtures()` instance in `conftest.py`. Create one
instance (or more — all are discovered automatically) and decorate your factory
functions with `@fx.fixture`.

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:declare-registry"

--8<-- "python/tests/docs/how-to/fixtures/conftest.py:simple-fixture"
```

## Inject a fixture into a test

Annotate a test parameter with `Fixture[T]`. Resolution is **type-based** — oxitest
matches the `T` in `Fixture[T]` against fixture return types. The parameter name
is just for readability.

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:inject-fixture"
```

The parameter name `data` doesn't need to match the fixture name `sample_data` —
oxitest finds the fixture by its return type `list[int]`.

!!! info "Disambiguation"
    If multiple fixtures return the same type, the parameter name acts as a
    **qualifier** to disambiguate. For example, if both `dev_db` and `prod_db`
    return `DBSession`, use `def test(dev_db: Fixture[DBSession])` to select
    the right one.

## Fixture teardown

=== "Yield teardown"
    Use `yield` to run cleanup code after the test completes. Code before `yield`
    runs as setup. Code after `yield` runs as teardown, even if the test raises.

    ```python
    --8<-- "python/tests/docs/how-to/fixtures/conftest.py:yield-fixture"
    ```

=== "Imperative (addfinalizer)"
    Annotate a parameter with `TestContext` to get the test context object.
    Use `ctx.addfinalizer()` (or its alias `ctx.on_teardown()`) to register
    cleanup callbacks. Finalizers run in reverse registration order after the test.

    ```python
    --8<-- "python/tests/docs/how-to/fixtures/conftest.py:imperative-teardown"
    ```

## Request a fixture from a fixture

Fixtures can depend on other fixtures using the same `Fixture[T]` annotation:

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:fixture-depends-on-fixture"
```

## Share a fixture across all tests with shared

!!! note
    "Shared" means session-scoped: the fixture runs once per session and its return value is frozen (immutable) to prevent cross-test interference.

A fixture with `shared=True` is created once for the entire session and shared
across all tests. The value is immutable — any attribute or item write raises
`SharedFixtureMutationError` at runtime.

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:shared-fixture"
```

Use `shared=True` for read-only session-wide resources such as loaded
configurations, compiled schemas, or database connection pools where mutation
would cause cross-test interference.

## Run fixtures automatically with autouse

A fixture with `autouse=True` runs for every test without being explicitly
requested:

```python
--8<-- "python/tests/docs/how-to/fixtures/autouse/conftest.py:autouse-fixture"
```

### Async autouse — legal combinations

Not every `autouse` × `async` combination has legal semantics. The table
below shows what registers and what is refused at decorator time.

| autouse × scope × async factory | Registers? |
| --- | --- |
| sync factory, any scope | legal, unchanged |
| async factory, `shared=True` | legal — applies to sync AND async tests |
| async factory, `shared=False` (default) | **AutouseRegistrationError** |

**Why the third row is refused.** A function-scope async autouse would
only fire on async tests, silently skipping sync tests in the same suite
— a divergence that hides itself. oxitest's `strict = "abort"` DNA refuses
that ambiguity at registration.

**Two ways forward if you hit this:**

```python
# Option 1: drop autouse=True and @arrange on the tests that need it.
@fx.fixture
async def each_txn():
    yield

@arrange("each_txn")
async def test_async_write(): ...

# Option 2: change to shared scope — applies to both test kinds.
@fx.fixture(autouse=True, shared=True)
async def db_pool():
    yield
```

See `AutouseRegistrationError` in the [error reference](../reference/errors.md).

## Use multiple namespaces

Create multiple `Fixtures()` instances — one per concern. Each variable name becomes a
namespace:

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:namespace-fixtures"
```

Access all of them through a single `fx: Fixtures` parameter. Fixtures resolve lazily —
only what the test accesses is created:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:namespace-test"
```

If two namespaces define a fixture with the same name, `fx.db.conn` and `fx.http.conn`
are always independent — no name collisions.

## Access built-in fixtures via `fx.oxi`

The reserved `oxi` namespace exposes all [built-in fixtures](use-builtin-fixtures.md)
under short names. Mix custom and built-in fixtures through the same `fx` parameter:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:fx-oxi-test"
```

| Attribute | Type |
|-----------|------|
| `fx.oxi.tmp` | `TempDir` |
| `fx.oxi.tmp_factory` | `TempDirFactory` |
| `fx.oxi.cap` | `StdCapture` |
| `fx.oxi.fd_cap` | `FdCapture` |
| `fx.oxi.patch` | `Patcher` |
| `fx.oxi.log` | `LogCapture` |
| `fx.oxi.warn` | `WarnCapture` |
| `fx.oxi.ctx` | `TestContext` |

!!! warning "Reserved name"
    Using `oxi` as a `Fixtures()` variable name is reserved and raises a `ValueError`
    at load time.

## Inject fixtures without a parameter

Use `@oxitest.arrange("name")` when a fixture should run for its side
effects but its return value is not needed in the test body:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:arrange"
```

The fixture runs (including any teardown) exactly as it would if requested via a
`Fixture[T]` parameter — the only difference is that the value is discarded.

**How it differs from `autouse=True`:** `autouse=True` on a fixture declaration
makes it run for *every* test in the session. `@oxi.arrange` is per-test —
it opts a single test (or a class of tests) into the fixture without affecting
anything else.

**How it differs from `Fixture[T]` injection:** a `Fixture[T]` parameter gives
the test access to the fixture's value. `@oxi.arrange` is the right choice
when only the side effect matters and no parameter is wanted.

Multiple fixture names can be passed in a single decorator:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:arrange-multiple"
```

## Understand conftest.py loading

oxitest discovers `conftest.py` files by walking up the directory tree from each
test file to the rootdir. Every `conftest.py` found along the way is loaded, and
its `Fixtures()` instances are registered in the fixture session.

```text
project/
  conftest.py          ← loaded for all tests
  tests/
    conftest.py        ← loaded for tests in tests/ and below
    test_api.py
    unit/
      conftest.py      ← loaded for tests in tests/unit/ only
      test_helpers.py
```

**Loading order:** conftest files are loaded from the rootdir inward — outermost
first. A fixture defined in `tests/conftest.py` can depend on one from
`project/conftest.py`.

**Name precedence:** when two conftest files at different levels define a fixture
with the same name, the innermost (closest to the test file) wins. A fixture in
`tests/unit/conftest.py` shadows a same-named fixture in `tests/conftest.py` for
tests inside `tests/unit/`.

**Eager loading:** all conftest files on the path are loaded at session start,
before any tests run. This means import errors in conftest files are reported as
collection errors (exit code 3).

## Understand fixture scoping

oxitest has two fixture scopes:

| Scope | Created | Torn down | Syntax |
|-------|---------|-----------|--------|
| **Function** (default) | Once per test that requests it | After that test completes | `@fx.fixture` |
| **Session** (shared) | Once per session | After all tests complete | `@fx.fixture(shared=True)` |

There is no module or class scope. If you need setup that runs once per module,
use a `shared=True` fixture. If you need per-class setup, use a regular fixture
and request it from each test method.

!!! note "Shared fixtures in parallel mode"
    In parallel mode each worker subprocess has its own fixture session, so a
    `shared=True` fixture runs once **per worker**, not once per run. See
    [Run in parallel](run-in-parallel.md#understand-session-scoped-fixture-behaviour-in-parallel-runs)
    for details.

## See also

- [Use built-in fixtures](use-builtin-fixtures.md) — `TempDir`, `StdCapture`, `Patcher`, `LogCapture`, and other auto-injected fixtures
- [Fixture types reference](../reference/python-api/fixture-types.md) — API docs for `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, and `Fixtures`
