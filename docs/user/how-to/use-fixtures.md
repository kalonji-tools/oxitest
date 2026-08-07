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

## Decide whether you need a fixture

Not every piece of setup should be one. A fixture hands the framework control
over *when* your value is built and thrown away; a plain function or a `with`
block keeps that control in the test. Both have setup and teardown, so
"it needs cleaning up" is not the deciding factor.

Reach for a fixture when at least one of these is true:

| | Ask | Example |
|---|---|---|
| 1 | Must setup happen **before the test body starts**? | capturing everything a test prints — a `with` block can only see what happens after it opens |
| 2 | Does teardown need **something only the runner knows** — whether the test failed, its name, a CLI flag? | keeping a temp directory only on failure |
| 3 | Must the value **outlive a single test**? | one database container shared by a whole package |
| 4 | Is the value **the running test itself**? | `TestContext` — its name, params, finalizers |

If none of them holds, you do not need a fixture, and reaching for one costs
you something real: a plain function is easier to read, easier to call from
other code, and its arguments are type-checked at the call site.

Shared setup that is *just a function* is just a function — put it in a module
beside your tests and import it:

```python
# tests/factories.py — not a fixture: nothing above applies.
def make_user(name: str) -> User:
    return User(name=name, role="member")


# tests/test_greeting.py
from tests.factories import make_user


def test_greeting() -> None:
    user = make_user("alice")
    assert greet(user) == "Hello alice", "greeting should address the user by name"
```

Written as a fixture instead, that gains a declaration file, a lifetime you had
to choose, and a parameter — and loses the ability to take an argument.

The reasoning behind the four questions is in
[ADR-0012](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0012-block-scoped-forms-belong-on-the-object.md)
Rule 4.

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

A declaration home does not need a test file beside it. Every directory from a
test up to the rootdir package is scanned, so a package holding shared fixtures
and no tests of its own is found by the tests in its subdirectories:

```
tests/
    __fixtures__.py        # shared declarations, no tests here
    api/
        test_api.py        # sees them
```

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

### Drop the namespace with `fx.<name>`

`fx.<name>` reaches a fixture without naming its package. It resolves the
**nearest** visible declaration, so a fixture redeclared closer to the test
wins — the same locality rule the qualified form follows:

```python
def test_orders(fx: Fixtures) -> None:
    conn = fx.conn          # nearest visible `conn`
    conn = fx.api.conn      # explicitly api's, even if a nearer one exists
```

The shortcut saves typing, never scope. It reads the same filtered catalog the
qualified form does, so it can never reach a fixture the boundary rules would
refuse — a fixture anchored in a sibling package stays out of reach either way.
When it cannot resolve a name it reports it as not found, rather than as a
boundary violation, because a bare name carries no package to point at.

Two cases behave in ways worth knowing up front:

- **A package name wins over a fixture of the same name.** If a package `api`
  sits beside a fixture called `api`, `fx.api` is the package. The fixture is
  still reachable, as `fx.api.api`. Avoid the collision rather than rely on the
  rule.
- **Built-ins keep their prefix.** `fx.oxi.tmp` has no shortcut form; the `oxi`
  namespace exists precisely so framework names cannot collide with yours.

For an `async` fixture, await the shortcut exactly as you would the qualified
form — `value = await fx.conn`. A sync test that reaches an async fixture is
refused at the access, before the fixture body runs.

!!! tip "Prefer the qualified form in large suites"
    `fx.api.conn` says where the fixture comes from; `fx.conn` makes the reader
    find out. Neither is enforced — oxitest has no setting that warns on or
    forbids the shortcut — so this is a convention for your team to pick.

### Fixtures that come from a plugin

An activated plugin can ship fixtures of its own, and they appear under the
plugin's namespace — its module name by default:

```python
def test_query(fx: Fixtures) -> None:
    conn = fx.oxi_pg.conn   # a fixture declared by the oxi_pg plugin
    conn = fx.conn          # the shortcut works the same way
```

Two differences from your own declarations are worth knowing:

- **They are ambient.** A plugin fixture is reachable from every test in the
  run, at any depth. The B1 boundary below anchors *your* declarations to their
  own subtree; a plugin has no place in your tree to be anchored to.
- **Yours wins a name collision.** If you declare `conn` in your own
  `__fixtures__.py`, your declaration outranks the plugin's for every test that
  can see it — the same locality rule that lets a nearer declaration override a
  farther one. The run stays green, and a notice names both so the shadowing is
  visible rather than silent.

To shorten a long namespace, or to enable a plugin fixture the plugin declared
as `autouse`, see
[Ship fixtures from a `__fixtures__.py`](write-plugins.md#ship-fixtures-from-a-__fixtures__py).

## Choose a lifetime

`lifetime` names the code-structural unit whose exit disposes the value. There
are four tiers:

| Lifetime | Built | Disposed | Under parallel execution |
|----------|-------|----------|--------------------------|
| `"function"` | Once per test that requests it | After that test | No effect |
| `"module"` | Once per test module | After the module's last test | No effect |
| `"package"` | Once per anchor package | After the subtree's last test | **Exactly once per run** — the subtree is collapsed onto a single worker |
| `"process"` | Once per worker *process* | At process teardown | **Once per worker**, so as many instances as `-n` — not once per run |

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

`process` is legal only in a rootdir package, and it is **not** a
run-wide singleton — each process builds its own. It is the tier for
a per-process resource such as a connection pool:

```python
# tests/__fixtures__.py — illustrative
@oxi.fixture(lifetime="process")
def engine() -> Iterator[Engine]:
    engine = Engine()
    yield engine
    engine.dispose()
```

!!! note "Wide lifetimes in parallel mode"
    A worker builds **one** fixture session and reuses it for every task group
    it picks up, so a `process` fixture is built at most once per process. The
    count is bounded by how many processes exist — your `-n`, plus the
    coordinator when an inprocess or arranged test resolves it — rather than by
    your directory layout. Anything that must happen exactly
    once per run — a schema migration, a shared artifact build — belongs at
    rootdir `package` and pays the parallelism cost. See
    [Run in parallel](run-in-parallel.md#understand-session-scoped-fixture-behaviour-in-parallel-runs)
    for the subprocess model behind this, described there for the legacy
    `shared=True` tier, which is rebuilt per task group for the same reason.

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
grep for it in CI. There is no allow-comment escape hatch, and no `strict` position softens
it. See the
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

## Narrow a fixture to a block

A fixture's lifetime is chosen by its declaration, and teardown happens at that
boundary. Sometimes a test wants less than the whole window — set something up
for three lines, tear it down, then assert the un-set-up behaviour, all inside
one test.

**That narrower window is a method on the fixture's own object, not a second
fixture.** Two built-ins already work this way:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:stdcapture-disabled"
```

`StdCapture` captures for the whole test; `cap.disabled()` opens a hole in that
window. `LogCapture.at_level()` is the same shape — it narrows the capture
level for a block rather than for the test.

When you write your own fixture, follow it: if the value you hand back needs a
narrower window, put a context manager on that value. Declaring a second
fixture beside the first, or a module-level helper that builds another copy,
splits one capability across two names — and each half then looks like the
wrong choice from the other's documentation.

Use a `classmethod` when the block-scoped form should work *without* injecting
the fixture at all, and an instance method when it narrows an object the test
already has.

The reasoning, and the audit of which built-ins need this, is in
[ADR-0012](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0012-block-scoped-forms-belong-on-the-object.md).

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
| `fx.oxi.ctx` | `TestContext` — legacy, use `oxi.current_test()` |

!!! warning "`fx.oxi.ctx` is legacy"
    It still works and stays semver-protected until v4, but
    `oxi.current_test()` replaces it — and unlike `fx.oxi.ctx`, it is
    reachable from a plain function the test calls (#1949).

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

**How it differs from `autouse=True`:** [`autouse=True`](#run-fixtures-automatically-with-autouse)
on a fixture declaration makes it run for every test in the declaration's B1
boundary. `@oxi.arrange` is per-test — it opts a single test (or a class of
tests) into the fixture without affecting anything else.

**How it differs from `Fixture[T]` injection:** a `Fixture[T]` parameter gives
the test access to the fixture's value. `@oxi.arrange` is the right choice
when only the side effect matters and no parameter is wanted.

Multiple fixture names can be passed in a single decorator:

```python
--8<-- "python/tests/docs/how-to/fixtures/test_fixtures.py:arrange-multiple"
```

## Run fixtures automatically with autouse

`autouse=True` makes a declaration run for every test in its
[B1 boundary](#understand-fixture-visibility-the-b1-boundary) without any test
requesting it:

```python
@oxi.fixture(lifetime="module", autouse=True)
def migrations() -> Yields[None]:
    apply_migrations()
    yield
    roll_back()
```

The value is discarded. If a test *also* requests the fixture — by
`Fixture[T]` or `fx.<name>` — it gets the same instance the autouse pass built,
not a second one.

### How often it runs

The lifetime tier sets the rate:

| `lifetime` | Runs |
|---|---|
| `"function"` | Once per test in the boundary |
| `"module"` | Once per module in the boundary |
| `"package"` | Once per package boundary — at the rootdir, exactly once per run |
| `"process"` | Once per process that reaches it, so `-n 4` means up to five |

!!! warning "A rate, not a boundary event"
    The build happens **inside the first test** that reaches the boundary, not
    before it. Three consequences worth knowing: a failure in the fixture's
    setup is reported against that test rather than against the boundary; the
    setup's cost lands in that test's measured time; and a boundary whose tests
    are all skipped or deselected never fires its autouse fixture at all.

Where several autouse fixtures apply to one test, they run
**widest-lifetime-first** — `"process"`, then `"package"`, then `"module"`,
then `"function"` — so a narrower one can rely on a wider one having already
run. Within one tier they run in declaration order.

### Opt a subtree out

Declare a fixture of the same name **without** `autouse` at a deeper anchor:

```
tests/__fixtures__.py          @oxi.fixture(lifetime="module", autouse=True)
                               def seed_db(): ...        ← fires across tests/

tests/contract/__fixtures__.py @oxi.fixture(lifetime="module")
                               def seed_db(): ...        ← fires nowhere unless asked
```

Inside `tests/contract` the deeper declaration is what resolution returns, and
it is not autouse — so nothing fires. Outside it, the deeper declaration is
invisible and `seed_db` keeps firing as before. The suppression is local to the
subtree that declared it.

oxitest reports this at registration:

```text
[notice] fixture registration — fixture 'seed_db' in tests/contract/__fixtures__.py
shadows definition in tests/__fixtures__.py within tests/contract; the shadowed
fixture is autouse, so it no longer fires there
```

That notice is deliberate. Opting out this way is supported, so the message
confirms it worked — and it is the only warning you get when two unrelated
fixtures happen to share a name and one silently disables the other.

### Async autouse

An `async` factory may be autouse at `"module"`, `"package"` and `"process"`
lifetimes. At `"function"` it is refused:

```text
UsageError: txn in tests/__fixtures__.py is an async fixture declared
autouse=True with lifetime="function".
An autouse function-lifetime fixture fires for every test in its boundary, and
the sync tests among them cannot await it.
Hint: drop autouse=True and use @oxi.arrange("txn") on the tests that need it,
or widen to lifetime="module" or wider, which applies to sync and async tests
alike.
```

A function-lifetime autouse fires for *every* test in the boundary, sync ones
included, so an async factory there would be unusable for tests that never
asked for it. The error names the declaration rather than failing once per sync
test in scope.

## Legacy: `Fixtures()` in `conftest.py`

!!! warning "Supported, but no longer the primary route"
    Everything in this section still works and is not deprecated. It is
    scheduled for removal in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), at which
    point `Fixtures()` and `conftest.py` discovery both go away
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
    "Shared" means cached beyond a single test, and frozen (immutable) to prevent cross-test interference. It does **not** mean one instance per run — see the parallel-mode note below.

A fixture with `shared=True` is created once per **task group** and shared across
every test in that group. In a serial run that is the whole run; under parallel
execution a task group is a single test module unless a `lifetime="package"`
declaration merges a subtree. The value is immutable — any attribute or item
write raises `SharedFixtureMutationError` at runtime.

```python
--8<-- "python/tests/docs/how-to/fixtures/conftest.py:shared-fixture"
```

Use `shared=True` for read-only resources that are safe to rebuild, such as
loaded configurations, compiled schemas, or database connection pools where
mutation would cause cross-test interference. In parallel mode a worker builds a
fresh fixture session for every task group it picks up, so a `shared=True`
fixture is rebuilt once **per task group** — not once per run, and not once per
worker. See
[Run in parallel](run-in-parallel.md#understand-session-scoped-fixture-behaviour-in-parallel-runs).

### Run legacy fixtures automatically with autouse

A `Fixtures()` fixture with `autouse=True` runs for every test without being
explicitly requested:

```python
--8<-- "python/tests/docs/how-to/fixtures/autouse/conftest.py:autouse-fixture"
```

This is the **legacy** route's autouse, and it differs from
[the new one](#run-fixtures-automatically-with-autouse) in more than syntax: a
`conftest.py` fixture is registered run-wide and is exempt from the B1
boundary, so `autouse=True` here really does mean every test in the run. A
`@oxi.fixture` declaration fires only within its own anchor's subtree.

### Async autouse — legal combinations

Not every `autouse` × `async` combination has legal semantics. The table
below shows what registers and what is refused at decorator time. It describes
the **legacy** `Fixtures()` route, whose tiers are `shared=True`/`False`; the
new route's equivalent is [Async autouse](#async-autouse) above.

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
- [Fixture declaration reference](../reference/python-api/fixture-declaration.md) — `@oxi.fixture`, the declaration-home rules, and the lifetime cap
- [Fixture types reference](../reference/python-api/fixture-types.md) — API docs for `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, and `Fixtures`
- [Error reference](../reference/errors.md#fixture-errors) — `BoundaryError`, `FixtureNotFoundError`, and the rest of the fixture error surface
