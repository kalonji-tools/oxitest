# JSON Output Format

!!! abstract "Reference"
    Complete reference for the oxitest CTRF JSON output format.

oxitest writes test results to a JSON file in [CTRF](https://ctrf.io) (Common Test Results Format)
when `--json` is passed. CI dashboards, test reporting tools, and custom integrations accept this format.

## Usage

```console
$ oxitest --json results.json
```

oxitest writes the file at the end of the run. If the file already exists, oxitest overwrites it.

**The file is always written.** If `--json PATH` was passed, `PATH` exists once oxitest exits,
whatever the exit code — including runs that abort before a single test executes. A missing
artifact therefore means the job never started, never that it started and failed.

## Schema

```json
{
  "results": {
    "tool": {
      "name": "oxitest"
    },
    "summary": {
      "tests": 42,
      "passed": 38,
      "failed": 2,
      "skipped": 2,
      "other": 0
    },
    "tests": [
      {
        "name": "tests/test_example.py::test_add",
        "status": "passed",
        "duration": 12.5
      },
      {
        "name": "tests/test_example.py::test_divide",
        "status": "failed",
        "duration": 8.1,
        "message": "ZeroDivisionError: division by zero"
      }
    ]
  }
}
```

## Field reference

| Field | Type | Description |
|-------|------|-------------|
| `results.tool.name` | string | Always `"oxitest"` |
| `results.summary.tests` | integer | Total number of tests in the run |
| `results.summary.passed` | integer | Tests that passed (includes non-strict `xpass`) |
| `results.summary.failed` | integer | Tests that failed (includes `error`, `timeout`, strict `xpass`) |
| `results.summary.skipped` | integer | Tests that were skipped (includes `xfail`) |
| `results.summary.other` | integer | Always `0` |
| `tests[].name` | string | Test node ID, e.g. `tests/test_foo.py::test_bar` |
| `tests[].status` | string | One of `"passed"`, `"failed"`, `"skipped"` |
| `tests[].duration` | float | Wall-clock time in milliseconds |
| `tests[].message` | string | Failure message. Present only for `failed` outcomes with a non-empty message; omitted for `passed`, `skipped`, and failures with no message text. |

## Status mapping

| oxitest outcome | CTRF status |
|-----------------|-------------|
| `Passed` | `passed` |
| `Warned` | `passed` |
| `Failed` | `failed` |
| `Error` | `failed` |
| `Timeout` | `failed` |
| `Skipped` | `skipped` |
| `XFailed` | `skipped` |
| `XPassed` (strict=false) | `passed` |
| `XPassed` (strict=true) | `failed` |
| `Flaky` | `passed` |

oxitest sorts tests in the output file alphabetically by `name`.

## Aborted runs

A run can end before any test executes — a test file that fails to import, a `conftest.py` that
raises, a plugin that fails to load, or a `--strict=abort` violation. oxitest still writes the CTRF
file, and each such error appears as its own `failed` entry:

```json
{
  "results": {
    "tool": { "name": "oxitest" },
    "summary": { "tests": 1, "passed": 0, "failed": 1, "skipped": 0, "other": 0 },
    "tests": [
      {
        "name": "tests/test_example.py",
        "status": "failed",
        "duration": 0.0,
        "message": "collection error in tests/test_example.py:\n…ModuleNotFoundError: No module named 'requests'"
      }
    ]
  }
}
```

| Field | Value for an aborted run |
|-------|--------------------------|
| `tests[].name` | For a collection error: the failing file's path, or `<collection>` if the error names no file. For a `--strict=abort` violation: the node ID of the test the violation belongs to, or `<strict>` for a suite-level violation. |
| `tests[].status` | Always `"failed"` |
| `tests[].duration` | `0.0` — nothing ran |
| `tests[].message` | The same text the console prints |
| `results.summary.failed` | The number of errors, so a dashboard summing failures sees a red run rather than an empty one |

!!! warning "`name` is not a unique key"
    A `--strict=abort` entry deliberately reuses the node ID of the test its violation belongs to.
    The violation *is* about that test, and reusing the ID is what lets a consumer line the two up.
    So do not treat `tests[].name` as identifying which kind of entry you are looking at, and do not
    key a map on it.

These entries are counted in `summary.tests` and `summary.failed`. The pass rate for such a run is
`0` of `N`, which is accurate: no test executed.

[`--junit-xml`](junit-output.md#aborted-runs) also writes its artifact on every early exit, and
passing both flags produces both. They agree about *whether* the run aborted, but they do not spell
it identically, so do not diff one against the other field by field:

- CTRF has a single flat `name`, so an import failure is named after the file and `<collection>` is
  used only when the error carries no path. JUnit has `classname` as well, so the file goes there
  and `<collection>` is used for every collection error.
- A collection error counts toward `summary.failed` here, but toward `errors=` — not `failures=` —
  in the XML. Compare `summary.failed` against `failures + errors`, never against `failures` alone.
