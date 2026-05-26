# JUnit XML Output Format

!!! abstract "Reference"
    Complete reference for the oxitest JUnit XML output format.

oxitest writes test results to a JUnit-compatible XML file when `--junit-xml` is passed.
JUnit XML is accepted by GitHub Actions, Jenkins, GitLab CI, and Azure DevOps for test
result reporting and trend analysis.

## Usage

```console
$ oxitest --junit-xml results.xml
```

oxitest writes the file at the end of the run. If the file already exists, oxitest
overwrites it. `--junit-xml` can be combined with `--json` to produce both formats in
a single run.

## Schema

```xml
<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="4" failures="1" errors="1" time="1.234">
  <testsuite name="oxitest" tests="4" failures="1" errors="1" skipped="1" time="1.234">
    <testcase classname="tests.test_example" name="test_add" time="0.012"/>
    <testcase classname="tests.test_example" name="test_divide" time="0.008">
      <failure message="ZeroDivisionError: division by zero"/>
    </testcase>
    <testcase classname="tests.test_example" name="test_slow" time="5.001">
      <error message="exceeded 5s timeout"/>
    </testcase>
    <testcase classname="tests.test_example" name="test_wip[case0]" time="0.001">
      <skipped message="not ready yet"/>
    </testcase>
  </testsuite>
</testsuites>
```

## Field reference

### `<testsuites>` attributes

| Attribute | Description |
|-----------|-------------|
| `tests` | Total number of test cases in the run |
| `failures` | Number of tests with a `<failure>` child element |
| `errors` | Number of tests with an `<error>` child element |
| `time` | Total wall-clock duration of the run in seconds (3 decimal places) |

### `<testsuite>` attributes

| Attribute | Description |
|-----------|-------------|
| `name` | Always `"oxitest"` |
| `tests` | Total number of test cases (same as `testsuites/@tests`) |
| `failures` | Number of failures (same as `testsuites/@failures`) |
| `errors` | Number of errors (same as `testsuites/@errors`) |
| `skipped` | Number of tests with a `<skipped>` child element |
| `time` | Total wall-clock duration in seconds (same as `testsuites/@time`) |

### `<testcase>` attributes

| Attribute | Description |
|-----------|-------------|
| `classname` | Module path converted to dot notation, e.g. `tests/unit/test_math.py` → `tests.unit.test_math` |
| `name` | Function name, e.g. `test_add`; parametrized cases include the param ID: `test_add[case0]` |
| `time` | Test duration in seconds (3 decimal places) |

### `<testcase>` child elements

A passed test has no child elements (`<testcase ... />`). All other outcomes add a single
child element:

| Element | Attribute | Description |
|---------|-----------|-------------|
| `<failure>` | `message` | Failure message text (assertion error, comparison string, etc.) |
| `<error>` | `message` | Error message text (exception traceback summary, timeout message) |
| `<skipped>` | `message` | Skip reason. Omitted when the reason is empty. |

## Status mapping

| oxitest outcome | JUnit element |
|-----------------|---------------|
| `Passed` | _(none — self-closing `<testcase/>`)_ |
| `Warned` | _(none — self-closing `<testcase/>`)_ |
| `Flaky` | _(none — self-closing `<testcase/>`)_ |
| `XPassed` (strict=false) | _(none — self-closing `<testcase/>`)_ |
| `Failed` | `<failure message="..."/>` |
| `XPassed` (strict=true) | `<failure message="expected failure but test passed (strict xfail)"/>` |
| `Error` | `<error message="..."/>` |
| `Timeout` | `<error message="..."/>` |
| `Skipped` | `<skipped message="..."/>` |
| `XFailed` | `<skipped message="..."/>` |

## JUnit XML vs CTRF JSON

Use JUnit XML when your CI platform has native JUnit support (GitHub Actions test
summaries, Jenkins JUnit plugin, GitLab CI artifacts). Use [CTRF JSON](json-output.md)
when you need richer structured data — CTRF includes per-test duration in milliseconds
and is designed for programmatic consumption and custom dashboards.
