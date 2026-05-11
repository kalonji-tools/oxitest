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
| `--verbose` | `-v` | flag | `false` | Enable verbose output. Prints each test name as it runs. |
| `-x` | — | flag | `false` | Stop immediately after the first test failure or error. |
| `--maxfail` | — | integer | `0` | Stop after `N` failures. `0` means unlimited. |
| `--tb` | — | `short\|line\|no` | `short` | Traceback style on failure (see [Traceback styles](#traceback-styles)). |
| `--tips` | — | flag | `false` | Expand assertion tip output from a count to a full `file:line` list (see [Tips](#tips)). |
| `--warnings` | — | flag | `false` | Enable display of Python warnings captured during test execution. |
| `--no-color` | — | flag | `false` | Disable ANSI color in output. |
| `--serial` | — | flag | `false` | Run all tests in a single process. Disables parallel workers. Conflicts with `--workers`. |
| `--workers` | — | integer | cpu count | Number of parallel worker processes. Conflicts with `--serial`. |
| `--durations` | — | integer | — | Show the N slowest tests at end of run. `0` disables. |
| `--json` | — | `PATH` | — | Write CTRF-format JSON results to `PATH`. |
| `--lf` | — | flag | `false` | Run only tests that failed on the last run. Conflicts with `--ff`. |
| `--ff` | — | flag | `false` | Run failed tests first, then the rest. Conflicts with `--lf`. |
| `--strict` | — | `abort\|enforce` | — | Enforce strict conventions. Use `--strict=MODE` with `=` (bare `--strict` defaults to `abort`). Checks: bare assert, dict parametrize, missing mark reason, marker without description. `abort` exits with code 3 before tests run. `enforce` runs tests but turns violations into errors. |

## Traceback styles

The `--tb` option controls how failure tracebacks are rendered.

| Value | Behaviour |
|-------|-----------|
| `short` | Shows the failing source line together with the operand values. Default. |
| `line` | Shows only the file and line number of the failure; no source or operand detail. |
| `no` | Suppresses traceback output entirely. |

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
