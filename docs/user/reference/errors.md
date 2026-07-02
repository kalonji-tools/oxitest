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

**Cause:** A `Helpers()` or `Fixtures()` variable name (or directory name)
used as a namespace matches a Python reserved keyword
(e.g. `class`, `for`, `match`).

**Fix:** Rename the `Helpers()` or `Fixtures()` variable, or rename the directory.

---

```text
"<name>" is a Python builtin and cannot be used as a namespace name.
```

**Cause:** A `Helpers()` or `Fixtures()` variable name (or directory name)
matches a Python builtin name (e.g. `int`, `list`, `print`).

**Fix:** Rename the `Helpers()` or `Fixtures()` variable, or rename the directory.

---

```text
Two conftest files use the same helpers namespace "<name>":
```

**Cause:** Two conftest.py files in the ancestor chain produce the same
namespace name from their `Helpers()` or `Fixtures()` variable names.

**Fix:** Rename the `Helpers()` or `Fixtures()` variable in one of the two
conftest files listed in the error message.

---

## Plugin errors

Plugin errors occur when oxitest cannot load or initialize a declared plugin.

---

```text
Plugin '<name>' not found
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
Plugin '<name>' has no oxitest_plugin() function
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
oxitest_plugin() did not return a Plugin instance
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
Multiple plugins provide a debugger backend: <plugin_a>, <plugin_b>
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
FixtureNotFoundError: no fixture named '<name>'
```

**Cause:** A test parameter is annotated with `Fixture[T]` but no fixture
with binding type `T` is registered — neither in `conftest.py`, via a plugin
`FixtureProvider`, nor as a built-in fixture.

**Fix:** Register a fixture that returns type `T` via `@fixtures.fixture` in
your `conftest.py`, or verify that the plugin providing the fixture is declared
in `pyproject.toml`. Check that the fixture has a return type annotation
matching `T`.

---

```text
FixtureCycleError: circular dependency detected among {<names>}
```

**Cause:** Two or more fixtures depend on each other, forming a cycle. For
example, fixture `a` depends on `b` and `b` depends on `a`.

**Fix:** Break the cycle by restructuring fixture dependencies. Extract shared
setup into a third fixture that both can depend on without creating a loop.

---

```text
UnannotatedFixtureParamError: parameter '<name>' has no Fixture[T] annotation
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
FixtureTeardownWarning: fixture '<name>' teardown failed during <node_id>: <error>
```

**Cause:** An exception was raised during the cleanup phase of a yield fixture.
The warning now includes the test `node_id` that triggered the teardown, making
it easier to identify which test exposed the issue. This is a **warning**, not
an error -- the test result itself is not affected.

**Fix:** Fix the teardown code in the fixture. Common causes include trying to
close an already-closed resource or referencing a variable that was not
assigned because setup failed.

---

```text
FixtureShadowWarning: fixture '<name>' in <child_conftest> shadows definition in <parent_conftest>
```

**Cause:** A `conftest.py` file defines a fixture with the same name as one
already registered by a parent `conftest.py`. The child definition silently
overrides the parent within its directory tree.

**Fix:** Rename the fixture in either the child or parent conftest to avoid
ambiguity. If the shadowing is intentional, suppress the warning with a
standard `warnings.filterwarnings` call in the child conftest.

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

## Strict mode violations

Strict mode violations are reported when running with `--strict=abort` and
produce **exit code 3**. They indicate patterns that violate oxitest's explicit
design principles.

---

```text
bare assert at line(s) <N, N, ...>
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
dict-style parametrize at line(s) <N, N, ...>
```

**Cause:** A `@parametrize` decorator uses a plain dictionary for test cases.
Strict mode requires frozen dataclasses for parametrize inputs to ensure
type safety and immutability.

**Fix:** Replace the dictionary with a frozen dataclass:

```python
from dataclasses import dataclass
import oxitest


@dataclass(frozen=True)
class AddCase:
    a: int
    b: int
    expected: int


@oxitest.parametrize(
    basic=AddCase(a=1, b=2, expected=3),
    zero=AddCase(a=0, b=0, expected=0),
)
def test_add(a: int, b: int, expected: int) -> None:
    assert a + b == expected
```

---

```text
missing mark reason for '@<marker>'
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
