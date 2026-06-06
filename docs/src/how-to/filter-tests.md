# Filter Tests

!!! abstract "How-to"
    Run a targeted subset of your test suite using keyword substring matching,
    marker expressions, or file paths.

## Filter by keyword substring

Run only tests whose node ID contains a substring:

```console
$ oxitest -k add
```

This matches any test whose full node ID (`path/to/test_file.py::test_function_name`)
contains `add`, e.g. `test_add`, `test_add_negative`.

The match is a plain substring check — there are no boolean operators.
To run tests matching multiple keywords, run oxitest once per keyword or
use marker-based filtering instead.

## Filter by marker expression

Use `-m` with `and`, `or`, and `not` to build compound expressions over markers:

```console
$ oxitest -m "slow or integration"
$ oxitest -m "not slow"
$ oxitest -m "database and not slow"
```

Markers must be registered in `pyproject.toml` (see [Use markers](use-markers.md)).

## Run a specific file

Pass a file path as a positional argument to restrict discovery to that file:

```console
$ oxitest tests/test_math.py
```

## Run a specific test by node ID

Use the `path::test_name` syntax to run a single test:

```console
$ oxitest tests/test_math.py::test_add
```

For class-based tests, include the class name:

```console
$ oxitest tests/test_math.py::TestArithmetic::test_add
```

A node ID without brackets matches all parametrized variants. To target a
specific case, include the parameter ID:

```console
$ oxitest tests/test_math.py::test_add[negative]
```

## Run multiple specific tests

Pass several node IDs as positional arguments:

```console
$ oxitest tests/test_math.py::test_add tests/test_math.py::test_subtract
```

You can mix file paths and node IDs. File paths run all tests in that file;
node IDs run only the named test:

```console
$ oxitest tests/test_math.py::test_add tests/test_strings.py
```

This runs `test_add` from `test_math.py` and **all** tests from `test_strings.py`.

## Use the interactive fuzzy finder

Use `--fzf` to browse and select tests interactively:

```console
$ oxitest query tests --fzf
```

Press **Tab** to select multiple tests, then **Enter** to run them all.
Press **Ctrl-R** to debug the focused test. The header bar shows all
available keybindings.

## See also

- [Use markers](use-markers.md) — filter by marker with `-m`
- [Run affected tests](run-affected-tests.md) — run only tests affected by git changes
- [CLI reference](../reference/cli.md) — full list of command-line options
