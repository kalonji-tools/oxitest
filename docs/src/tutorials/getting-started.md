# Getting started

!!! abstract "Tutorial"
    Follow along to run your first test suite with oxitest.

In this tutorial you will install oxitest, write a small test file, and run it.
Along the way you will see how to read oxitest's output, how to interpret a
failure, and how to run a single test by name. By the end you will have a
working setup and know enough to test your own code.

## Prerequisites

- Python 3.10 or newer
- `pip` available in your shell
- No prior knowledge of oxitest is required

## Step 1 — Install oxitest

Install oxitest from PyPI the same way you would install any Python package.

```console
$ pip install oxitest
```

!!! tip "Developing oxitest itself?"
    Use `maturin develop` instead of `pip install` to build the Rust extension
    in place and make your local changes visible immediately.

## Step 2 — Create a project directory

Create a fresh directory to work in, along with a `tests/` subdirectory, and enter it.

```console
$ mkdir -p my_project/tests && cd my_project
```

## Step 3 — Write a test file

Create a file called `tests/test_math.py` with the following content:

```python
# tests/test_math.py

def test_addition():
    assert 1 + 1 == 2

def test_multiplication():
    assert 3 * 4 == 12

def test_string_repeat():
    assert "ha" * 3 == "hahaha"
```

oxitest discovers test files whose names match `test_*.py` or `*_test.py`, and
runs every function inside them whose name starts with `test_`. No imports, no
base classes, and no decorators are required.

## Step 4 — Run oxitest

From inside `my_project`, run:

```console
$ oxitest
```

You should see output similar to this:

```text
collected 3 items

···

════════════════════════════════════════════════════════════════════════════════
  3 passed
════════════════════════════════════════════════════════════════════════════════
  tip   3 assertions without messages  (--tips to expand)
════════════════════════════════════════════════════════════════════════════════
```

oxitest found your file, collected the three test functions, executed each one,
and printed a summary line. The collection and reporting steps happen in Rust,
which is why startup is fast even in large projects.

The `·` symbol (middle dot) marks a passing test that has at least one assertion
without a message argument. The plain `.` (period) appears instead when every
assertion in the test includes a message (e.g. `assert x == 1, "x must be 1"`).
The tip line at the end is a reminder that adding messages makes failures easier
to diagnose.

## Step 5 — Make a test fail

Understanding failure output is just as important as understanding success.
Edit `tests/test_math.py` and introduce a deliberate mistake in `test_multiplication`:

```python
def test_multiplication():
    assert 3 * 4 == 13   # wrong expected value
```

Run oxitest again:

```console
$ oxitest
```

The output now includes a diagnostic block for the failing test:

```text
collected 3 items

·F·

FAILURES ════════════════════════════════════════════════════════════════════════
FAILED  ./test_math.py::test_multiplication
        ┌─ ./test_math.py:5
        │
      5 │    assert 3 * 4 == 13   # wrong expected value
        │
        │  left:  12
        └─ right: 13

════════════════════════════════════════════════════════════════════════════════
  1 failed · 2 passed
════════════════════════════════════════════════════════════════════════════════
  tip   2 assertions without messages  (--tips to expand)
════════════════════════════════════════════════════════════════════════════════
```

The diagnostic block shows the file path and line number, the expression that
was evaluated, and the actual values on each side of the comparison. This is
often all you need to spot the problem without opening a debugger.

Revert the change before continuing:

```python
def test_multiplication():
    assert 3 * 4 == 12
```

## Step 6 — Run a single test by name

When you have many tests it is useful to run only one. The `-k` flag accepts a
substring expression and runs only the tests whose names contain a match.

Run just the addition test:

```console
$ oxitest -k addition
```

```text
collected 1 item

·

════════════════════════════════════════════════════════════════════════════════
  1 passed
════════════════════════════════════════════════════════════════════════════════
  tip   1 assertions without messages  (--tips to expand)
════════════════════════════════════════════════════════════════════════════════
```

You can also match multiple tests by combining terms with `and`, `or`, and
`not`. For example, `-k "addition or repeat"` would run `test_addition` and
`test_string_repeat`.

## Step 7 — Add a pyproject.toml for configuration

For options you want applied on every run, add an `[tool.oxitest]` section to
`pyproject.toml` in your project root. Create the file now:

```toml
# pyproject.toml

[tool.oxitest]
testpaths = ["tests"]
```

With `testpaths` set, oxitest will search `tests/` instead of the current
directory on every run. Command-line flags always take precedence over the
values in `pyproject.toml`.

## What you have learned

- How to install oxitest
- How to write a test file that oxitest can discover
- How to read both passing and failing output
- How to run a single test with `-k`
- How to set persistent defaults in `pyproject.toml`

!!! tip "Next steps"
    - [Use markers](../how-to/use-markers.md) — `@mark.skip`, `@mark.skipif`, `@mark.xfail`, custom marks, `-m` filter
    - [Use parametrize](../how-to/use-parametrize.md) — run one test against multiple named cases
    - [Use fixtures](../how-to/use-fixtures.md) — the `Fixtures()` registry, `shared=True`, yield teardown
    - [Use built-in fixtures](../how-to/use-builtin-fixtures.md) — `TempDir`, `StdCapture`, `Patcher`, `LogCapture`
    - [Run tests in parallel](../how-to/run-in-parallel.md) — `--workers`, `--serial`, tuning thresholds
    - [Use the test cache](../how-to/use-test-cache.md) — `--lf`, `--ff`, `--durations`
    - [CLI flags](../reference/cli.md) — full list of command-line options
    - [Configuration](../reference/configuration.md) — all keys supported under `[tool.oxitest]`
