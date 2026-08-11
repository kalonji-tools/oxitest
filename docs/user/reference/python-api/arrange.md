# `@arrange`

!!! abstract "Reference"
    Explicit fixture-arrangement decorator. Attach one or more fixtures to a
    test function so they are set up before the test runs (and torn down
    after) without appearing in the parameter list.

Arranges the named fixtures (and their transitive dependencies) around the
test body. Useful for autouse-like effects on a single test, or when the
fixture's value is not needed by the test body itself (e.g. patching, log
capture setup).

## It also decides scheduling

Tests that arrange fixtures in the same connected component are co-located
onto the main process. Since [#1848](https://github.com/kalonji-tools/oxitest/issues/1848)
this decorator is the only thing that co-locates: nothing is inferred from a
fixture's lifetime.

| Fixture lifetime | Effect of arranging it |
|---|---|
| `function` | consumers land together; still rebuilt per test |
| `module` | consumers land together; still rebuilt per module |
| `process` | consumers land in one process, so the fixture is built **once** rather than once per worker |

!!! warning "A type entry does not schedule yet"
    `@oxi.arrange("db")` co-locates. `@oxi.arrange(TempDir)` is accepted and
    then ignored by the scheduler, because the type's name and the fixture's
    name are different keys. Tracked in
    [#2045](https://github.com/kalonji-tools/oxitest/issues/2045). Use the
    string form when placement matters.

See [Run in parallel](../../how-to/run-in-parallel.md) for how this interacts
with worker distribution,
[Use fixtures](../../how-to/use-fixtures.md) for usage examples, and
[ADR-0006](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0006-async-organizational-strategy.md)
for the async interaction rules (sync test + function-scope async fixture
is rejected at arrange time).

::: oxitest.arrange
    options:
      show_source: false
      heading_level: 2
