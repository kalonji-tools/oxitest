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
    return Plugin(reporters=[MyReporter()])
```

---

## Fixture errors

Fixture errors occur during fixture resolution or teardown.

---

```text
FixtureNotFoundError: no fixture named '<name>'
```

**Cause:** A test parameter is annotated with `Fixture[T]` but no fixture with
that name is registered -- neither in `conftest.py`, via a plugin
`FixtureProvider`, nor as a built-in fixture.

**Fix:** Register the fixture with `@fixtures.fixture` in your `conftest.py`,
check the spelling of the parameter name, or verify that the plugin providing
the fixture is declared in `pyproject.toml`.

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

**Fix:** Annotate the parameter with the appropriate fixture type:

```python
from oxitest import Fixture
from oxitest.builtins import TempDir


def test_example(tmp: Fixture[TempDir]):
    ...
```

If the parameter is not a fixture, remove it from the function signature.

---

```text
FixtureTeardownWarning: error in teardown of fixture '<name>': <error>
```

**Cause:** An exception was raised during the cleanup phase of a yield fixture.
This is a **warning**, not an error -- the test result itself is not affected.

**Fix:** Fix the teardown code in the fixture. Common causes include trying to
close an already-closed resource or referencing a variable that was not
assigned because setup failed.

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

Run the test in serial mode (`--serial`) with `--tb=long` to narrow down the
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

**Fix:** Use comparison assertions so oxitest can show both sides on failure:

```python
# Before (bare assert -- no diagnostics)
assert is_valid(x)

# After (comparison -- oxitest shows expected vs actual)
assert is_valid(x) == True
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


@oxitest.mark.parametrize(
    case=AddCase,
    cases=[AddCase(1, 2, 3), AddCase(0, 0, 0)],
)
def test_add(case: AddCase):
    assert case.a + case.b == case.expected
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
