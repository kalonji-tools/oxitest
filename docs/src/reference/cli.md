# CLI Reference

!!! abstract "Reference"
    Complete reference for all oxitest command-line options.

## Invocation

```text
oxitest [SUBCOMMAND] [OPTIONS] [PATHS...]
```

oxitest organises its features into subcommands. Running `oxitest` with no
subcommand is equivalent to `oxitest run`.

| Subcommand | Purpose |
|------------|---------|
| `run` | Run tests (default when no subcommand is given) |
| `debug` | Run tests under an interactive debugger |
| `list` | List collected tests without running them |
| `fixtures` | List or visualise registered fixtures |
| `env` | Print environment information and exit |

`PATHS` is one or more files or directories to collect tests from. Defaults to
the current working directory when omitted (applies to `run`, `debug`, `list`,
and `fixtures`).

---

## `oxitest run`

Run the test suite. This is the default subcommand.

```text
oxitest run [OPTIONS] [PATHS...]
oxitest [OPTIONS] [PATHS...]        # equivalent
```

### Filtering

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-k` | — | `EXPR` | — | Filter tests by keyword expression. Only tests whose names contain the expression are run. |
| `--marker` | `-m` | `EXPR` | — | Filter tests by marker expression (`and`/`or`/`not` supported). |
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
| `--durations` | — | integer | — | Show the N slowest tests at end of run. |
| `--keep-tmp` | — | `failed\|always` | — | Keep temporary directories created by `TempDir`. `failed` keeps them only for failed tests; `always` keeps them unconditionally. |

### Reports

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--json` | — | `PATH` | — | Write CTRF-format JSON results to `PATH`. |
| `--junit-xml` | — | `PATH` | — | Write JUnit XML results to `PATH`. |

---

## `oxitest debug`

Run tests under an interactive debugger. Implies `--serial`, `--show-internals`,
and no timeout. Post-mortem mode (default) also implies `--maxfail 1`.

```text
oxitest debug [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--always` | — | flag | `false` | Pause the debugger before every test (trace mode). Without `--always`, the debugger only activates on test failure (post-mortem mode). |
| `-k` | — | `EXPR` | — | Filter tests by keyword expression. |
| `--marker` | `-m` | `EXPR` | — | Filter tests by marker expression. |
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

## `oxitest list`

List collected tests and exit without running them.

```text
oxitest list [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-k` | — | `EXPR` | — | Filter tests by keyword expression. |
| `--marker` | `-m` | `EXPR` | — | Filter tests by marker expression. |
| `--affected` | — | `REF` | — | Filter to tests affected by git changes. |
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. `-v` shows marks and fixtures per test. `-vv` groups parametrize cases with expanded values. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

---

## `oxitest fixtures`

List all registered fixtures and exit. Use `--tree` to visualise the dependency
graph.

```text
oxitest fixtures [OPTIONS] [PATHS...]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--tree` | — | flag | `false` | Show fixture dependency tree instead of a flat list. Visualises which fixtures depend on which. Detects circular dependencies. |
| `--verbose` | `-v` | `LEVEL` | `normal` | Verbosity level. With `--tree`: `-v` adds tags (`shared`, `async`, `autouse`); `-vv` also adds origin (`conftest.py` path). |
| `--quiet` | `-q` | flag | `false` | Quiet output (minimal detail). |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. |

### Fixture tree

`oxitest fixtures --tree` renders all fixtures as a dependency tree. Each
fixture is a node; arrows point to its dependencies. Useful for understanding
fixture relationships and debugging circular dependencies.

```console
$ oxitest fixtures --tree
db
└── config

── 2 fixtures
```

Use `-k` to filter which fixtures appear as roots:

```console
$ oxitest fixtures --tree -k db
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

When a circular dependency is detected, `oxitest fixtures --tree` prints an
error and exits with a non-zero exit code:

```console
$ oxitest fixtures --tree
error: Circular fixture dependency: a -> b -> a
```

---

## `oxitest env`

Print environment information (oxitest version, Python, rustc, OS) and exit
with code 0. Useful for bug reports.

```text
oxitest env
```

No flags. Version information is available here rather than via a `--version`
flag.

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
