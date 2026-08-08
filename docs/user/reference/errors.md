# Error Reference

!!! abstract "Reference"
    Catalog of oxitest error messages with causes and fixes. Grouped by
    category. See also [Exit codes](exit-codes.md) for the numeric codes
    oxitest returns.

## Collection errors

Collection errors prevent oxitest from discovering tests. They produce
**exit code 3**.

---

```text
Failed to load conftest fixtures: ModuleNotFoundError: No module named '<name>'
```

**Cause:** A `conftest.py` file imports a module that is not installed or not
on `PYTHONPATH`.

**Fix:** Install the missing dependency (`pip install <name>`) or verify your
import paths and virtual environment are correct.

---

```text
Failed to import test module: <path>: <error>
```

**Cause:** A test file could not be imported due to a syntax error, missing
dependency, or other import-time failure.

**Fix:** Run the file directly to see the full traceback:

```console
$ python -c "import <module>"
```

---

```text
SyntaxError in <path>
```

**Cause:** Invalid Python syntax in a test file.

**Fix:** Open the file and check the line number reported in the error
message. Common causes include mismatched parentheses, missing colons, or
Python 2 syntax in a Python 3 file.

---

```text
"<name>" is a Python keyword and cannot be used as a namespace name.
```

**Cause:** A `Fixtures()` variable name (or directory name)
used as a namespace matches a Python reserved keyword
(e.g. `class`, `for`, `match`).

**Fix:** Rename the `Fixtures()` variable, or rename the directory.

---

```text
"<name>" is a Python builtin and cannot be used as a namespace name.
```

**Cause:** A `Fixtures()` variable name (or directory name)
matches a Python builtin name (e.g. `int`, `list`, `print`).

**Fix:** Rename the `Fixtures()` variable, or rename the directory.

---

## Configuration errors

Configuration errors surface when `pyproject.toml` cannot be parsed into a
valid `[tool.oxitest]` section. They produce **exit code 4** (`UsageError`).
See [ADR-0008](../../adr/0008-config-fail-closed-narrow-scope.md) for the
fail-closed design and its narrow scope.

---

```text
error: <path>/pyproject.toml: unknown field `<name>`, expected one of `testpaths`, `python_files`, ...
```

**Cause:** A key inside `[tool.oxitest]` is not a recognized field. Usually
a typo (`waivres` → `waivers`) or a stale key from a prior oxitest version.

**Fix:** Fix the typo, or remove the key if it was removed in a recent
oxitest release. See the [Configuration reference](configuration.md#keys)
for the current schema.

---

```text
error: <path>/pyproject.toml: invalid type: string "<value>", expected <T>
```

**Cause:** A key inside `[tool.oxitest]` has the wrong type — e.g.
`timeout = "10"` instead of `timeout = 10`, or `serial = "true"` instead
of `serial = true`.

**Fix:** Adjust the value to match the type listed in the
[Configuration reference](configuration.md#keys).

---

```text
error: <path>/pyproject.toml: `<key>` is no longer supported; move settings under <replacement> instead
```

**Cause:** A key was removed in a prior oxitest release. The error names
the new location for the settings (e.g. `doctest_modules` → `[tool.oxitest.doctest]`).

**Fix:** Follow the migration hint in the error message. See the
[Configuration reference](configuration.md#keys) for the current schema.

**Note:** Syntax errors elsewhere in `pyproject.toml` (a broken
`[tool.ruff]`, `[project]`, etc.) do not produce this error — oxitest warns
and runs under defaults, letting each tool police its own section.

---

## Plugin errors

Plugin errors occur when oxitest cannot load or initialize a declared plugin.

---

```text
plugin "<name>" not found. Is it installed?
  <ImportError>
```

**Cause:** A module listed in `plugins = [...]` in `pyproject.toml` cannot be
imported.

**Fix:** Ensure the package is installed in the active environment and that the
module name is spelled correctly:

```console
$ python -c "import <name>"
```

---

```text
plugin "<name>" has no oxitest_plugin() function
```

**Cause:** The module exists and imports successfully, but it does not export
the required entry point function.

**Fix:** Add the entry point to the plugin's `__init__.py`:

```python
from oxitest.plugin import Plugin


def oxitest_plugin(config=None) -> Plugin:
    return Plugin()
```

---

```text
oxitest_plugin() in '<name>' must return oxitest.Plugin, got <type>
```

**Cause:** The entry point function returned a value that is not an instance of
`oxitest.plugin.Plugin`.

**Fix:** Ensure the function returns `Plugin(...)`:

```python
from oxitest.plugin import Plugin


def oxitest_plugin(config=None) -> Plugin:
    return Plugin(reporters=(MyReporter(),))
```

---

```text
multiple plugins provide a debugger backend: <plugin_a>, <plugin_b>
```

**Cause:** More than one plugin in `plugins = [...]` declares a
`debugger_backend` field on its `Plugin` return value. Only one debugger
backend can be active.

**Fix:** Remove the extra plugin from `plugins` in `pyproject.toml`, or
reconfigure one of the plugins to not provide a debugger backend.

---

## Async backend errors

Async backend errors occur when the configured async backend cannot be resolved.

---

```text
async backend '<name>' not found — is the plugin installed?
```

**Cause:** The `async_backend` option in `pyproject.toml` names a backend that no
installed plugin provides.

**Fix:** Install the plugin that provides the backend, or check the spelling:

```toml
[tool.oxitest]
plugins = ["oxitest_trio"]
async_backend = "trio"
```

---

```text
multiple plugins provide async backend '<name>': <plugin_a>, <plugin_b>
```

**Cause:** Two or more installed plugins each provide an async backend with the same
`name` property.

**Fix:** Remove one of the conflicting plugins from the `plugins` list in
`pyproject.toml`, or contact the plugin authors to use distinct names.

---

## Fixture errors

Fixture errors occur during fixture resolution or teardown.

---

```text
TestContext.current() is not available inside a fixture body.
  It is legal only from the body of a running test, and from plain functions that body calls.
  Inside a fixture, declare `ctx: TestContext` as a parameter instead — that context supports teardown registration.
```

**Cause:** `TestContext.current()` reads ambient state, so it refuses rather
than guessing when there is no running test to describe. The message names the
position it fired in — a fixture body, import or collection time, a
wider-than-function fixture's teardown (which runs after the test it might
have meant is already over), or a thread the test spawned. `threading` starts
each thread with a fresh context, so the identity does not cross that boundary.

The refusal follows the same reasoning as `TestIdentityUnavailableError`: a
plausible-but-wrong context is worse than an error, because it is well-formed
and silent.

**Fix:** Inside a fixture, declare `ctx: TestContext` as a parameter — that
context supports `on_teardown` and `module_path`. At import or collection
time, move the call into a test. In a wide fixture's teardown there is no
current test by construction, so capture what you need during setup instead.

---

```text
ArrangeError: cannot arrange async fixture(s) on a sync test — 1 illegal entry.
  Arranged at:  test_foo.py:42
  Test kind:    sync (`def test_...`)
  Illegal:
    - 'redis_client' (function scope) — defined at conftest.py:15
  Three ways forward:
    1. Make the test async — `async def test_...`
    2. Change fixture scope to 'shared' or 'session'
    3. Convert fixture to sync — remove `async` from def
```

**Cause:** A sync test used `@oxi.arrange` on one or more async function-scope
fixtures — the illegal cell of the (test kind × fixture kind) matrix. Async
tests may legally arrange async-each fixtures; sync fixtures compose freely on
either test kind.

**Fix:** Pick one of the three escape hatches the diagnostic names.

Other `@arrange` failure modes surface through the existing error hierarchy:
missing arranged fixtures via `FixtureNotFoundError`, factory raises via
`FixtureSetupError` — both documented below.

See [`@arrange` with async fixtures](../how-to/use-async-tests.md#arrange-with-async-fixtures) for the async-composition rules.

---

```text
AsyncFixtureAccessError: async fixture 'conn' cannot be used by a sync test.
  Accessed as: fx.pkg.conn
  Test kind:   sync (`def test_...`)
  Lifetime:    each
  Three ways forward:
    1. Make the test async — `async def test_...`, then `await fx.pkg.conn`
    2. Raise the fixture's lifetime so it is built outside the test
    3. Convert fixture to sync — remove `async` from def
```

**Cause:** A sync test reached an async fixture through the `fx` proxy. This is
the same illegal cell `ArrangeError` covers, on the other access path — a sync
test cannot `await`, so the only thing it could receive is a coroutine nothing
will ever await.

**Fix:** Pick one of the three the diagnostic names.

The error fires at the access itself, before the fixture factory runs, so the
traceback points at your line rather than into the fixture body.

A related `AttributeError` covers the neighbouring mistake — an *async* test
that forgot the `await`:

```text
AttributeError: 'conn' is an async fixture — await it before use:
`value = await fx....conn`, then `value.execute`
```

See [async fixtures](../how-to/use-async-tests.md#async-fixtures).

---

```text
TestContext.name is not available here.
  This TestContext was built for a fixture resolution, not for a test, so
  there is no test to name.
  Inside a fixture, ctx supports teardown registration only:
  ctx.addfinalizer(...) / ctx.on_teardown(...).
  To read a test's identity, declare `ctx: TestContext` on the test itself and
  pass what you need into the fixture.
```

**Cause:** A **fixture** body read `ctx.name`, `ctx.node_id`, `ctx.marks` or
`ctx.param_id`. A fixture is built once per lifetime tier, for whichever test
reaches it first, so above `function` lifetime there is no single test to name
— and at `function` lifetime the identity is not threaded to the resolution
site. These reads used to return the fixture's own name, silently, so
`f"test_{ctx.name}"` produced one identical value for the whole run.

**Fix:** Use `ctx` in a fixture for `addfinalizer` / `on_teardown` (and
`module_path`, which is unaffected). If the fixture needs the test's identity,
declare `ctx: TestContext` on the test and pass the value in from there.

The error surfaces wrapped in `FixtureSetupError`, since it is raised while the
fixture factory runs.

See [`TestContext`](python-api/builtins.md#testcontext).

---

```text
AutouseRegistrationError: cannot register async fixture '<name>' as function-scope autouse.
  Defined at:  <file>:<lineno>
  Scope:       each  (autouse=True)
  Why:         a function-scope async autouse would only fire on
               async tests; silently skipping sync tests hides the
               mismatch. oxitest is strict: refuse the combination
               at registration so the intent is stated up front.
  Two ways forward:
    1. Drop autouse=True and use @arrange('<name>') on
       the async tests that need it.
    2. Pass shared=True — a shared-scope async autouse
       applies to both sync and async tests.
```

**Cause:** `@Fixtures.fixture(autouse=True)` was applied to an `async def`
factory with `shared=False` (the default). The combination is refused at
decorator time — before any test runs. A function-scope async autouse would
only fire on async tests, silently skipping sync tests in the same suite.

**Fix:** Choose one of the two exits the diagnostic names — drop
`autouse=True` and use `@arrange('<name>')` on the async tests that need it,
or change the scope to `shared=True` (applies to both sync and async
tests). See
[Async autouse — legal combinations](../how-to/use-fixtures.md#async-autouse-legal-combinations).

---

```text
FixtureNotFoundError: fixture '<name>' not found.
  Hint: declare it with @oxi.fixture in a __fixtures__.py, or have a plugin provide it, and annotate the parameter with Fixture[<type>] in the test signature.
```

**Cause:** A test parameter is annotated with `Fixture[T]` but no fixture with
binding type `T` is reachable — nothing declares it with `@oxi.fixture` in a
`__fixtures__.py` on this test's ancestor chain, nor in a `conftest.py`, nor
via a plugin `FixtureProvider`, nor as a built-in fixture. The bare-name route
also reports this error when the fixture *is* declared but is anchored outside
the test's package: unlike the `fx` proxy, it has no namespace segment to
attribute the failure to, so it cannot raise `BoundaryError`.

**Fix:** Declare a fixture returning type `T` with `@oxi.fixture(lifetime=...)`
in the `__fixtures__.py` of the test's own package or an ancestor of it. Check
that the fixture has a return type annotation matching `T`. If the fixture is
supposed to come from elsewhere, verify that the `conftest.py` defining it is
on the walk-up path, or that the plugin providing it is declared in
`pyproject.toml`.

When the namespace is known, the message names it and adds an unconditional
note about inline declarations:

```text
FixtureNotFoundError: fixture '<name>' not found in namespace '<namespace>'.
  Hint: check that '<namespace>' declares a fixture named '<name>' — in its package's __fixtures__.py, or in a Fixtures() instance of that name — or verify the spelling.
  If '<name>' is declared inline in another test module it is capped at 'module' lifetime and cannot be used here; move it to __fixtures__.py to share it.
```

The inline note is always printed, whether or not such a declaration exists.
Inline fixtures register on module import, so whether this process has seen one
depends on worker assignment and import order — a hint that appeared only
sometimes would be worse than one that is always true and sometimes irrelevant.

---

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

**Cause:** A test reached a `@oxi.fixture` declaration through the `fx` proxy
that lies outside its own anchor package and outside every ancestor of it —
the [B1 boundary](../how-to/use-fixtures.md#understand-fixture-visibility-the-b1-boundary).
The fixture exists; it is simply not visible from here. Sibling packages and
prefix-lookalike siblings (`tests/apiv2` against an anchor at `tests/api`) are
both outside. The same check runs when a fixture resolves *its own*
dependencies, against the fixture's anchor rather than the calling test's
location.

This is deliberately a distinct error from `FixtureNotFoundError`. Reporting
"not found" for a correctly-spelled name would send you hunting for a typo that
is not there. The stable code `fixture-boundary` is part of the message so
documentation can link the failure and CI can grep for it without matching on
prose.

**Fix:** Pick one of the three restructurings the diagnostic names — move the
declaration up to a package that is an ancestor of both, move the test into the
fixture's package or below it, or declare a fixture of the same shape in the
test's own package. There is no allow-comment escape hatch, and no `strict` position softens
it; the boundary is not configurable.

When the leaf name is also wrong, the boundary is still reported first, with the
missing leaf appended:

```text
  Also: namespace 'api' has no fixture named 'typo' — fixing the spelling alone will not make this access legal.
```

!!! note "Two visibility regimes"
    `conftest.py` fixtures are registered run-wide and are exempt from B1, so a
    cross-directory `conftest.py` fixture resolves where a `@oxi.fixture` one
    would not. Both regimes are live until `conftest.py` support is retired.

---

```text
FixtureCycleError: fixture cycle detected: <a> → <b> → <name>
```

**Cause:** Two or more fixtures depend on each other, forming a cycle. For
example, fixture `a` depends on `b` and `b` depends on `a`.

**Fix:** Break the cycle by restructuring fixture dependencies. Extract shared
setup into a third fixture that both can depend on without creating a loop.

---

```text
UnannotatedFixtureParamError: parameter '<name>' in <fn_name> is not injected.
To request a fixture, annotate it: <name>: Fixture[<type>]
Unannotated parameters are not resolved by oxitest.
```

**Cause:** A test function has a parameter that is not annotated with
`Fixture[T]`. oxitest requires explicit type annotations to inject fixtures.

**Fix:** Annotate the parameter with the appropriate fixture type. Built-in
fixtures use bare type annotations — no `Fixture[T]` wrapper needed:

```python
from oxitest import TempDir


def test_example(tmp: TempDir):
    ...
```

If the parameter is not a fixture, remove it from the function signature.

---

```text
AmbiguousFixtureError: ambiguous fixture: N fixtures provide type 'T': 'a', 'b'. Use the fixture name as the parameter name to disambiguate.
```

**Cause:** Multiple fixtures return the same binding type `T`, and the parameter
name doesn't match any of them. oxitest can't determine which fixture to inject.

**Fix:** Use the fixture name as the parameter name to disambiguate. For example,
if `dev_db` and `prod_db` both return `DBSession`, write
`def test(dev_db: Fixture[DBSession])` to select the right one.

---

```text
BroadFixtureTypeError: parameter 'x' uses Fixture[Any] which is too broad for type-based resolution. Use a concrete binding type.
```

**Cause:** A parameter is annotated with `Fixture[Any]` or `Fixture[object]` in
strict mode (`strict = "abort"`). Type-based resolution requires a concrete type.

**Fix:** Replace the broad annotation with a specific type:
`Fixture[DBSession]` instead of `Fixture[Any]`.

!!! note
    In non-strict mode, `Fixture[Any]` falls back to name-based resolution with
    a deprecation warning. In `strict = "abort"` mode, it is a collection error.

---

```text
[warning] fixture teardown — fixture '<name>' teardown failed during <node_id>: <error>
```

**Cause:** An exception was raised during the cleanup phase of a yield fixture.
The diagnostic includes the test `node_id` that triggered the teardown, making
it easier to identify which test exposed the issue. This is a **diagnostic**,
not an error — the test result itself is not affected.

**Fix:** Fix the teardown code in the fixture. Common causes include trying to
close an already-closed resource or referencing a variable that was not
assigned because setup failed.

---

```text
[warning] fixture teardown — teardown callback '<name>' failed during <node_id>: <error>
```

**Cause:** A cleanup callback registered outside the yield-fixture route raised
during teardown. That covers `ctx.addfinalizer()` / `ctx.on_teardown()` and the
cleanups the built-in fixtures register for themselves — `Patcher.close`,
`TempDir`'s removal, the capture fixtures' `close`. `<name>` is the callable: a
bound method reads as `Patcher.close`, a plain function as its own name.

Before this was reported, such a failure was discarded in silence — the cleanup
stopped running and nothing said so. This is a **diagnostic**, not an error: the
test result and the exit code are unaffected.

**Fix:** Fix the cleanup. For `Patcher.close` the usual cause is a
`patch.chdir()` whose original directory has since been deleted. Note that the
remaining overrides are still reverted — a failing undo does not strand the
ones behind it.

---

```text
[warning] teardown registration — a callback registered from inside a running teardown is never run — the loop that would have run it has already passed this point. Do this cleanup inline in the current teardown instead.
```

**Cause:** `ctx.addfinalizer()` or its alias `ctx.on_teardown()` was called
from inside a callback that is itself running as a teardown. The callback is
registered and then never invoked. This is a **diagnostic**, not an error — the
test result is not affected, and the registration is not rejected, only
reported.

**Fix:** Do the cleanup inline in the teardown you are already inside. If the
cleanup belongs to a fixture rather than to a test, express it with a `yield`
fixture, which runs its teardown at the right boundary by construction.

---

```text
[notice] fixture registration — fixture '<name>' in <shadower> shadows definition in <shadowed>
[notice] fixture registration — fixture '<name>' in <shadower> shadows definition in <shadowed> within <anchor>
[notice] fixture registration — fixture '<name>' in <shadower> shadows definition in <shadowed> within <anchor>; the shadowed fixture is autouse, so it no longer fires there
```

**Cause:** Two declarations of the same fixture name are both reachable from at
least one test, so the nearer one wins there. The first form is emitted when the
winner is ambient — a `conftest.py`, plugin or built-in fixture, which resolves
run-wide. The second is emitted when the winner is anchored, and `<anchor>` is
the subtree where it takes over: outside that subtree the other declaration
still resolves.

The third form adds the consequence when the declaration being shadowed is
[autouse](../how-to/use-fixtures.md) and the one shadowing it is not: the
shadowed fixture stops running inside `<anchor>`, and keeps running outside it.
That is the supported way to opt a subtree out of an autouse fixture, so this
notice is confirmation when it was deliberate — and the only signal you get when
two unrelated fixtures happened to pick the same name.

Declarations that no single test can reach at the same time do **not** produce
this notice. `tests/api/v1/__fixtures__.py` and `tests/admin/v1/__fixtures__.py`
may both declare `conn` — neither subtree contains the other, so neither
overrides anything. The same holds for two test modules that each declare an
inline fixture of the same name.

**Fix:** Rename one of the two, or move the nearer declaration if the override
was not intended. If it was intended, the notice is informational only.

!!! note "Two senses of *shadow*"
    This notice is about one **fixture name** overriding another on the
    `Fixture[T]` route. It is unrelated to the naming-clash rule, where a
    package **segment** wins over a same-named fixture in shortcut form —
    `fx.api` returns the sub-proxy, and the fixture stays reachable at its
    qualified path.

---

```text
shared fixture is immutable: cannot set attribute '<name>'
```

**Cause:** Test code attempted to set an attribute, delete an attribute, set an
item, or delete an item on a value returned by a `shared=True` fixture. Shared
fixtures use a frozen proxy to prevent cross-test mutation.

**Fix:** Either remove `shared=True` from the fixture (each test gets its own
copy), or restructure the test to avoid mutation:

```python
# Don't mutate the shared value
def test_read_only(config: Fixture[AppConfig]):
    local = dataclasses.replace(config, debug=True)  # copy instead
    assert local.debug
```

---

## Execution errors

Execution errors occur while tests are running.

---

```text
TimeoutError: test exceeded <N>s timeout
```

**Cause:** A test ran longer than the configured timeout. The timeout may come
from the `--timeout` CLI flag, the `timeout` key in `pyproject.toml`, or a
per-test `@mark.timeout(N)` decorator.

**Fix:** Either optimize the test to run faster, or increase the timeout:

```console
$ oxitest --timeout 60
```

```python
import oxitest


@oxitest.mark.timeout(60)
def test_slow_operation():
    ...
```

---

```text
worker subprocess unresponsive; killing
```

**Cause:** A parallel worker process stopped producing output, typically due to
an infinite loop, deadlock, or blocking I/O call in test or fixture code.

**Fix:** Check your test and fixture code for:

- Infinite loops or unbounded retries
- Blocking network or file I/O without a timeout
- Deadlocks from threading or multiprocessing

Run the test in serial mode (`--serial`) with `--show-internals` to narrow down the
hanging test.

---

```text
<node id> left the worker's working directory deleted. Restored to <path>.
Tests in a worker share one process, so this would have killed every
subprocess spawned afterwards.
```

**Cause:** The test changed the process working directory — usually with
`patch.chdir()` or a bare `os.chdir()` — into a directory that was then
deleted, most often the test's own `tmp` directory when its teardown ran.

Tests within a worker share one process, and the working directory is
process-global. Once it points at a deleted directory, every subprocess
started afterwards in that worker dies during interpreter startup, before any
of its own code runs. The failures surface on unrelated tests, so the cause is
not where the symptoms are.

oxitest restores the directory and reports the test that left it deleted. The
run continues; the reported test is marked as an error even if its own
assertions passed, because a test that silently breaks the rest of the worker
is not a passing test.

**Fix:** Restore the directory in the test, or avoid changing it at all:

```python
def test_reads_from_a_directory(tmp: TempDir, patch: Patcher) -> None:
    patch.chdir(tmp)  # restored automatically at teardown
    ...
```

If a test spawns subprocesses, pass an explicit working directory rather than
relying on inheritance:

```python
subprocess.run([sys.executable, "-c", "..."], cwd=str(tmp), check=False)
```

Note that `patch.chdir()` only restores if the test reaches its teardown. An
assertion that fails *before* the restore leaves the directory changed.

---

```text
a fixture teardown at a lifetime wider than function left the worker's
working directory deleted. Restored to <path>. No single test owns this
boundary, so the run continues with the directory repaired.
```

**Cause:** The same situation, but caused by the teardown of a fixture at
`module`, `package`, `process` or `session` lifetime. Those teardowns run at a
scope boundary rather than inside any one test, so no individual test can be
blamed and none is marked as an error.

**Fix:** As above — restore the working directory inside the fixture before it
deletes anything, or do not change it.

---

## Strict mode violations

Strict mode violations are reported when running with `--strict=abort` and
produce **exit code 3**. They indicate patterns that violate oxitest's explicit
design principles.

---

```text
<node_id>                                                     bare-assert        line <N>
```

**Cause:** The test file contains `assert` statements that do not use a
comparison operator, preventing oxitest from generating enriched assertion
diagnostics.

**Fix:** Use a comparison operator so oxitest can show both sides on failure,
or add a message string that explains intent:

```python
# Before (bare assert -- no diagnostics)
assert is_valid(x)

# After — comparison (oxitest shows expected vs actual)
assert validate(x).status == "valid"

# After — message string (explains intent on failure)
assert is_valid(x), "x should be valid after transform"
```

For exception testing, use `oxitest.raises()` instead of bare `assert`.

---

```text
<node_id>                                                     dict-parametrize
```

**Cause:** A `@parametrize` decorator uses a plain dictionary for test cases.
Strict mode requires frozen dataclasses for parametrize inputs to ensure
type safety and immutability.

**Fix:** Replace the dictionary with a frozen dataclass:

```python
--8<-- "python/tests/docs/reference/test_errors.py:dataclass-parametrize-fix"
```

---

```text
<node_id>                                                     missing-mark-reason   <marker>
```

**Cause:** A custom marker is declared in `[tool.oxitest] markers` without a
description string.

**Fix:** Add a description after the marker name, separated by a colon:

```toml
[tool.oxitest]
markers = [
    "slow: tests that involve real sleep or network delays",
    "integration: tests requiring external services",
]
```

---

```text
missing-return-annotation   <fixture_name>
```

**Cause:** A fixture function in `conftest.py` does not have a return type
annotation. Strict mode requires explicit return types on all fixtures for
clarity and type safety.

**Fix:** Add a return type annotation to the fixture function:

```python
from oxitest import Fixtures, Yields

fixtures = Fixtures()


@fixtures.fixture
def db_connection() -> Yields[Connection]:
    conn = Connection()
    yield conn
    conn.close()
```

---

```text
unused-fixture   <fixture_name>
```

**Cause:** A fixture defined in `conftest.py` is never referenced by any
collected test (neither via `Fixture[T]` annotations nor as a dependency of
another fixture that is used). This often indicates dead code or a typo in
a parameter name.

**Fix:** Either remove the unused fixture from `conftest.py`, or add a test
that uses it. If the fixture is intentionally unused (e.g., an `autouse`
fixture), verify that it is marked with `autouse=True` -- autouse fixtures
are excluded from this check.

---

### `single-case-parametrize`

```text
strict: @parametrize with a single case — use a plain test instead
```

**Cause:** A `@oxi.parametrize` decorator has exactly one case. This adds
indirection without benefit.

**Fix:** Remove the decorator and inline the value, or add more test cases:

```python
# Before
@oxi.parametrize("x", [42])
def test_answer(x: int):
    assert x == 42

# After
def test_answer():
    assert 42 == 42
```

---
