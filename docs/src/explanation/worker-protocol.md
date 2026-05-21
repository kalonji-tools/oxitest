# Worker Subprocess Protocol

!!! abstract "Explanation"
    How the Rust parallel runner communicates with Python worker subprocesses.

## Overview

When oxitest runs tests in parallel, the Rust scheduler spawns one or more persistent
Python worker subprocesses. Each worker receives test tasks via **stdin** and emits
results via **stdout**, using newline-delimited JSON.

```
┌──────────────┐    stdin (JSON tasks)     ┌─────────────────┐
│  Rust runner │ ─────────────────────────► │  Python worker  │
│  (parallel)  │ ◄───────────────────────── │  (_bridge.worker)│
└──────────────┘   stdout (JSON results)   └─────────────────┘
```

**Spawn command:** `python -m oxitest._bridge.worker`

- stdin: piped (Rust writes tasks)
- stdout: piped (Rust reads results)
- stderr: inherited (Python tracebacks visible to user)

## Lifecycle

1. Rust spawns the worker subprocess
2. Worker enters a read loop on stdin
3. For each newline-delimited JSON task, the worker executes all items and writes one result line per test to stdout
4. When stdin reaches EOF (Rust closes the pipe), the worker exits cleanly
5. Rust calls `child.wait()` to reap the process

Workers are **persistent** — one subprocess handles multiple module groups sequentially.
This amortizes Python interpreter startup cost across many tests.

## Task Schema (stdin)

Each line on stdin is a JSON object:

```json
{
    "module_path": "tests/test_math.py",
    "items": [
        {"fn_name": "test_add", "param_id": null},
        {"fn_name": "test_mul", "param_id": "x0"}
    ],
    "conftest_paths": ["tests/conftest.py", "conftest.py"],
    "timeout_secs": 30
}
```

| Field | Type | Description |
|-------|------|-------------|
| `module_path` | string | Relative path to the test module file |
| `items` | array | Test functions to execute in this module |
| `items[].fn_name` | string | Function name within the module |
| `items[].param_id` | string \| null | Parametrize case ID (e.g. `"x0"`) or null |
| `conftest_paths` | array of strings | Conftest files to load for fixture resolution |
| `timeout_secs` | int \| null | Per-test timeout in seconds, or null for no timeout |

## Result Schema (stdout)

For each test item, the worker writes exactly one JSON line to stdout:

```json
{
    "node_id": "tests/test_math.py::test_add",
    "outcome": "passed",
    "duration_ms": 12.5,
    "failure_repr": null,
    "message": null,
    "file": null,
    "lineno": null,
    "source_line": null,
    "no_message_lines": [],
    "left": null,
    "right": null,
    "op": null,
    "strict": false,
    "frames": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | Full test identifier: `module_path::fn_name[param_id]` |
| `outcome` | string | Test result status (see below) |
| `duration_ms` | float | Wall-clock execution time in milliseconds |
| `failure_repr` | string \| null | Human-readable failure summary (for skipped/xfailed/timeout/warned) |
| `message` | string \| null | Primary diagnostic message (assertion message, error text) |
| `file` | string \| null | Source file where the failure occurred |
| `lineno` | int \| null | Line number of the failure |
| `source_line` | string \| null | Source code at the failure line |
| `no_message_lines` | array of int | Line numbers of bare assert statements (no message) |
| `left` | string \| null | Left operand repr for comparison assertions |
| `right` | string \| null | Right operand repr for comparison assertions |
| `op` | string \| null | Comparison operator (e.g. `"=="`, `"!="`, `"in"`) |
| `strict` | bool | Whether the test was in strict xfail mode |
| `frames` | array of objects | Stack frames from the traceback (empty for passed tests). Each object has `file` (string), `lineno` (int), `name` (string), `line` (string). |

## Outcome Values

| Value | Meaning |
|-------|---------|
| `passed` | Test completed successfully |
| `failed` | Assertion failed |
| `error` | Unexpected exception (not AssertionError) |
| `skipped` | Test was skipped (via `skip()` or `skipif`) |
| `xfailed` | Expected failure occurred |
| `xpassed` | Expected failure did NOT occur (unexpected pass) |
| `warned` | Test passed but emitted warnings |
| `timeout` | Test exceeded the configured timeout |

## Error Handling

### Malformed JSON from worker

If the Rust side receives a line that doesn't parse as valid JSON, it logs a warning
and counts it as a received result (to maintain the expected count), but does not
forward it to the reporter. The watchdog deadline is still reset.

### Subprocess crash

If the worker process exits unexpectedly (stdout disconnects before all results arrive),
Rust synthesizes `WorkerResult::crashed()` for each remaining test item in the group.
These appear as `"error"` outcomes with message "Worker subprocess exited unexpectedly".

### Watchdog timeout

Each worker has a per-result watchdog deadline:

- **With `timeout_secs` configured:** `timeout_secs + 30s` grace period
- **Without timeout:** 10-minute cap

If no result line arrives within the deadline, Rust kills the subprocess and synthesizes
`WorkerResult::timed_out()` for remaining items. Empty lines (blank stdout output) do
**not** reset the deadline — only real result lines do.

### Remaining unprocessed groups

If all workers crash before the scheduler is drained, any groups still queued are
reported as crashed errors via `drain_remaining_into_crashed()`.

## Protocol Invariants

1. **One result per item:** For N items in a task, the worker MUST write exactly N result lines
2. **Order matches input:** Results are emitted in the same order as `items` in the task
3. **No interleaving:** One task is fully processed before the next is read from stdin
4. **Newline-delimited:** Each JSON object is on its own line (no pretty-printing)
5. **Stdout only:** Results go to stdout; diagnostic output (tracebacks, prints) goes to stderr
