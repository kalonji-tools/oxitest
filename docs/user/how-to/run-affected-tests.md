# Run Affected Tests

!!! abstract "How-to"
    Run only the tests that are affected by your current git changes, skipping
    everything that could not have been broken by them.

## Basic usage

```console
$ oxitest --affected
```

oxitest runs `git diff --name-only` against the default base ref (configured via
`affected_base`, or `HEAD` when no base is configured), classifies the changed
files, and runs only the test files that are affected by those changes.

If no files have changed relative to the base ref, oxitest prints a summary to
stderr and exits with code 0:

```
affected: 0 of 12 test files selected [base: main]
  (no files changed)
```

When files changed but none are Python, the summary explains why:

```
affected: 0 of 12 test files selected [base: main]
  (3 files changed, 3 non-Python ignored)
```

## Specify a base ref

Pass a git ref after `=` to compare against a specific branch, tag, or commit:

```console
$ oxitest --affected=main
$ oxitest --affected=origin/main
$ oxitest --affected=HEAD~3
$ oxitest --affected=v1.2.0
```

!!! note "Use `=` to attach the ref"
    `--affected` requires the `=` syntax when supplying a ref.
    `--affected main` (with a space) is not valid — the ref must be joined with `=`.

## Configure a default base ref

Set `affected_base` in `pyproject.toml` to avoid repeating the ref on every
invocation:

```toml
[tool.oxitest]
affected_base = "main"
```

With this in place, `oxitest --affected` behaves as if you had written
`oxitest --affected=main`. A ref supplied on the command line always takes
precedence over `affected_base`.

## How it works

oxitest applies a four-step pipeline after collecting all test files:

1. **Git diff** — runs `git diff --name-only <base>` from the project rootdir
   to get the list of changed file paths.

2. **Classify** — splits the changed files into categories:
   - `pyproject.toml` changed → skip filtering, run all tests.
   - Declaration files — `__fixtures__.py` and `__init__.py` — tracked
     separately.
   - All other `.py` files — treated as source/test files.
   - Non-`.py` files (`.md`, `.toml`, etc.) — ignored.

3. **Direct matches** — any test file that is itself in the changed set is
   included immediately.

4. **Import graph analysis** — for each remaining test file, oxitest parses
   its `import` and `from … import` statements using a Rust-side AST parser
   (`rustpython-parser`), making import analysis roughly 20x faster than an
   equivalent Python `ast` walk. It then checks whether any changed source file
   appears as an imported module. Test files that import a changed module (or
   any of its parent packages) are included.

Declaration files are handled as a special case: when a `__fixtures__.py` or an
`__init__.py` changes, every test file in the same directory subtree is
included, because a fixture declared there is visible to all tests below it.

A declaration file at the rootdir therefore selects every test — through the
subtree rule, not through the run-all path that `pyproject.toml` takes.

## What counts as affected

| Changed file | Tests included |
|---|---|
| A test file itself | That test file |
| A source file | Test files that import it (directly or via a parent package) |
| A `__fixtures__.py` or `__init__.py` | All test files in the same directory and its subdirectories |
| `pyproject.toml` | All test files (full run) |
| Non-Python file | None (ignored) |

## Limitations

The import analysis is purely static — it reads `import` statements as written
in the source. It does not detect:

- **Dynamic imports** (`importlib.import_module(name)`, `__import__(name)`) where
  the module name is computed at runtime.
- **Relative imports** (`from . import utils`) — these are intentionally skipped
  because the same-package files are already caught by direct-change detection.
- **Indirect imports** — if `test_a.py` imports `helpers.py` which imports
  `utils.py`, and only `utils.py` changes, `test_a.py` is not included unless
  it also imports `utils` directly or via a parent package.

When in doubt, run the full suite. The flag is an acceleration tool for inner
loops, not a substitute for a full CI run.

## Example workflow

During a feature branch development cycle:

```console
# See what has changed relative to the base branch
$ git diff --name-only main

# Run only tests affected by those changes
$ oxitest --affected=main

# After making fixes, run affected again for quick feedback
$ oxitest --affected=main

# Before merging, run the full suite to confirm nothing is broken
$ oxitest
```

In CI you might run affected tests on every push to a feature branch, and the
full suite only on pull requests targeting the main branch.

## See also

- [Filter tests](filter-tests.md) — narrow tests by keyword or file path
- [Use the test cache](use-test-cache.md) — re-run failures first with `--failed`
- [Configuration reference](../reference/configuration.md) — full list of `pyproject.toml` keys including `affected_base`
