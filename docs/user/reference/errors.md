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
namespace '<name>' cannot be written as fx.<name>.<fixture> because it is
not a Python identifier.
```

**Cause:** A namespace derived from a directory basename or a plugin module
path cannot be spelled as an attribute. Either it is not an identifier
(`integration-tests`, `2fast`, or a dotted plugin path like `pkg.sub`), or it
is a reserved keyword (`class`, `for`), in which case the reason reads
`a Python keyword` instead.

`integration-tests` is the case worth knowing: `fx.integration-tests.conn` is
valid Python meaning `fx.integration - tests.conn`, so the access never
reaches oxitest and you see a missing fixture named `integration`.

**Severity:** a **warning**, not an error — the fixtures are still reachable
by shortcut access (`fx.<name>`), so the run continues. A namespace you wrote
by hand under `[tool.oxitest.plugin_settings.<module>]` is refused instead,
because you can retype it.

**Fix:** Rename the directory to a valid Python identifier, or keep using
shortcut access.

Builtins and soft keywords (`int`, `list`, `match`, `type`) are **not**
refused — `fx.int.conn` parses and resolves. A validator rejecting them was
removed in ADR-0009 Amendment 16.

---

```text
collection error: <fn_name> in <path> line <N> contains yield, so calling it returns a generator instead of running.
None of the test body executes, and the test is reported as passed. A test function returns None.
Hint: remove the yield, or move the generator into a fixture, where yield is how teardown is expressed.
```

**Cause:** A test function contains `yield`, which makes it a generator
function. Calling it returns a generator object and runs **no part of the
body**, so before this refusal the test was reported as passed having verified
nothing. Applies to `async def` and to methods of a `Test*` class. A
`@mark.skip` does not suppress it — a skip is a decision about a test that
could have run.

**Fix:** Remove the `yield`, or move the generator into a fixture, where
`yield` is how teardown is expressed:

```python
# Before — the body never runs
def test_connection():
    conn = connect()
    yield
    conn.close()

# After — the fixture owns setup and teardown
@oxi.fixture(lifetime="function")
def conn() -> Yields[Connection]:
    connection = connect()
    yield connection
    connection.close()


def test_connection(conn: oxi.Fixture[Connection]) -> None:
    assert conn.is_open, "the fixture must hand the test a live connection"
```

See [ADR-0017](../../adr/0017-a-test-function-returns-none.md).

---

```text
TestReturnedValueError: <fn_name> returned a generator instead of None, so none of its body ran and the test proved nothing.
Only the returned value shows this shape, so collection could not refuse it. A test function returns None.
Hint: remove the yield, or return nothing. To express setup and teardown, move the generator into a fixture, where yield is how teardown is written.
```

**Cause:** The same defect as above, reached by a route collection cannot see.
Two routes exist, and the message names neither, because it cannot tell them
apart from the value alone:

- **A decorator.** A wrapper built with `functools.wraps` leaves
  `inspect.isgeneratorfunction` answering `False`, so the shape is invisible
  until the wrapper is called.
- **An `async def` that returns a generator.** `async def test_x(): return (i
  for i in items)` is an ordinary coroutine — no wrapper is involved — and the
  generator only appears once the coroutine is awaited.

Reported as a **per-test error with exit code 4**, not a collection refusal.
The run is not stopped — every other test still executes and reports.

**Fix:** Remove the `yield`, or return nothing. With a decorator, the change
goes on the function *inside* it.

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

**Cause:** A module listed in `plugins = [...]` in `pyproject.toml` is not
installed. A dotted name reports this when any package on the path to it is
absent, not only the last segment.

**Fix:** Ensure the package is installed in the active environment and that the
module name is spelled correctly:

```console
$ python -c "import <name>"
```

**Exit code:** `4`. A setting naming something absent is an invalid request —
see [Exit codes](exit-codes.md#plugin-configuration-against-plugin-declarations).

---

```text
plugin "<name>" failed to import.
  The plugin is installed. An import inside it failed: <ImportError>
```

**Cause:** The plugin module itself is installed, but something it imports is
not. The absent module named in the message is the plugin's dependency.

**Fix:** Install the missing dependency, or report it to the plugin's author —
a plugin should declare what it imports.

**Exit code:** `3`. The plugin is defective rather than absent, so this is not
your `pyproject.toml`'s mistake.

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

Fixture errors occur during fixture declaration, resolution, or teardown.

---

```text
FixtureNotFoundError: fixture '<name>' not found.
  Hint: declare it with @oxi.fixture in a __fixtures__.py, or have a plugin provide it, and annotate the parameter with Fixture[<type>] in the test signature.
```

**Cause:** A test parameter is annotated with `Fixture[T]`, but no fixture with
binding type `T` is reachable. Nothing declares it with `@oxi.fixture` on this
test's ancestor chain, no plugin `FixtureProvider` supplies it, and it is not a
built-in fixture. The bare-name route also reports this error when the fixture
*is* declared but is anchored outside the test's package: unlike the `fx`
proxy, it has no namespace segment to attribute the failure to, so it cannot
raise `BoundaryError`.

A fixture that is declared correctly also reports as not found when its
declaration file could not be parsed, because a file that does not parse
registers nothing. Look for a diagnostic beside this error naming that file
and its syntax error — it is printed with the run, and `--warnings` expands
it. Fix the parse error rather than the fixture name.

**Fix:** Declare a fixture returning type `T` with `@oxi.fixture(lifetime=...)`
in the `__fixtures__.py` or `__init__.py` of the test's own package, or of an
ancestor of it. Check that the fixture has a return type annotation matching
`T`. If the fixture is supposed to come from a plugin, verify that the plugin
is declared in `pyproject.toml`.

When the namespace is known, the message names it and adds a note about inline
declarations:

```text
FixtureNotFoundError: fixture '<name>' not found in namespace '<namespace>'.
  Hint: check that '<namespace>' declares a fixture named '<name>' — in the __fixtures__.py or __init__.py of the anchor directory '<namespace>' — or verify the spelling.
  If '<name>' is declared inline in another test module it is capped at 'module' lifetime and cannot be used here; move it to __fixtures__.py to share it.
```

The inline note is always printed, whether or not such a declaration exists.
Inline fixtures register on module import, so whether this process has seen one
depends on worker assignment and import order. A hint that appeared only
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

**When it fires.** An access written literally — `fx.api.api_conn` — is read
out of the test body before the run starts, so the error refuses collection and
no test executes. An access oxitest cannot see until it runs, such as
`getattr(fx, name)`, reports as an errored test instead. Both exit `4`. The
first form is refused even when the code holding it would never have run: a
violation inside a skipped test, an `xfail`, or a branch that is never taken is
still a violation, and reporting it only when the line happens to execute meant
an `xfail` could absorb it and report the run as passing.

This is deliberately a distinct error from `FixtureNotFoundError`. Reporting
"not found" for a correctly-spelled name would send you hunting for a typo that
is not there. The stable code `fixture-boundary` is part of the message, so
documentation can link the failure and CI can grep for it without matching on
prose.

**Fix:** Pick one of the three restructurings the diagnostic names. There is no
allow-comment escape hatch and no `strict` position that softens it; the
boundary is not configurable.

When the leaf name is also wrong, the boundary is still reported first, with the
missing leaf appended:

```text
  Also: namespace 'api' has no fixture named 'typo' — fixing the spelling alone will not make this access legal.
```

---

```text
FixtureCycleError: fixture cycle detected: <a> → <b> → <name>
  Hint: break the cycle by removing a dependency or extracting shared setup into a separate fixture.
```

**Cause:** A fixture depends on itself through a chain of `Fixture[T]`
parameters. The chain in the message is sorted, then the fixture that closed
the cycle is appended.

**Fix:** Remove one dependency in the chain, or extract the setup both fixtures
need into a third fixture that neither depends on.

---

```text
UnannotatedFixtureParamError: parameter '<name>' in <fn_name> is not injected.
To request a fixture, annotate it: <name>: Fixture[<type>]
Unannotated parameters are not resolved by oxitest.
```

**Cause:** A parameter name matches a declared fixture, but the parameter has
no `Fixture[T]` annotation. Injection is explicit in oxitest: an unannotated
parameter is never resolved, even when a fixture of that name exists.

**Fix:** Annotate the parameter with `Fixture[<type>]`. If the parameter is not
meant to be a fixture, rename it so it does not match a fixture name.

---

```text
AmbiguousFixtureError: ambiguous fixture: 2 fixtures provide type 'DatabaseHandle': 'primary_db', 'replica_db'. Use the fixture name as the parameter name to disambiguate.
```

**Cause:** Two or more reachable fixtures declare the same return type, and the
parameter was resolved by type rather than by name.

**Fix:** Name the parameter after the fixture you want. Name-based resolution
takes precedence over type-based resolution, so `primary_db: Fixture[DatabaseHandle]`
selects one candidate unambiguously — provided that fixture's *provided type*
matches the annotation. A fixture declared `Yields[T]` provides `T`, not
`Yields[T]`. When the name matches exactly one fixture and the types still
disagree, the run reports `FixtureTypeMismatchError` instead.

---

```text
BroadFixtureTypeError: parameter 'x' uses Fixture[Any] which is too broad for type-based resolution. Use a concrete binding type.
```

**Cause:** A parameter is annotated `Fixture[Any]` or `Fixture[object]`. Every
fixture matches such a type, so type-based resolution cannot choose one.

**Fix:** Use the concrete type the fixture returns. If the value genuinely has
no useful type, resolve by name instead — name the parameter after the fixture.

---

```text
FixtureTypeMismatchError: fixture 'target' provides 'str', but the parameter is annotated Fixture[int]. Correct the annotation, or name a different fixture.
```

**Cause:** The parameter name matches exactly one fixture, and that fixture
provides a different type than the annotation declares. For a yield fixture the
provided type is the type it yields — a fixture declared `Yields[str]` provides
`str`.

**Fix:** Correct whichever of the two is wrong: the annotation, or the fixture
named. This is not an ambiguity — exactly one fixture carries the name, so
renaming the parameter cannot help.

---

```text
Error in fixture '<name>': <the exception the fixture raised>
  Hint: check the fixture function body for the exception above. If using a yield fixture, the error is in setup (before yield).
```

**Cause:** The fixture factory itself raised. `FixtureSetupError` wraps the
original exception rather than replacing it, so the underlying message is
carried through. For a yield fixture, this covers the setup half only — a
failure after `yield` is a teardown failure and is reported as the diagnostic
below.

**Fix:** Fix the exception in the fixture body. The wrapped message names it.

---

```text
Error in fixture '<name>': <a lifetime refusal>
```

**Cause:** `AsyncDependencyError`, a subclass of `FixtureSetupError`, is raised
when a fixture dependency's lifetime cannot hold its value. It covers three
refusals: a fixture that outlives the test depending on a shorter-lived async
fixture, a sync fixture depending on an async one, and a wider-lifetime fixture
depending on a `function`-lifetime async one. An async value is bound to one
test's event loop, so a wider-lived holder would hand it to tests whose loop is
gone.

It is a subclass rather than a flag because `FixtureSetupError` also wraps
genuine exceptions from a user's fixture body, which are ordinary failures. Only
the wiring mistake votes for exit 4.

**Fix:** Match the lifetimes. Either lower the depending fixture to
`lifetime="function"`, or raise the async dependency so it is built outside the
test.

---

```text
AsyncFixtureAccessError: async fixture 'conn' cannot be used by a sync test.
  Accessed as: fx.pkg.conn
  Test kind:   sync (`def test_...`)
  Lifetime:    function
  Three ways forward:
    1. Make the test async — `async def test_...`, then `await fx.pkg.conn`
    2. Raise the fixture's lifetime so it is built outside the test
    3. Convert fixture to sync — remove `async` from def
```

**Cause:** A sync test reached an async fixture through the `fx` proxy. A sync
test cannot `await`, so the only thing it could receive is a coroutine nothing
will ever await.

**Fix:** Pick one of the three the diagnostic names.

The error fires at the access itself, before the fixture factory runs, so the
traceback points at your line rather than into the fixture body.

A related `AttributeError` covers the neighbouring mistake — an *async* test
that forgot the `await`:

```text
AttributeError: 'conn' is an async fixture — await it before use: `value = await fx....conn`, then `value.execute`
```

See [async fixtures](../how-to/use-async-tests.md#async-fixtures).

---

```text
ArrangeError: cannot arrange async fixture(s) on a sync test — 1 illegal entry.
  Arranged at:  test_foo.py:42
  Test kind:    sync (`def test_...`)
  Illegal:
    - 'redis_client' (function scope) — defined at __fixtures__.py:15
  Three ways forward:
    1. Make the test async — `async def test_...`
    2. Widen the fixture lifetime to 'module', 'package' or 'process'
    3. Convert fixture to sync — remove `async` from def
```

**Cause:** A sync test used `@oxi.arrange` on one or more async
`function`-lifetime fixtures — the same illegal cell `AsyncFixtureAccessError`
covers, on the other access path. It is detected during collection, not at
decorator time. Async tests may legally arrange async `function`-lifetime
fixtures; sync fixtures compose freely on either test kind.

**Fix:** Pick one of the three the diagnostic names.

Other `@arrange` failure modes surface through the errors above: a missing
arranged fixture raises `FixtureNotFoundError`, and a factory that raises
produces `FixtureSetupError`.

See [`@arrange` with async fixtures](../how-to/use-async-tests.md#arrange-with-async-fixtures).

---

```text
UsageError: <name> in <file> is an async fixture declared autouse=True with lifetime="function".
An autouse function-lifetime fixture fires for every test in its boundary, and the sync tests among them cannot await it.
Hint: drop autouse=True and use @oxi.arrange("<name>") on the tests that need it, or widen to lifetime="module" or wider, which applies to sync and async tests alike.
```

**Cause:** `@oxi.fixture(lifetime="function", autouse=True)` was applied to an
`async def` factory. The combination is refused at registration, before any
test runs. An autouse `function`-lifetime fixture fires for every test in its
boundary, so it would manufacture the illegal sync-test-awaits-async-fixture
cell for tests that never asked for it.

**Fix:** Choose one of the two the diagnostic names — drop `autouse=True` and
use `@oxi.arrange("<name>")` on the tests that need it, or widen the fixture to
`lifetime="module"` or wider, which applies to sync and async tests alike.

---

```text
TestContext.current() is not available inside a fixture body.
  It is legal only from the body of a running test, and from plain functions that body calls.
  Inside a fixture, declare `ctx: TestContext` as a parameter instead — that context supports teardown registration.
```

**Cause:** `TestContext.current()` reads ambient state, so it refuses rather
than guessing when there is no running test to describe. The message names the
position it fired in — a fixture body, import or collection time, a
wider-than-`function` fixture's teardown (which runs after the test it might
have meant is already over), or a thread the test spawned. `threading` starts
each thread with a fresh context, so the identity does not cross that boundary.

**Fix:** Inside a fixture, declare `ctx: TestContext` as a parameter — that
context supports `on_teardown` and `module_path`. At import or collection time,
move the call into a test. In a wide fixture's teardown there is no current test
by construction, so capture what you need during setup instead.

---

```text
TestContext.name is not available here.
  This context was built for a fixture resolution, not for a test, so
  there is no test to name.
  Inside a fixture, ctx supports teardown registration only:
  ctx.addfinalizer(...) / ctx.on_teardown(...).
  To read the test's identity in a fixture, declare `test: TestIdentity` and
  lifetime="function" (#1879).
  On a test itself, use oxi.current_test().
```

**Cause:** A **fixture** body read `ctx.name`, `ctx.node_id`, `ctx.marks` or
`ctx.param_id`. A fixture is built once per lifetime tier, for whichever test
reaches it first, so above `function` lifetime there is no single test to name —
and at `function` lifetime the identity is not threaded to the resolution site.

**Fix:** Use `ctx` in a fixture for `addfinalizer` / `on_teardown` (and
`module_path`, which is unaffected). If the fixture needs the test's identity,
declare `ctx: TestContext` on the test and pass the value in from there.

The error surfaces wrapped in `FixtureSetupError`, since it is raised while the
fixture factory runs.

See [`TestContext`](python-api/builtins.md#testcontext).

---

```text
fixture value is frozen: cannot set attribute '<name>'
```

**Cause:** A test tried to mutate a fixture value that outlives one test. Values
declared above `function` lifetime are wrapped in a `FrozenProxy` that
intercepts attribute assignment, item assignment, and the setter half of an
augmented assignment such as `x.attr += y`. The wrapper exists so that one
test's mutation cannot leak into a sibling test. The deletion half reports
`cannot delete attribute` instead.

**Fix:** If per-test mutation is intended, declare the fixture
`@oxi.fixture(lifetime="function")` so each test gets its own value. If it is
not intended, treat the value as read-only and build a fresh derived value
rather than writing to the cached one.

See [`SharedFixtureMutationError`](exceptions.md#sharedfixturemutationerror) for
the exception type and how to assert on it.

---

```text
[warning] fixture teardown — fixture '<name>' teardown failed during <node_id>: <error>
```

**Cause:** A yield fixture raised after its `yield`. The exception is caught,
the diagnostic is recorded, and execution continues, so a teardown failure
cannot mask the test result it follows.

**Fix:** Handle the error inside the teardown half of the fixture. A teardown
that can fail should say what it could not clean up.

---

```text
[warning] fixture teardown — teardown callback '<name>' failed during <node_id>: <error>
```

**Cause:** A callback registered with `ctx.on_teardown(...)` or
`ctx.addfinalizer(...)` raised. Same handling as the entry above: caught,
recorded, execution continues. When the callback has no readable name the
message says `a teardown callback` instead.

**Fix:** Handle the error inside the callback.

---

```text
[warning] teardown registration — a callback registered from inside a running teardown is never run
```

**Cause:** `ctx.on_teardown(...)` was called while teardown was already
running. The teardown list is being drained at that point, so a callback
appended to it is never reached.

**Fix:** Register the callback during setup, before the fixture yields.

---

```text
[notice] fixture registration — fixture '<name>' in <shadower> shadows definition in <shadowed>
```

**Cause:** Two declarations provide the same fixture name, and the nearer one
wins. This is legal — a package may deliberately override an ancestor's
fixture — so it is a notice rather than a warning.

**Fix:** No action is required if the shadowing is intended. If it is not,
rename one of the two declarations.

---

```text
[warning] <fixture> — <fixture> (lifetime="package") co-locates <count> modules onto one worker — parallelism is disabled for <dir>. Narrow the fixture's package, or drop to lifetime="module".
```

**Cause:** A `package` fixture exists exactly once per run, and oxitest
guarantees that by scheduling every test under the declaring package onto a
single worker. When that package holds more than one test module, the guarantee
costs parallelism for the whole subtree — see
[Choose a lifetime](../how-to/use-fixtures.md#choose-a-lifetime).

This is a **diagnostic**, not an error. Every test still runs and the results
are unaffected. A declaring package that holds a single test module costs no
parallelism and emits nothing.

**Fix:** Move the declaration to a narrower package, or drop the fixture to
`lifetime="module"` if each module can hold its own value.

---

## Execution errors

Execution errors occur while tests are running.

---

```text
Timed out after <N>s
```

**Cause:** A test ran longer than its deadline. The deadline may come
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
Timed out after <N>s (the requested <M>s was capped by an enclosing deadline)
```

**Cause:** The test runs inside another test that already has a deadline, and
the enclosing deadline had less time left than this one asked for. The
effective deadline is always the shortest of the live deadlines, so a nested
deadline can never extend one that is already running (ADR-0016).

**Fix:** Raise the enclosing deadline, not this one. Raising the nested value
alone changes nothing, because the enclosing deadline is what cut the test.

---

```text
the <N>s deadline was cleared during this test, so the test did not run under a deadline
```

**Cause:** The test is reported `warned` rather than `passed`. On Unix a
deadline is delivered by one process-global timer. Code in the test wrote that
timer — with `signal.alarm` or `signal.setitimer`, or through a library that
uses one — which voids the deadline oxitest armed.

oxitest does not lock the timer, because the timer is not oxitest's to lock. It
reports the loss instead: the test passed, but it did not pass under the
deadline it declared.

**Fix:** Stop the test from writing the process timer, or accept the report. A
test that needs its own timer cannot also have an oxitest deadline enforced.

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
<node_id>                                                     test-returns-value   line <N>
```

**Cause:** A test function contains `return <value>`. oxitest discards whatever
a test returns, so an assertion written as `return a == b` is evaluated and
thrown away — the test passes whether or not the comparison holds.

Unlike a generator test, the body **did** run, so this is reported only under
`strict`. `return` and `return None` are both the rule being kept and neither
is flagged.

**Fix:** Write the comparison as an assertion with a message:

```python
# Before — the comparison is discarded
def test_addition():
    return add(2, 2) == 4

# After
def test_addition():
    assert add(2, 2) == 4, "addition is the whole subject of this test"
```

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

**Cause:** A fixture function does not have a return type annotation. Strict
mode requires explicit return types on all fixtures for clarity and type
safety.

**Fix:** Add a return type annotation to the fixture function:

```python
from oxitest import Yields, fixture


@fixture(lifetime="function")
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
