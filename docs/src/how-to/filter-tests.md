# Filter Tests

!!! abstract "How-to"
    Run a targeted subset of your test suite using keyword expressions or file paths.

## Filter by keyword

Run only tests whose name contains a substring:

```console
$ oxitest -k add
```

This matches any test whose name or node ID contains `add`, e.g. `test_add`, `test_add_negative`.

## Combine keywords with logical operators

Use `and`, `or`, and `not` to build compound expressions:

```console
$ oxitest -k "add or subtract"
$ oxitest -k "math and not slow"
$ oxitest -k "not slow"
```

Expressions are matched against the full node ID (`path/to/test_file.py::test_function_name`).

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
