# CLI Reference

!!! abstract "Reference"
    Complete reference for all oxitest command-line options.

Most flags have a `pyproject.toml` equivalent. See [Configuration](configuration.md) for the full key table.

## Invocation

```text
oxitest [SUBCOMMAND] [OPTIONS] [PATHS_OR_NODE_IDS...]
```

oxitest organises its features into subcommands. Running `oxitest` with no
subcommand is equivalent to `oxitest run`.

| Subcommand | Purpose |
|------------|---------|
| `run` | Run tests (default when no subcommand is given) |
| `debug` | Run tests under an interactive debugger |
| `query` | Inspect tests, fixtures, marks, helpers, or plugins without running them |
| `inspect` | Interactive TUI explorer for tests, fixtures, marks, and other project metadata |
| `env` | Print environment information and exit |
| `fixtures` | **Deprecated.** Use `oxitest query fixtures` instead. |

Positional arguments accept **file paths**, **directories**, or **node IDs**:

- A plain path (`tests/test_math.py` or `tests/`) limits file discovery to that path.
- A **node ID** (`tests/test_math.py::test_add`) targets a specific test. The `::` separator tells oxitest this is a node ID, not a path. The file portion is extracted automatically for collection scoping.
- Class-based node IDs use double separators: `tests/test_math.py::TestSuite::test_add`.
- Mixing is allowed: `oxitest run tests/test_a.py::test_foo tests/test_b.py` runs one specific test from file A and all tests from file B.

Defaults to the current working directory when omitted (applies to `run`, `debug`, and
`query tests`/`query fixtures`).

---

## `oxitest run`

Run the test suite. This is the default subcommand.

```text
oxitest run [OPTIONS] [PATHS_OR_NODE_IDS...]
oxitest [OPTIONS] [PATHS_OR_NODE_IDS...]        # equivalent
```

### Filtering

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | Filter tests using the query DSL (e.g. `name(foo)`, `mark(slow) & !mark(integration)`). See [Query DSL](#query-dsl). |
| `--failed` | — | `only\|first` | — | Failed-test mode. `only` runs just previously-failed tests. `first` runs failures before the rest. |
| `--lf` | — | flag | — | Alias for `--failed only`. Run only previously-failed tests. |
| `--ff` | — | flag | — | Alias for `--failed first`. Run previously-failed tests first. |
| `--affected` | — | `REF` | — | Run only tests affected by git changes. Use `--affected=REF` with `=` (bare `--affected` uses the `affected_base` config value, or `HEAD`). |

### Execution

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-x` | — | flag | `false` | Stop immediately after the first test failure or error. Equivalent to `--maxfail 1`. Conflicts with `--maxfail`. |
| `--maxfail` | — | integer | `0` | Stop after `N` failures. `0` means unlimited. |
| `--serial` | — | flag | `false` | Run all tests in a single process. Disables parallel workers. Conflicts with `--workers`. |
| `--workers` | `-n` | `auto\|integer` | cpu count | Number of parallel worker processes. `auto` uses all available CPUs. Conflicts with `--serial`. |
| `--schedule` | — | `longest-first\|failed-first\|random` | — | Group scheduling strategy for parallel runs. Defaults to `longest-first`. |
| `--timeout` | — | integer (seconds) | — | Per-test timeout. Tests exceeding this are killed and marked failed. Overrides pyproject.toml `timeout`. |
| `--retries` | — | integer | — | Retry failed tests up to N times. |
| `--strict` | — | `abort\|enforce` | — | Enforce strict conventions. Use `--strict=MODE` with `=` (bare `--strict` defaults to `abort`). Checks: bare assert, dict parametrize, missing mark reason, marker without description. `abort` exits with code 3 before tests run. `enforce` runs tests but turns violations into errors. |

### Output

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. `-v` or `--verbose` sets `detailed`. `-vv` or `--verbose=full` sets `full`. |
| `--quiet` | `-q` | flag | `false` | Quiet output. |
| `--tb` | — | `detail\|line\|no` | `detail` | Traceback style on failure (see [Traceback styles](#traceback-styles)). |
| `--show-locals` | — | flag | `false` | Show local variable values in the failing frame. Requires `--tb=detail`. |
| `--show-internals` | — | flag | `false` | Show internal oxitest framework frames in tracebacks. Requires `--tb=detail`. |
| `--tips` | — | flag | `false` | Expand assertion tip output from a count to a full `file:line` list (see [Tips](#tips)). |
| `--warnings` | — | flag | `false` | Expand warning details. Without this flag, only a count is shown. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. `auto` detects TTY. `always` forces color (useful in pipes). `never` disables color. |
| `--durations` | — | integer | — | Show the N slowest tests and N slowest fixtures at end of run. |
| `--keep-tmp` | — | `failed\|always` | — | Keep temporary directories created by `TempDir`. `failed` keeps them only for failed tests; `always` keeps them unconditionally. |
| `--collection-profile` | — | flag | `false` | Print per-file prescan and collection timing breakdown to stderr. Useful for diagnosing slow collection. |

### Reports

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--json` | — | `PATH` | — | Write CTRF-format JSON results to `PATH`. |
| `--junit-xml` | — | `PATH` | — | Write JUnit XML results to `PATH`. |
| `--cov` | — | flag | `false` | Enable coverage collection via coverage.py. Requires the `coverage` package. |
| `--cov-report` | — | `term\|html\|xml\|json\|none` | `term` | Coverage report format. Requires `--cov`. |

---

## `oxitest debug`

Run tests under an interactive debugger. Implies `--serial`, `--show-internals`,
and no timeout. Post-mortem mode (default) also implies `--maxfail 1`.

```text
oxitest debug [OPTIONS] [PATHS_OR_NODE_IDS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--always` | — | flag | `false` | Pause the debugger before every test (trace mode). Without `--always`, the debugger only activates on test failure (post-mortem mode). |
| `-E` | — | `EXPR` | — | Filter tests using the query DSL. |
| `--failed` | — | `only\|first` | — | Failed-test mode. |
| `--lf` | — | flag | — | Alias for `--failed only`. |
| `--ff` | — | flag | — | Alias for `--failed first`. |
| `--affected` | — | `REF` | — | Run only tests affected by git changes. |
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. |
| `--quiet` | `-q` | flag | `false` | Quiet output. |
| `--tb` | — | `detail\|line\|no` | `detail` | Traceback style. |
| `--show-locals` | — | flag | `false` | Show local variable values in the failing frame. |
| `--keep-tmp` | — | `failed\|always` | — | Keep temporary directories. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

See [Debug tests](../how-to/debug-tests.md) for usage examples.

---

## `oxitest query`

Inspect project resources — tests, fixtures, marks, helpers, or plugins — and
exit without running any tests.

```text
oxitest query <resource> [OPTIONS] [PATHS...]
```

The `<resource>` argument selects what to inspect:

| Resource | What it lists | Requires Python |
|----------|---------------|-----------------|
| `tests` | Collected test items | Yes (collection) |
| `fixtures` | Registered fixtures | Yes |
| `marks` | All marks used in the project | No (Rust AST) |
| `helpers` | Conftest helper namespaces | No (Rust AST) |
| `plugins` | Registered plugins and protocols | Yes |

Resources that use Rust AST (`marks`, `helpers`) are instant — they never
invoke Python. `tests`, `fixtures`, and `plugins` require Python.

### Common flags

These flags apply to all resources:

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | DSL filter expression. See [Query DSL](#query-dsl). |
| `--fzf` | — | flag | `false` | Open results in an interactive fuzzy-finder. For tests: Tab to multi-select, Enter to run selected, Ctrl-R to debug focused item. |
| `--detail` | — | `ID` | — | Show a single-item detail card for the given identifier. |
| `--format` | — | `jsonl` | — | Output as JSON Lines (one JSON object per result). |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

### `query tests`

```text
oxitest query tests [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | DSL filter (see [Query DSL](#query-dsl)). Predicates: `name()`, `source()`, `mark()`. |
| `--count` | — | flag | `false` | Show only the total test count. Fast: uses Rust-side prescan without invoking Python. |
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. `-v` shows marks and fixtures per test. `-vv` groups parametrize cases with expanded values. |
| `--format` | — | `jsonl` | — | Output as JSON Lines. |
| `--fzf` | — | flag | `false` | Interactive fuzzy-finder. |
| `--detail` | — | `ID` | — | Show detail card for a single test. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

!!! tip
    `oxitest query tests --count` never invokes Python. It uses the Rust-side
    prescan to count tests instantly, even in large projects.

### `query fixtures`

List all registered fixtures and exit. Use `--tree` to visualise the dependency
graph.

```text
oxitest query fixtures [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--tree` | — | flag | `false` | Show fixture dependency tree instead of a flat list. Visualises which fixtures depend on which. Detects circular dependencies. |
| `-E` | — | `EXPR` | — | DSL filter (see [Query DSL](#query-dsl)). Predicates: `name()`, `shared()`, `autouse()`, `async()`. |
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. With `--tree`: `-v` adds tags (`shared`, `async`, `autouse`); `-vv` also adds origin (`conftest.py` path). |
| `--quiet` | `-q` | flag | `false` | Quiet output (minimal detail). |
| `--format` | — | `jsonl` | — | Output as JSON Lines. |
| `--fzf` | — | flag | `false` | Interactive fuzzy-finder. |
| `--detail` | — | `ID` | — | Show detail card for a single fixture. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

#### Fixture tree

`oxitest query fixtures --tree` renders all fixtures as a dependency tree. Each
fixture is a node; arrows point to its dependencies. Useful for understanding
fixture relationships and debugging circular dependencies.

```console
$ oxitest query fixtures --tree
db
└── config

── 2 fixtures
```

Use `-E` to filter which fixtures appear as roots:

```console
$ oxitest query fixtures --tree -E 'name(db)'
db
└── config

── 1 of 2 fixtures
```

Verbosity controls the amount of detail per node:

| Level | Shows |
|-------|-------|
| *(default)* | Fixture names only. |
| `-v` | Names + tags (`shared`, `async`, `autouse`). |
| `-vv` | Names + tags + origin (`conftest.py` path). |

When a circular dependency is detected, `oxitest query fixtures --tree` prints
an error and exits with a non-zero exit code:

```console
$ oxitest query fixtures --tree
error: Circular fixture dependency: a -> b -> a
```

### `query marks`

List all marks used across test files. Uses Rust AST — no Python required.

```text
oxitest query marks [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | DSL filter. Predicate: `name()`. |
| `--format` | — | `jsonl` | — | Output as JSON Lines. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

### `query helpers`

List all conftest helper namespaces. Uses Rust AST — no Python required.

```text
oxitest query helpers [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | DSL filter. Predicate: `name()`. |
| `--format` | — | `jsonl` | — | Output as JSON Lines. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

### `query plugins`

List all registered plugins and the protocols they implement.

```text
oxitest query plugins [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | DSL filter. Predicate: `name()`, `protocol()`. |
| `--verbose` | `-v` | flag | `false` | Show detailed protocol information. |
| `--quiet` | `-q` | flag | `false` | Suppress header output. |
| `--format` | — | `jsonl` | — | Output as JSON Lines. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

### Query DSL

The `-E '<expr>'` flag filters results using a small predicate DSL. Predicates
vary by resource but the syntax is consistent:

| Predicate | Resources | Matches when… |
|-----------|-----------|---------------|
| `name(pat)` | all | name contains `pat` (substring, case-insensitive) |
| `source(pat)` | tests, fixtures, helpers | file path or node ID contains `pat` |
| `mark(name)` | tests | test has the given mark |
| `async()` | tests, fixtures | test or fixture is an async function |
| `shared()` | fixtures | fixture is declared `shared=True` |
| `autouse()` | fixtures | fixture is declared `autouse=True` |
| `used_in(id)` | marks | mark is applied to a test matching `id` |
| `protocol(p)` | plugins | plugin implements protocol `p` |

Expressions can be combined with `and`, `or`, `not`, and parentheses:

```text
-E 'mark(slow) and not source(test_legacy)'
-E 'scope(session) or autouse()'
```

---

## `oxitest inspect`

Interactive terminal UI for exploring test project metadata — tests, fixtures,
marks, conftests, plugins, and helpers — without running any tests.

```text
oxitest inspect [NAME] [OPTIONS]
```

### Positional arguments

| Argument | Description |
|----------|-------------|
| `NAME` | Jump directly to a node whose name contains this string. If one match is found, the TUI opens on that node's list. If multiple matches are found, a disambiguation screen is shown. If no match is found, the TUI opens on the Home screen. |

### Startup filters

These flags narrow the data loaded into the TUI before it starts. They use the
same filter semantics as `oxitest run`.

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-E` | — | `EXPR` | — | Filter tests using the query DSL before building the graph. See [Query DSL](#query-dsl). |
| `--affected` | — | `REF` | — | Limit test files to those affected by git changes relative to `REF`. Use `--affected=REF` with `=`. Bare `--affected` uses the `affected_base` config value. |
| `--lf` | — | flag | — | Show only previously-failed tests. |
| `--ff` | — | flag | — | Show previously-failed tests before others. |

### Key bindings

| Key | Action |
|-----|--------|
| `j` / `Down` | Move cursor down |
| `k` / `Up` | Move cursor up |
| `Space` / `l` / `Right` | Navigate into selected item (or expand/collapse a parametrized group on the test list) |
| `Backspace` / `Left` | Go back to previous screen |
| `h` | Open session history screen |
| `/` | Enter search mode — type to filter by name (substring) or DSL expression |
| `Esc` (in search mode) | Exit search mode and clear the query |
| `?` | Toggle help overlay |
| `q` / `Esc` (in normal mode) | Quit |
| `Ctrl+C` | Quit (works in any mode) |

### Progressive loading

`oxitest inspect` uses a two-phase loading model:

1. **Phase 1 (instant)** — Tests, marks, and helpers are extracted from the
   Rust AST without starting a Python session. The TUI becomes interactive
   immediately.
2. **Phase 2 (background)** — A background thread initialises the Python
   session and collects fixtures and plugins. These are merged into the graph
   when ready. If the Python session fails, the TUI continues with whatever
   data phase 1 collected.

See [Use oxitest inspect](../how-to/use-inspect.md) for usage examples.

---

## `oxitest env`

Print environment information (oxitest version, Python, rustc, OS) and exit
with code 0. Useful for bug reports.

```text
oxitest env
```

No flags.

---

## Deprecated subcommands

### `oxitest fixtures` (deprecated)

!!! warning "Deprecated"
    `oxitest fixtures` is deprecated and will be removed in a future release.
    Use [`oxitest query fixtures`](#query-fixtures) instead.

`oxitest fixtures` still works but prints a deprecation warning to stderr:

```text
Warning: 'oxitest fixtures' is deprecated and will be removed in a future release. Use 'oxitest query fixtures' instead.
```

The command is hidden from `--help` output. Migrate by replacing:

```bash
# Old (deprecated)
oxitest fixtures

# New
oxitest query fixtures
```

---

## Flag interactions

!!! info "Conflicting flags"
    Some flags contradict each other. Passing both produces a descriptive error
    and [exit code 4](exit-codes.md) before any tests run.

| Flag A | Flag B | Why they conflict |
|--------|--------|-------------------|
| `-x` | `--maxfail` | Both control when to stop after failures. Use one or the other. |
| `-v`/`-vv` | `--verbose=LEVEL` | Both set verbosity. Use short form or long form, not both. |
| `--serial` | `--workers` | Mutually exclusive execution modes. |
| `--serial` | `--schedule` | Schedule controls parallel worker ordering; no effect in serial mode. |
| `--show-locals` | *(without `--tb=detail`)* | `--show-locals` only applies to the `detail` traceback style. |
| `--show-internals` | *(without `--tb=detail`)* | `--show-internals` only applies to the `detail` traceback style. |

Note: `-v -q` is valid — quiet trumps verbose silently.

Example:

```console
$ oxitest -x --maxfail 5
error: -x and --maxfail both control when to stop after failures. Use one or the other.
```

---

## Traceback styles

The `--tb` option controls how failure tracebacks are rendered:

| Value | Behaviour |
|-------|-----------|
| `detail` | The failing source line, color-coded diff, and fix suggestions. Internal framework frames hidden unless `--show-internals` is set. Default. |
| `line` | One compact line per failure: `STATUS  node_id  :lineno  message`. |
| `no` | Suppresses traceback output entirely. Only the summary count is shown. |

Use `--show-locals` to include local variable values in the failing frame.
Use `--show-internals` to include internal oxitest framework frames.

Example `--tb=detail` output (default):

```
FAILED  tests/test_math.py::test_add
        ┌─ tests/test_math.py:4
        │
      4 │    assert x == y
        │
        ├─  diff
        │  - left:  41
        │  + right: 42
        └─ why:   values should match
```

Example `--tb=detail --show-internals` output:

```
FAILED  tests/test_math.py::test_divide
        ┌─ tests/test_math.py:2
        │
        ├─  frames
        │    tests/test_math.py:8  test_divide
        │      result = compute(1, 0)
        │    tests/test_math.py:5  compute
        │      return helper(a, b)
        │    tests/test_math.py:2  helper
        │      return a / b
        │
        └─ ZeroDivisionError: division by zero
```

Example `--tb=line` output:

```
FAILURES ════════════════════════════════════════════════════════════════════════
FAILED  tests/test_math.py::test_add     :4   expected 3, got 5
ERROR   tests/test_math.py::test_divide  :2   ZeroDivisionError: division by zero
```

---

## Tips

When tests contain bare `assert` statements with no message (e.g. `assert result == expected`),
oxitest suggests adding assertion messages to improve failure output.

Without `--tips`, only a count of such assertions is shown after a passing run. With `--tips`,
the full `file:line` list of every bare assertion is printed instead.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All tests passed (or no tests were collected). |
| `1` | One or more tests failed or errored. |
| `2` | Run interrupted (e.g. `-x` or `--maxfail` reached). |
| `3` | Collection error or strict violations found when using `--strict=abort`. |
| `4` | Invalid CLI arguments — oxitest exits before running any tests. |

See the [Exit Codes](exit-codes.md) reference page for full details.
