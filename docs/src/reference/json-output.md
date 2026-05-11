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

oxitest sorts tests in the output file alphabetically by `name`.
