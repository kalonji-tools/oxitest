# CLI Reference

!!! abstract "Reference"
    Complete reference for all oxitest command-line options.

## Invocation

```text
oxitest [OPTIONS] [PATHS...]
```

`PATHS` is one or more files or directories to collect tests from. Defaults to the
current working directory when omitted.

## Options

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `-k` | — | `EXPR` | — | Filter tests by keyword expression. Only tests whose names contain the expression are run. |
| `--marker` | `-m` | `EXPR` | — | Filter tests by marker expression (`and`/`or`/`not` supported). Only tests carrying a matching mark are run. |
| `--verbose` | `-v` | flag | `false` | Enable verbose output. Prints each test name and result as it runs. Without `-v`, only failures are shown after the progress bar completes. |
| `-x` | — | flag | `false` | Stop immediately after the first test failure or error. Equivalent to `--maxfail 1`. Conflicts with `--maxfail`. |
| `--maxfail` | — | integer | `0` | Stop after `N` failures. `0` means unlimited. |
| `--tb` | — | `long\|short\|line\|no` | `short` | Traceback style on failure (see [Traceback styles](#traceback-styles)). |
| `--tips` | — | flag | `false` | Expand assertion tip output from a count to a full `file:line` list (see [Tips](#tips)). |
| `--warnings` | — | flag | `false` | Expand warning details. Without this flag, only a count is shown. With it, each warning is displayed in a box with the test function name and warning type/message. Warnings captured by `WarnCapture` or `oxitest.warns()` are excluded. |
| `--color` | — | `auto\|always\|never` | `auto` | Color output mode. `auto` detects TTY. `always` forces color (useful in pipes). `never` disables color. |
| `--serial` | — | flag | `false` | Run all tests in a single process. Disables parallel workers. Conflicts with `--workers`. |
| `--workers` | `-n` | `auto\|integer` | cpu count | Number of parallel worker processes. `auto` uses all available CPUs. Conflicts with `--serial`. |
| `--schedule` | — | `longest-first\|failed-first\|random` | — | Group scheduling strategy for parallel runs. Defaults to `longest-first`. |
| `--timeout` | — | integer (seconds) | — | Per-test timeout. Tests exceeding this are killed and marked failed. Overrides pyproject.toml `timeout`. |
| `--durations` | — | integer | — | Show the N slowest tests at end of run. |
| `--json` | — | `PATH` | — | Write CTRF-format JSON results to `PATH`. |
| `--junit-xml` | — | `PATH` | — | Write JUnit XML results to `PATH`. |
| `--failed` | — | `only\|first` | — | Failed-test mode. `only` runs just previously-failed tests. `first` runs failures before the rest. |
| `--strict` | — | `abort\|enforce` | — | Enforce strict conventions. Use `--strict=MODE` with `=` (bare `--strict` defaults to `abort`). Checks: bare assert, dict parametrize, missing mark reason, marker without description. `abort` exits with code 3 before tests run. `enforce` runs tests but turns violations into errors. |
| `--capture-environment` | — | flag | `false` | Print environment info (oxitest version, Python, rustc, OS) and exit with code 0. Useful for bug reports. |
| `--fixtures` | — | flag | `false` | List all registered fixtures and exit. Alias: `--fx`. |
| `--quiet` | `-q` | flag | `false` | Quiet output (minimal detail for `--fixtures`). |
| `--list` | — | flag | `false` | List collected tests and exit (no execution). |
| `--affected` | — | `REF` | — | Run only tests affected by git changes. Use `--affected=REF` with `=` (bare `--affected` uses the `affected_base` config value, or `HEAD`). |
| `--retries` | — | integer | — | Retry failed tests up to N times. |
| `--retries-delay` | — | integer (seconds) | — | Seconds to wait between retries. Has no effect without `--retries`. |

## Flag interactions

!!! info "Conflicting flags"
    Some flags contradict each other. Passing both produces a descriptive error
    and [exit code 4](exit-codes.md) before any tests run.

| Flag A | Flag B | Why they conflict |
|--------|--------|-------------------|
| `-x` | `--maxfail` | Both control when to stop after failures. Use one or the other. |
| `--verbose` / `-v` | `--quiet` / `-q` | Opposite output modes. Use one or the other. |
| `--serial` | `--workers` | Mutually exclusive execution modes. |
| `--serial` | `--schedule` | Schedule controls parallel worker ordering; no effect in serial mode. |
| `--retries-delay` | *(without `--retries`)* | Delay has no effect without retries. |

Example:

```console
$ oxitest -x --maxfail 5
error: -x and --maxfail both control when to stop after failures. Use one or the other.
```

## Traceback styles

The `--tb` option controls how failure tracebacks are rendered. Each mode shows
progressively less detail:

| Value | Behaviour |
|-------|-----------|
| `long` | Full call-chain frames (including oxitest internals), color-coded diff, and fix suggestions. |
| `short` | The failing source line, color-coded diff, and fix suggestions. Internal framework frames hidden. Default. |
| `line` | One compact line per failure: `STATUS  node_id  :lineno  message`. |
| `no` | Suppresses traceback output entirely. Only the summary count is shown. |

Example `--tb=short` output (default):

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

Example `--tb=long` output:

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

## Tips

When tests contain bare `assert` statements with no message (e.g. `assert result == expected`),
oxitest suggests adding assertion messages to improve failure output.

Without `--tips`, only a count of such assertions is shown after a passing run. With `--tips`,
the full `file:line` list of every bare assertion is printed instead.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All tests passed (or no tests were collected). |
| `1` | One or more tests failed or errored. |
| `2` | Run interrupted (e.g. `-x` or `--maxfail` reached). |
| `3` | Collection error or strict violations found when using `--strict=abort`. |
| `4` | Invalid CLI arguments — oxitest exits before running any tests. |

See the [Exit Codes](exit-codes.md) reference page for full details.
