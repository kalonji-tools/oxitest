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
[tool.oxitest]
strict = "enforce"                     # "off" | "enforce" | "abort" — controls coverage severity

[tool.oxitest.doctest]
scope = "public"                       # "public" | "all" (all is reserved for future; currently identical to public)
skip = ["tests/fixtures", "generated"] # path prefixes to exclude (optional; see below)
waivers = ".oxi-doctest-waivers"       # shrink-only ratchet file (optional; defaults to this name)
```

Present-with-defaults is enough: an empty `[tool.oxitest.doctest]` table enables collection. Coverage severity comes from the global `[tool.oxitest].strict` setting.

### Strictness semantics

Coverage severity is controlled by the global `[tool.oxitest].strict` mode — the same dial that governs bare-assert and missing-mark-reason enforcement:

- **`strict` absent or `off`** — collection stays on but the coverage rule is **silent**. No diagnostics are emitted for coverage gaps.
- **`strict = "enforce"`** — every public subject missing an `Examples:` section (or with an empty one) surfaces as a Warning diagnostic. The run does not fail on coverage gaps.
- **`strict = "abort"`** — the same gaps surface as Error diagnostics and hard-fail the run at collection time. Analysis errors (scanner could not resolve an alias chain) also hard-fail under `abort`.

### Waivers ratchet

Under `strict = "abort"`, real projects rarely start at zero coverage. The `waivers` file lets you acknowledge known-missing subjects without blocking the gate for the rest.

- **Plain text**, one dotted name per line, `#` comments allowed, no globs.
- Default path is `.oxi-doctest-waivers` at the repo root; override via `waivers = "..."`.
- Missing file = empty set (silent — the terminal state after full burn-down).
- **Waived subject that is still missing coverage** → downgraded to `Notice` (visible tech debt, not blocking).
- **Missing subject not in the waivers file** → severity per `strict` (`abort` → hard-fail).
- **Name in the waivers file that no longer needs coverage** → `Error` regardless of strict mode. This is the *shrink-only* invariant: entries can leave the file, never enter — regressions are caught immediately.
- **Subset run (explicit file path)** → the stale-entry check is skipped. When `oxitest` is invoked with an explicit file path, the entry may legitimately refer to a subject outside the scanned subset. Stale enforcement runs only on full-tree scans (invocations without a positional path argument).

Add an entry when you consciously defer coverage for a subject. Remove it when the docstring lands.

### Excluding path prefixes

Some directories under `testpaths` aren't part of your public API — test fixtures, generated stubs, vendored code. Add path prefixes to `skip` to exclude them from coverage scanning entirely (no subject enumeration, no alias walking, no diagnostics):

```toml
[tool.oxitest.doctest]
scope = "public"
skip = ["tests/fixtures", "generated"]
waivers = ".oxi-doctest-waivers"
```

Prefixes are matched relative to the project root using `starts_with` semantics (no globs). A prefix `tests/fixtures` matches `tests/fixtures/sample/mod.py` but not `tests/fixtures_utils/helper.py` — matching is component-wise, not substring. Skipped files produce no coverage or analysis diagnostics and don't need waivers entries.

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

**Test infrastructure is auto-excluded from public-subject coverage.**
Files named `conftest.py` (at any nesting level) and files matching
`python_files` (default `test_*.py`) are skipped from the coverage
rule's subject enumeration — their top-level definitions are fixture
registrations and test helpers by pytest/oxitest convention, not
public API. Their docstrings can still contain `>>>` blocks that get
collected and run as regular doctests; only the coverage rule ignores
them.

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
