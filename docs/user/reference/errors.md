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
