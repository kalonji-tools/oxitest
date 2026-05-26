# Use fixtures

!!! abstract "How-to"
    Share setup and teardown across tests using oxitest's typed fixture system.

## Declare a Fixtures registry

Fixtures are declared via a `Fixtures()` instance in `conftest.py`. Create one
instance (or more — all are discovered automatically) and decorate your factory
functions with `@fx.fixture`.

```python
# conftest.py
from __future__ import annotations
import oxitest

fx = oxitest.Fixtures()

@fx.fixture
def sample_data() -> list[int]:
    return [1, 2, 3, 4, 5]
```

## Inject a fixture into a test

Annotate a test parameter with `Fixture[T]`. The annotation is the injection
signal — unannotated parameters are **not** injected.

```python
# tests/test_example.py
from conftest import sample_data
from oxitest import Fixture

def test_sum(sample_data: Fixture[list[int]]) -> None:
    assert sum(sample_data) == 15
```

Import the fixture function directly from `conftest`. Your type checker knows
`sample_data` is `list[int]` — no plugin required.

## Fixture teardown

=== "Yield teardown"
    Use `yield` to run cleanup code after the test completes. Code before `yield`
    runs as setup. Code after `yield` runs as teardown, even if the test raises.

    ```python
    from collections.abc import Generator

    @fx.fixture
    def temp_db() -> Generator[Connection, None, None]:
        conn = connect("sqlite:///:memory:")
        conn.execute("CREATE TABLE t (id INTEGER)")
        yield conn
        conn.close()
    ```

=== "Imperative (addfinalizer)"
    Annotate a parameter with `TestContext` to get the test context object.
    Use `ctx.addfinalizer()` (or its alias `ctx.on_teardown()`) to register
    cleanup callbacks. Finalizers run in reverse registration order after the test.

    ```python
    from pathlib import Path
    import tempfile
    from oxitest import TestContext

    @fx.fixture
    def managed_file(ctx: TestContext) -> Path:
        path = Path(tempfile.mktemp())
        path.write_text("hello")
        ctx.addfinalizer(lambda: path.unlink(missing_ok=True))
        return path
    ```

## Request a fixture from a fixture

Fixtures can depend on other fixtures using the same `Fixture[T]` annotation:

```python
@fx.fixture
def user(db: Fixture[Connection]) -> User:
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    return User(id=1, name="Alice")
```

## Share a fixture across all tests with shared

A fixture with `shared=True` is created once for the entire session and shared
across all tests. The value is immutable — any attribute or item write raises
`SharedFixtureMutationError` at runtime.

```python
@fx.fixture(shared=True)
def app_config() -> dict:
    return load_config("config.yaml")
```

Use `shared=True` for read-only session-wide resources such as loaded
configurations, compiled schemas, or database connection pools where mutation
would cause cross-test interference.

## Run fixtures automatically with autouse

A fixture with `autouse=True` runs for every test without being explicitly
requested:

```python
from collections.abc import Generator

@fx.fixture(autouse=True)
def reset_database(db: Fixture[Connection]) -> Generator[None, None, None]:
    yield
    db.execute("DELETE FROM users")
```

## Use multiple namespaces

Create multiple `Fixtures()` instances — one per concern. Each variable name becomes a
namespace:

```python
# conftest.py
import oxitest

db = oxitest.Fixtures()
http = oxitest.Fixtures()

@db.fixture
def conn() -> Connection:
    return Database.connect()

@http.fixture
def client() -> HttpClient:
    return HttpClient(base_url="http://localhost")
```

Access all of them through a single `fx: Fixtures` parameter. Fixtures resolve lazily —
only what the test accesses is created:

```python
from oxitest import Fixtures

def test_api_writes_to_db(fx: Fixtures) -> None:
    response = fx.http.client.post("/users", json={"name": "Alice"})
    row = fx.db.conn.query("SELECT name FROM users WHERE id = ?", response.json()["id"])
    assert row["name"] == "Alice"
```

If two namespaces define a fixture with the same name, `fx.db.conn` and `fx.http.conn`
are always independent — no name collisions.

## Access built-in fixtures via `fx.oxi`

The reserved `oxi` namespace exposes all [built-in fixtures](use-builtin-fixtures.md)
under short names. Mix custom and built-in fixtures through the same `fx` parameter:

```python
from oxitest import Fixtures

def test_export(fx: Fixtures) -> None:
    result = fx.db.conn.export()
    (fx.oxi.tmp.path / "export.json").write_text(result)
    assert fx.oxi.tmp.path.joinpath("export.json").exists()
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
