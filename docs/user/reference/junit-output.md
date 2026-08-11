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

The elements above are self-closing only while the message fits on one line. See
[Multi-line messages](#multi-line-messages) for the shape they take otherwise — which is the common
case for a failing doctest or any exception whose text spans several lines.

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

## Multi-line messages

When a message spans more than one line — a failing doctest, or any exception whose text wraps —
the `message` attribute holds only its **first line**, and the full text is repeated as the
element's body:

```xml
<failure message="Expected: 4">Expected: 4
Got: 5
</failure>
```

**Read the body, not the attribute, when you need the whole message.** XML attribute-value
normalisation collapses a literal newline in an attribute value to a space, so a conforming parser
would hand you a mangled single-line version of the attribute regardless — the body is the only
place the original survives.

This applies on every route, not only to aborted runs. Single-line messages are unaffected and keep
the self-closing `<failure message="..."/>` form.

## Aborted runs

A run can end before any test executes — an import error, a declaration-file failure, a plugin that fails
to load, an async backend that fails to initialise, a doctest coverage error, a fixture-name
validation failure, a malformed `-E` expression, or a `strict = "abort"` violation. oxitest still
writes the XML file in every one of those cases, and it does **not** write an empty
`<testsuites tests="0" failures="0" errors="0"/>`.

Instead each error becomes a synthesised `<testcase>`, counted in `tests=` and in `errors=` or
`failures=`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="1" failures="0" errors="1" time="0.004">
  <testsuite name="oxitest" tests="1" failures="0" errors="1" skipped="0" time="0.004">
    <testcase classname="tests.test_bad_import" name="&lt;collection&gt;" time="0.000">
      <error message="collection error in tests/test_bad_import.py:">collection error in tests/test_bad_import.py:
Traceback (most recent call last):
  ...
ModuleNotFoundError: No module named 'nope'</error>
    </testcase>
  </testsuite>
</testsuites>
```

Counting them is the point. A report with `errors="0"` renders as a green run in every JUnit
consumer, so an aborted run serialised that way is worse than no file at all — CI would treat a
suite that never executed as passing. Reporting 0 passed of N cannot inflate a pass rate;
reporting 0 of 0 does.

### Naming

| Route | `classname` | `name` | Child element |
|-------|-------------|--------|---------------|
| Collection error naming a file | The file, dot-mangled as usual | `<collection>` | `<error>` |
| Collection error naming no file | _(empty)_ | `<collection>` | `<error>` |
| `strict = "abort"`, per-test violation | The test's module | The test's function name | `<failure>` |
| `strict = "abort"`, suite-level violation | _(empty)_ | `<strict>` | `<failure>` |

`<error>` for collection and `<failure>` for strict follows the same distinction as the status
mapping above: a collection error means the suite could not be loaded, a strict violation means it
was loaded and found non-conforming.

!!! warning "`classname`/`name` is not a unique key"
    Two separate things make it non-unique, and both are expected:

    **It can collide with a real test.** A per-test `strict = "abort"` entry deliberately reuses the
    identity of the test its violation belongs to. The violation *is* about that test, and reusing
    the identity is what lets a consumer line the two up. The `<collection>` and `<strict>` markers
    are the reliable signal for "this is not a real test" — neither is a valid Python identifier.

    **It can collide with itself.** One run emits one entry per violation, and a run can produce
    several: an undescribed marker for each marker in `markers`, all of them
    `classname="" name="<strict>"`; or a bare assert *and* a missing return annotation on the same
    test, both carrying that test's identity. Jenkins and GitHub's test reporting key on
    `classname`+`name` and will collapse or suffix these. Read the `<failure>` messages rather than
    counting distinct names — the `<testsuites>` counters are authoritative for how many violations
    there were.

!!! note "Deliberate divergence from other runners"
    pytest, nose2, unittest-xml-reporting and jest-junit all put the "this is not a real test"
    marker in `classname` (`Test suite failed to run`, `unittest.loader._FailedTest`, and so on).
    oxitest keeps the module path in `classname` and puts the marker in `name`.

    This is deliberate, not an oversight. A `strict = "abort"` violation reuses the node ID of the
    test it belongs to; a synthetic `classname` would destroy that reuse and leave `classname`
    meaning "the module" on one aborted-run route and "the kind of problem" on the other. Keeping
    the module path in `classname` on both routes also means a collection error groups under the
    same node its tests would have grouped under.

## JUnit XML vs CTRF JSON

Use JUnit XML when your CI platform has native JUnit support (GitHub Actions test
summaries, Jenkins JUnit plugin, GitLab CI artifacts). Use [CTRF JSON](json-output.md)
when you need richer structured data — CTRF includes per-test duration in milliseconds
and is designed for programmatic consumption and custom dashboards.
