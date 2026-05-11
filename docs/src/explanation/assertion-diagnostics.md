# Assertion Diagnostics

!!! abstract "Explanation"
    How oxitest captures the left and right operands of a failing assertion and why that requires rewriting the AST before execution.

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

## The solution: rewrite the AST before execution

oxitest's `OxitestAssertRewriter` is a Python `ast.NodeTransformer`. It runs on the parsed
syntax tree of each test module before any code in that module executes.

The transformer walks the tree looking for `Assert` nodes. When it finds one, it replaces it
with generated code that:

1. Evaluates the left-hand side and stores it in a local variable.
2. Evaluates the right-hand side and stores it in a local variable.
3. Performs the comparison using those stored values.
4. If the comparison fails, raises `_OxitestAssertionError` carrying both stored values, the
   operator name, and the optional message.

The rewritten code is semantically equivalent to the original `assert` — it raises if and only
if the original would raise — but it preserves the operands so the reporter can display them.

Because the transformation happens at the AST level, before the module is compiled to bytecode,
the original source line still appears in tracebacks. The rewriter does not change line numbers.

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

The rewriter operates on the outermost `Assert` node and its direct comparison. It does not
recurse into complex sub-expressions.

For `assert f(x) == g(y)`, the rewriter captures the return values of `f(x)` and `g(y)` — the
operands of `==` — but it does not expose what `x` or `y` were. The intermediate values inside
the expressions are not tracked.

Chained comparisons are also a current limitation. Python allows `assert a < b < c`, which is
syntactic sugar for `assert a < b and b < c`. The rewriter does not currently decompose chained
comparisons into individual captured steps.

!!! note "Known limitations"
    These are known constraints of the current implementation, not fundamental limits of the
    approach. Deeper expression introspection is possible with more rewriting work, and is
    something pytest's `--showlocals` and the `ward` test framework have explored.

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

## The `--tb` styles

The `--tb` flag controls how much of the diagnostic block is printed for each failure.

=== "`--tb short` (default)"
    Shows the source line of the failing assertion and the captured operand values.

    ```text
    FAILED  ./test_diag.py::test_comparison
            ┌─ ./test_diag.py:4
            │
          4 │    assert x == y
            │
            │  left:  41
            └─ right: 42
    ```

=== "`--tb line`"
    Shows the file path and line number and the captured operand values, but omits the
    source code line. Useful when the goal is failure locations and values without the
    surrounding source context.

=== "`--tb no`"
    Suppresses the diagnostic block entirely. Only the test name and FAILED status are
    shown. Useful for scripting or piping output to another tool that will process failures
    programmatically.
