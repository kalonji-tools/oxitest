# `@arrange`

!!! abstract "Reference"
    Explicit fixture-arrangement decorator. Attach one or more fixtures to a
    test function so they are set up before the test runs (and torn down
    after) without appearing in the parameter list.

Arranges the named fixtures (and their transitive dependencies) around the
test body. Useful for autouse-like effects on a single test, or when the
fixture's value is not needed by the test body itself (e.g. patching, log
capture setup).

See [Use fixtures](../../how-to/use-fixtures.md) for usage examples and
[ADR-0006](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0006-async-organizational-strategy.md)
for the async interaction rules (sync test + function-scope async fixture
is rejected at arrange time).

::: oxitest.arrange
    options:
      show_source: false
      heading_level: 2
