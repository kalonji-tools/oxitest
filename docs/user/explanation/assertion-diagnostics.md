# Assertion Diagnostics

!!! abstract "Explanation"
    How oxitest captures the left and right operands of a failing assertion and why that requires rewriting the AST before execution.

!!! info "Deep dive"
    See [PyO3 Bridge Contract](../../../internals/book/bridge.html) for how TestResult and FailureDiagnostic are serialized across the Rust/Python boundary.

## The problem Python's AssertionError creates

When a Python `assert` statement fails, the interpreter raises `AssertionError`. By that
point, the operands are gone.

Consider `assert x == y`. Python evaluates the expression `x == y`, gets `False`, and raises
`AssertionError`. The values that `x` and `y` held at the moment of comparison are not stored
anywhere. The only information preserved is the optional message string if the programmer wrote
one: `assert x == y, "values differ"`. Without that string, the error is silent about what
the values actually were.

This is not a bug in Python; it is a consequence of how `assert` is defined. The statement is
a simple truth check, not a structured comparison. The interpreter has no reason to hold onto
the operands after it has evaluated the boolean result.

The consequence for test runners is that showing a useful failure message — "left: 41, right: 42"
— requires work that Python's built-in machinery does not do.

pytest solves this with its own AST rewriting. oxitest does the same, for the same reason.

## How oxitest captures operands

When a test module is imported, oxitest rewrites `assert` statements so that
both sides of the comparison are captured before the check runs. If the assertion
fails, the captured values are available for the diagnostic output.

The rewriting preserves the original source lines in tracebacks — line numbers
are unchanged.

## What gets captured

The rewriter handles the comparison operators that appear in common test assertions:

- Equality and inequality: `==`, `!=`
- Ordering: `<`, `<=`, `>`, `>=`
- Membership: `in`, `not in`
- Identity: `is`, `is not`

For each of these, both operands are captured and stored. The failure output shows them labelled
as `left` and `right` beneath the source line.

Truthiness asserts — `assert x` where the whole expression is the tested value with no operator
— are also handled. The value is captured and displayed under a `value:` label.

The optional message is passed through unchanged. If the programmer wrote
`assert x == y, "the counts must match"`, that string appears in the diagnostic under a `why:`
label, alongside the captured operand values.

## What does not get captured

For `assert f(x) == g(y)`, the return values of `f(x)` and `g(y)` are captured,
but the arguments `x` and `y` are not. Only the direct operands of the comparison
are tracked.

Chained comparisons (`assert a < b < c`) are not currently decomposed into
individual captured steps.

## Bare asserts and the no-message tip

A bare `assert x == y` with no message is the common case. When it fails, the diagnostic block
shows the source line and the operand values, which is usually enough.

A bare `assert x` — a truthiness check with no comparison and no message — produces the least
informative failure possible. oxitest tracks these as `no_message_lines`. At the end of a run
that had any such failures, the summary surfaces a tip suggesting that adding a message would
make future failures easier to understand.

In CI mode (where output is not a TTY), passing tests with bare asserts emit a `·` middot
marker in the progress stream rather than `.` (a clean pass). This makes them visually distinct
in the output log.

## Color-coded diffs

When a comparison assertion fails, oxitest renders the left and right values with color
to make differences immediately visible:

- **`- left:`** value in **bold red** — what the expression produced
- **`+ right:`** value in **bold green** — what was expected
- **`why:`** in **dim gray** — the assertion message (neutral context)

For multi-line string comparisons, oxitest produces a unified diff (similar to `git diff`)
using the `similar` crate. Each removed line is prefixed with `-` and each added line
with `+`.

## Dataclass field diffs

When an `assert ==` comparison fails between two dataclass instances, oxitest
detects this at exception time and computes per-field diffs. Instead of showing
a single opaque comparison, the diagnostic box lists each field where the values
diverge:

```text
  ┌ test_user.py:12
  │
  │   assert updated == expected
  │
  │   field diffs (User):
  │     name  "alice" != "Alice"
  │     age   30 != 31
  │
  └ assert updated == expected
```

This works automatically for any class decorated with `@dataclasses.dataclass`.
No configuration is needed. The field diff appears inside the diagnostic box
alongside the standard left/right/operator output.

## Fix suggestions

When oxitest can diagnose the likely cause of an error, it appends a `hint:` line to the
failure output. Current patterns:

| Error | Hint |
|-------|------|
| Async/sync fixture mismatch (`can't be used in 'await'`) | Mark the fixture `async def` or make the test synchronous. |
| `SharedFixtureMutationError` | Declare the fixture with `lifetime="function"` for a mutable per-test copy. |

## Frame truncation

By default (`--tb=detail`), oxitest hides internal framework frames from tracebacks.
Only user code frames are shown. Frames from `oxitest/_bridge/`, `oxitest/_builtins/`,
and `oxitest/plugin` are filtered out. At least one frame (the innermost) is always shown.

Pass `--show-internals` to include all frames, including oxitest internals.

## The `--tb` styles

The `--tb` flag controls how much of the diagnostic block is printed for each failure.
Use `--show-locals` and `--show-internals` for additional detail within `--tb=detail`.

=== "`--tb detail` (default)"
    Shows the source line of the failing assertion, color-coded diff of operand values,
    and any fix suggestions. Internal framework frames are hidden unless `--show-internals`
    is set.

    ```text
    FAILED  ./test_diag.py::test_comparison
            ┌─ ./test_diag.py:4
            │
          4 │    assert x == y
            │
            ├─  diff
            │  - left:  41
            │  + right: 42
            └─ why:   values should match
    ```

=== "`--tb detail --show-internals`"
    Full call-chain frames (including oxitest internals), plus the diff and suggestions.

    ```text
    FAILED  ./test_diag.py::test_comparison
            ┌─ ./test_diag.py:4
            │
            ├─  frames
            │    test_diag.py:4  test_comparison
            │      assert x == y
            │
            ├─  diff
            │  - left:  41
            │  + right: 42
            └─ why:   values should match
    ```

=== "`--tb line`"
    One compact line per failure: `STATUS  node_id  :lineno  message`.

=== "`--tb no`"
    Suppresses the diagnostic block entirely. Only the test name and FAILED status are
    shown. Useful for scripting or piping output to another tool that will process failures
    programmatically.

## Default output mode

By default (without `-v`), oxitest shows only the progress bar during execution. Passing
tests are silent. Failures are printed at the end before the summary.

With `-v` (verbose), every test result is printed as it completes — including passing tests
with their duration.

## See also

- [Strict mode](strict-mode.md) — bare asserts as a strict violation
- [CLI reference](../reference/cli.md) — `--tb`, `--show-locals`, `--show-internals` flags
- [Errors reference](../reference/errors.md) — error message catalog
