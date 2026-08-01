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

[tool.oxitest.doctest]                 # empty table = enable with defaults (scope = "public")
```

The bare table is enough to opt in — an empty `[tool.oxitest.doctest]` enables collection with `scope = "public"`. Coverage severity comes from the global `[tool.oxitest].strict` setting. **To disable coverage, drop the whole `[tool.oxitest.doctest]` table** — absence of the table means the rule is off.

### Strictness semantics

Coverage severity is controlled by the global `[tool.oxitest].strict` mode — the same dial that governs bare-assert and missing-mark-reason enforcement:

- **`strict` absent or `off`** — collection stays on but the coverage rule is **silent**. No diagnostics are emitted for coverage gaps.
- **`strict = "enforce"`** — every public subject missing an `Examples:` section (or with an empty one) surfaces as a Warning diagnostic. The run does not fail on coverage gaps.
- **`strict = "abort"`** — the same gaps surface as Error diagnostics and hard-fail the run at collection time. Analysis errors (scanner could not resolve an alias chain) also hard-fail under `abort`.

### Curating scope

The default `scope = "public"` scans every public subject under `testpaths`. For a targeted subset, pass a **list of entries** instead:

```toml
[tool.oxitest.doctest]
scope = [
    "src/mypkg/api.py",                       # every subject in the file
    "src/mypkg/util/",                        # every subject under the directory (trailing / required)
    "src/mypkg/other.py::PublicClass",        # one top-level symbol
    "src/mypkg/other.py::PublicClass::run",   # one method inside a class
]
```

The four entry shapes:

| Shape | Meaning |
|-------|---------|
| `"path/to/dir/"` | Directory prefix. Trailing `/` is required. |
| `"path/to/mod.py"` | Whole file. Every subject in the module. |
| `"path/to/mod.py::sym"` | One top-level function or class. |
| `"path/to/mod.py::Cls::method"` | One method inside a class. Bare `Cls` still matches the class only — list each method you want checked. |

Members are opt-in per method: `"Cls"` alone does not walk the class body, and there is no `Cls::*` wildcard. If a listed method doesn't exist at check time, the entry surfaces as a stale-entry diagnostic under `strict`.

**Edge cases.** Only top-level classes are supported — a `Cls::method` entry where `Cls` is nested inside another class silently drops to a stale-entry diagnostic. Ellipsis-body stubs (`def m(self): ...`, common in abstract classes and `@overload` chains) are filtered at lookup so `@overload`'d functions resolve to the real implementation; a Member entry naming an abstract-stub method surfaces as stale rather than a false-positive MissingHeader.

**Skip is subtractive, not additive.** `skip = ["file.py::Cls::method"]` only removes a subject that some `scope` entry already put into scope. A skip Member entry combined with a whole-file scope (`scope = ["file.py"]`) is a no-op — whole-file scope only enumerates top-level subjects, so there is no method subject to subtract; the skip entry becomes stale. Use `scope = ["file.py::Cls::method"]` first if you need the method in scope to then skip it (rare — usually just omit the entry entirely).

**Explicit list entries bypass the leading-underscore filter.** `scope = ["src/mypkg/mod.py::_helper"]` will cover `_helper`, because naming it explicitly is opt-in. The scalar `scope = "public"` still filters underscored names — the private-bypass only applies to list-form entries. Built-in filters (`norecursedirs`, the `python_files` glob, `conftest.py`) always win, so a symbol inside `test_*.py` or `conftest.py` stays out even if you list it.

### Skipping subjects

`skip` uses the same list grammar as `scope` and subtracts from the resolved subject set:

```toml
[tool.oxitest.doctest]
scope = "public"
skip  = [
    "src/mypkg/internal/",              # directory prefix
    "src/mypkg/lib.py::deprecated_helper",
]
```

Every entry shape valid in `scope` is valid in `skip`. Skipped subjects produce no coverage or analysis diagnostics.

### Stale entry detection

A **stale entry** is a scope entry — in either `scope` or `skip` — that can never match a coverage subject, under any invocation shape. `--affected`, `--lf`, `-E`, and explicit paths all agree on the same verdict, because staleness is a property of the entry, not of a particular run:

- A `Prefix` (`dir/`) or `File` (`f.py`) entry is stale only when its path does not exist on disk. Matching zero coverage subjects is not, by itself, stale — private-only modules, `test_*.py` files, and `conftest.py` all legitimately yield none, and that is never treated as a typo.
- A `Symbol` (`f.py::name`) or `Member` (`f.py::Cls::name`) entry is *additionally* stale when its file was scanned but the named symbol produced no coverage subject.

Stale entries surface via the global `strict` dial:

- **`strict` absent** — silent. Stale entries are not reported.
- **`strict = "enforce"`** — each stale entry surfaces as a Warning diagnostic.
- **`strict = "abort"`** — each stale entry surfaces as an Error and hard-fails collection.

The diagnostic text tells you which case applies:

```
<kind> entry '<entry>' names a path that does not exist (remove the entry or fix the path)
<kind> entry '<entry>' matched no coverage subjects (remove it, or check the symbol name)
```

This lets a strict project catch drift in its `scope` / `skip` config without a separate lint pass.

When enabled, oxitest scans all `.py` files in your test paths for
docstrings containing `>>>` interactive examples.

!!! warning "The `scope = "off"` scalar was removed"
    Earlier revisions accepted `scope = "off"` to disable coverage. That scalar is gone — the config parser now rejects it with a migration hint. **To opt out, delete the whole `[tool.oxitest.doctest]` table.** Absence of the table disables the rule; presence enables it with `scope = "public"` unless you override.

!!! note "Upgrading from `doctest_modules = true`"
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
