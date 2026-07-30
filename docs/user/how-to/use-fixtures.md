# Use fixtures

!!! abstract "How-to"
    Share setup and teardown across tests using oxitest's typed fixture system.

!!! info "Deep dive"
    See [PyO3 Bridge Contract](../../../internals/book/bridge.html) for how fixture lifecycle is managed across the Rust/Python boundary.

Fixtures are declared with `@oxi.fixture` in a `__fixtures__.py` beside the
tests that use them. An older route — a `Fixtures()` instance in `conftest.py`
— is still fully supported and is documented in
[Legacy: `Fixtures()` in `conftest.py`](#legacy-fixtures-in-conftestpy) at the
bottom of this page.

## Declare a fixture

Put a `__fixtures__.py` in the package that holds the tests, import oxitest,
and decorate a factory function. `lifetime` is a required keyword — there is
no default.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:declare-fixture"
```

The **namespace** is the basename of the directory the declaration file sits
in. Nothing names it explicitly — move the file and the namespace moves with
it:

```text
tests/
  api/
    __fixtures__.py    ← declares into fx.api
    test_api.py
    v1/
      __fixtures__.py  ← declares into fx.v1
      test_v1.py
```

Namespaces are therefore **not unique across a tree**: `tests/api/v1/` and
`tests/admin/v1/` both derive `v1`. That is legal, because no test can see
both — see [the B1 boundary](#understand-fixture-visibility-the-b1-boundary)
below.

`__fixtures__.py` is not the only declaration home. Three file kinds are
scanned:

| File | May declare | Notes |
|------|-------------|-------|
| `__fixtures__.py` | any lifetime | The general home. |
| `__init__.py` | any lifetime | A home for package-lifetime fixtures. |
| `test_*.py` | `function`, `module` | **Inline** — visible only inside that one module. |

A fixture placed in any other file — `helpers.py`, `utils.py` — is invisible to
oxitest by design. It is never scanned, so the fixture is dead code rather than
a silent half-registration.

!!! warning "A declaration home needs a test file beside it"
    oxitest registers a `__fixtures__.py` once per directory that holds a
    collected test file. A `__fixtures__.py` in a package whose tests all live
    in *sub*directories is never discovered, and its fixtures fail to resolve.
    Keep at least one test module in the declaring package, or move the
    declarations down. Tracked as
    [#1765](https://github.com/kalonji-tools/oxitest/issues/1765).

## Access a fixture

Two routes reach the same declaration.

**Through the `fx` proxy.** Annotate a parameter with the bare type `Fixtures`
and read `fx.<namespace>.<name>`. Resolution is lazy — only what the test
actually touches is built.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:proxy-access"
```

**Through `Fixture[T]` injection.** Annotate a test parameter with `Fixture[T]`.
Matching is **type-based** — oxitest compares the `T` in `Fixture[T]` against
fixture return types. The parameter name is a tie-breaker, not the key.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:injection-access"
```

!!! info "Disambiguation"
    If multiple fixtures return the same type, the parameter name acts as a
    **qualifier** to disambiguate. For example, if both `dev_db` and `prod_db`
    return `DBSession`, use `def test(dev_db: Fixture[DBSession])` to select
    the right one.

A yield fixture annotates its return as `Iterator[T]` rather than `T`, so
type-based matching never sees `T`. Reach those through
`fx.<namespace>.<name>`.

## Choose a lifetime

`lifetime` names the code-structural unit whose exit disposes the value. There
are four tiers:

| Lifetime | Built | Disposed | Under parallel execution |
|----------|-------|----------|--------------------------|
| `"function"` | Once per test that requests it | After that test | No effect |
| `"module"` | Once per test module | After the module's last test | No effect |
| `"package"` | Once per anchor package | After the subtree's last test | **Exactly once per run** — the subtree is collapsed onto a single worker |
| `"session"` | Once per worker process | At worker teardown | **Once per worker**, not once per run |

`module` lifetime with a `yield` is the common shape for an expensive resource
shared by one test file:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:module-lifetime"
```

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:module-lifetime-test"
```

`package` lifetime buys exactness and charges parallelism. Declared in
`tests/api/v1/__fixtures__.py`, it exists exactly once for the whole run, and
oxitest guarantees that by scheduling every test under `tests/api/v1/` onto one
worker:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/v1/__fixtures__.py:package-lifetime"
```

A `package` declaration that merges two or more test modules onto one worker
emits a warning naming the fixture, the declaring file, and the module count,
so the cost never has to be diagnosed by bisecting CI times. A declaring
package that holds a single module costs no parallelism and stays silent.

`session` is legal only in a rootdir package, and it is **not** a
run-wide singleton — each worker subprocess builds its own. It is the tier for
a per-process resource such as a connection pool:

```python
# tests/__fixtures__.py — illustrative
@oxi.fixture(lifetime="session")
def engine() -> Iterator[Engine]:
    engine = Engine()
    yield engine
    engine.dispose()
```

!!! note "Wide lifetimes in parallel mode"
    Each worker subprocess builds its own fixture session, so a `session`
    fixture runs once **per worker**, not once per run. Anything that must
    happen exactly once per run — a schema migration, a shared artifact build —
    belongs at rootdir `package` and pays the parallelism cost. See
    [Run in parallel](run-in-parallel.md#understand-session-scoped-fixture-behaviour-in-parallel-runs)
    for the subprocess model behind this.

## Understand fixture visibility: the B1 boundary

A fixture's **anchor** is the directory holding its declaration file. A fixture
is usable only by tests in its anchor package or a descendant of it — nowhere
else. A declaration in `tests/api/__fixtures__.py` serves `tests/api/` and
everything below it:

```text
tests/
  api/
    __fixtures__.py    ← anchor
    test_api.py        ✓ same package
    v1/
      test_v1.py       ✓ descendant
  admin/
    test_admin.py      ✗ sibling — refused
```

A descendant reaching up its ancestor chain is the ordinary case:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/v1/test_v1.py:descendant-access"
```

A sibling reaching across is refused at access time with a `BoundaryError`. The
example below is illustrative — it cannot be a passing test:

```python
# tests/admin/test_admin.py
def test_admin_dashboard(fx: Fixtures) -> None:
    conn = fx.api.api_conn  # raises BoundaryError
```

```text
BoundaryError: [fixture-boundary] fixture 'api.api_conn' is not visible from this test.
  Fixture anchor: tests/api
  This test:      tests/admin/test_admin.py
  B1: a fixture is usable only by tests in its anchor package or
      below (ADR-0009 Rule 3).
  Three ways forward:
    1. Move the declaration to a package that is an ancestor of both
    2. Move the test into tests/api or a package below it
    3. Declare a fixture of the same shape in this test's own package
```

The error is deliberately not `FixtureNotFoundError` — the fixture exists and
is spelled correctly, so "not found" would send you hunting for a typo that is
not there. The code `fixture-boundary` is stable: link it from your own docs,
grep for it in CI. There is no allow-comment escape hatch and no
`strict = "warn"` softening. See the
[error reference](../reference/errors.md#fixture-errors) for the full entry.

The boundary is enforced on both access routes, but only the `fx` proxy can
name it. `Fixture[T]` injection resolves by bare name and has no namespace
segment to attribute the failure to, so an out-of-anchor fixture reaches that
route as a plain `FixtureNotFoundError`. If a fixture you can see in the tree
reports as "not found", check the anchor before checking the spelling.

The same boundary governs a fixture's own dependencies, read against the
**fixture's** anchor rather than the location of whichever test triggered
resolution. A fixture anchored at `tests/api/` therefore cannot depend on one
anchored at `tests/api/v1/`, even when the test that asks for it lives in
`v1/`.

An inline declaration is anchored to its own test module, so it is invisible to
every other file — including siblings in the same directory.

!!! note "Two visibility regimes are live"
    `@oxi.fixture` declarations are strictly bounded as described above.
    `conftest.py` fixtures are **not**: they are registered run-wide and are
    exempt from the boundary, so a `conftest.py` fixture resolves from
    directories a `@oxi.fixture` one would not. Both regimes run side by side
    until `conftest.py` support is retired in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720); the gap is
    tracked as
    [#1760](https://github.com/kalonji-tools/oxitest/issues/1760).

## Request a fixture from a fixture

A fixture declares its own dependencies with the same `Fixture[T]` annotation a
test uses:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:fixture-dependency"
```

The test asks only for the outer fixture; the chain resolves behind it:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:dependency-test"
```

## Fixture teardown

=== "Yield teardown"
    Use `yield` to run cleanup code after the fixture's lifetime boundary is
    reached. Code before `yield` runs as setup; code after it runs as teardown,
    even if the test raises.

    ```python
    --8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:yield-teardown"
    ```

    ```python
    --8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:teardown-test"
    ```

=== "Imperative (addfinalizer)"
    Annotate a parameter with `TestContext` to get the test context object.
    Use `ctx.addfinalizer()` (or its alias `ctx.on_teardown()`) to register
    cleanup callbacks. Finalizers run in reverse registration order after the test.

    ```python
    --8<-- "python/tests/docs/how-to/fixtures/conftest.py:imperative-teardown"
    ```

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

## Legacy: `Fixtures()` in `conftest.py`

!!! warning "Supported, but no longer the primary route"
    Everything in this section still works and is not deprecated. It is
    scheduled for removal in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), at which
    point `Fixtures()`, `Helpers()`, and `conftest.py` discovery all go away
    together. New fixtures belong in a `__fixtures__.py`.

    The two routes differ in one user-visible way beyond syntax: `conftest.py`
    fixtures are registered run-wide and are exempt from
    [the B1 boundary](#understand-fixture-visibility-the-b1-boundary).

### Declare a Fixtures registry

Create one `Fixtures()` instance (or more — all are discovered automatically)
in `conftest.py` and decorate your factory functions with `@fx.fixture`.

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:declare-registry"

--8<-- "python/tests/docs/how-to/fixtures/conftest.py:simple-fixture"
```

Injection works exactly as it does for `@oxi.fixture` declarations — annotate a
test parameter with `Fixture[T]`:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:inject-fixture"
```

The parameter name `data` doesn't need to match the fixture name `sample_data` —
oxitest finds the fixture by its return type `list[int]`.

### Yield teardown on a legacy fixture

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:yield-fixture"
```

### Depend on another legacy fixture

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:fixture-depends-on-fixture"
```

### Share a fixture across all tests with shared

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
would cause cross-test interference. In parallel mode each worker subprocess
has its own fixture session, so a `shared=True` fixture runs once **per
worker**, not once per run — see
[Run in parallel](run-in-parallel.md#understand-session-scoped-fixture-behaviour-in-parallel-runs).

### Run fixtures automatically with autouse

A fixture with `autouse=True` runs for every test without being explicitly
requested:

```python
--8<-- "python/tests/docs/how-to/fixtures/autouse/conftest.py:autouse-fixture"
```

`@oxi.fixture` has no `autouse` keyword yet — autouse on the new declaration
route is
[#1716](https://github.com/kalonji-tools/oxitest/issues/1716).

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

### Use multiple namespaces

Create multiple `Fixtures()` instances — one per concern. Each variable name becomes a
namespace, and it must not be a Python keyword or builtin:

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

### Understand conftest.py loading

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

**Registration is run-wide.** Which conftest files load is decided by the
walk-up, but once loaded, their fixtures live in a single flat registry for the
run. A fixture from `tests/unit/conftest.py` is therefore resolvable from
`tests/integration/test_x.py` whenever both directories contribute tests to the
same run — oxitest is more permissive here than pytest, and more permissive
than the B1 boundary that governs `@oxi.fixture`. Tracked as
[#1760](https://github.com/kalonji-tools/oxitest/issues/1760).

## See also

- [Use built-in fixtures](use-builtin-fixtures.md) — `TempDir`, `StdCapture`, `Patcher`, `LogCapture`, and other auto-injected fixtures
- [Fixture types reference](../reference/python-api/fixture-types.md) — API docs for `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, and `Fixtures`
- [Error reference](../reference/errors.md#fixture-errors) — `BoundaryError`, `FixtureNotFoundError`, and the rest of the fixture error surface
