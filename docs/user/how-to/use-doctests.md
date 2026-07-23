# Use doctests

!!! abstract "How-to"
    Run interactive `>>>` examples embedded in Python docstrings as tests.

## Enabling doctest collection

Doctests are off by default. Enable them with the `--doctest-modules` flag:

```console
$ oxitest --doctest-modules
```

Or permanently in `pyproject.toml`:

```toml
[tool.oxitest.doctest]
scope = "public"      # target public subjects (names in __all__, or top-level non-underscore)
strictness = "off"    # "off" | "warn" (M1) | "required" (M2 — allowlist ratchet)
```

Present-with-defaults is enough: an empty `[tool.oxitest.doctest]` table enables collection with `scope = "public"`, `strictness = "warn"`. Set `scope = "off"` to disable entirely.

When enabled, oxitest scans all `.py` files in your test paths for
docstrings containing `>>>` interactive examples.

!!! note "Migration from `doctest_modules = true`"
    The legacy `doctest_modules` boolean at `[tool.oxitest]` was replaced by the `[tool.oxitest.doctest]` sub-table. Runs with the old key hard-error at config load — replace with the shape above.

## How doctests work

A doctest is a code example in a docstring that uses the Python
interactive prompt syntax:

```python
--8<-- "python/tests/docs/how-to/test_doctests_example.py:doctest-example"
```

Each docstring with `>>>` examples becomes a single test item. The
examples run sequentially and share state within the same docstring —
variables set by earlier examples are visible to later ones.

## How doctests appear in output

Doctest items use a `<doctest>` prefix in their node IDs to distinguish
them from regular tests:

```
mylib.py::<doctest>mylib.add        PASSED
mylib.py::<doctest>mylib.Calculator PASSED
```

The automatic `doctest` marker is applied to all doctest items. Use it
to filter:

```console
$ oxitest -E "mark(doctest)"       # run only doctests
$ oxitest -E "!mark(doctest)"      # exclude doctests
```

## Combining with regular tests

Doctests and regular `test_*` functions coexist in the same run.
If a file has both a `test_add` function and a docstring with `>>>`
examples, both are collected:

```console
$ oxitest --doctest-modules tests/
collected 5 items

tests/test_math.py::test_add                    PASSED
tests/test_math.py::test_subtract               PASSED
src/mylib.py::<doctest>mylib.add                 PASSED
src/mylib.py::<doctest>mylib.Calculator.multiply PASSED
src/mylib.py::<doctest>mylib                     PASSED
```

## Where doctests are discovered

oxitest finds doctests in:

- **Module-level** docstrings (the docstring at the top of a `.py` file)
- **Function** and **async function** docstrings
- **Class** docstrings
- **Method** docstrings (including async methods)

Discovery uses Rust AST analysis — files without `>>>` examples are
skipped without importing them into Python.

## Failure diagnostics

When a doctest fails, the output shows the expected vs actual result:

```
FAILED mylib.py::<doctest>mylib.broken

Failed example:
    1 + 1
Expected:
    3
Got:
    2
```

## Limitations

Doctests are documentation-first, not test-first. They intentionally
have a simpler execution model than regular tests:

- **No fixture injection** — doctests cannot request oxitest fixtures.
  They must import everything they need.
- **No parametrize** — each docstring runs as a single test case.
- **No marks** — you cannot apply `@mark.skip` or `@mark.xfail` to
  doctests. Use `# doctest: +SKIP` on individual examples instead.
- **No assertion rewriting** — doctests use output comparison, not
  `assert` statements.
- **Exempt from strict mode** — bare `assert` checks do not apply to
  docstrings since they contain documentation examples, not production
  test code.
