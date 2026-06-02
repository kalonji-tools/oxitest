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

## Combine a file path with `-k`

Narrow further by adding a keyword filter on top of a specific file:

```console
$ oxitest tests/test_math.py -k add
```

This runs only tests inside `tests/test_math.py` whose name contains `add`.

## See also

- [Use markers](use-markers.md) — filter by marker with `-m`
- [Run affected tests](run-affected-tests.md) — run only tests affected by git changes
- [CLI reference](../reference/cli.md) — full list of command-line options
