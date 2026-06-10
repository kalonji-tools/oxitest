# Filter Tests

!!! abstract "How-to"
    Run a targeted subset of your test suite using the query DSL,
    file paths, or node IDs.

A **node ID** is a test's unique address: `path/to/file.py::test_name`. For class methods: `path/to/file.py::ClassName::test_method`. For parametrized cases: `path/to/file.py::test_name[case_id]`.

## Filter by name

Use `-E` with the `name()` predicate to run tests whose name contains a
substring:

```console
$ oxitest -E 'name(add)'
```

This matches any test whose function name contains `add`, e.g. `test_add`,
`test_add_negative`.

Combine predicates with `&` (and), `|` (or), and `!` (not):

```console
$ oxitest -E 'name(add) | name(repeat)'
$ oxitest -E '!name(slow)'
```

## Filter by marker

Use `-E` with the `mark()` predicate to run tests by marker:

```console
$ oxitest -E 'mark(slow)'
$ oxitest -E 'mark(slow) & !mark(integration)'
$ oxitest -E 'mark(database) | mark(integration)'
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

- [Use markers](use-markers.md) — register and apply custom markers
- [Run affected tests](run-affected-tests.md) — run only tests affected by git changes
- [CLI reference](../reference/cli.md) — full list of command-line options
