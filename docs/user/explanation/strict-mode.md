# Strict Mode

!!! abstract "Explanation"
    What strict mode enforces, why each check exists, and how to choose between abort and enforce.

!!! info "Deep dive"
    See [Pipeline Deep Dive](../../../internals/book/pipeline.html) for the strict_or_skip phase that partitions items into clean and violated sets.

## The problem strict mode solves

A test suite has two kinds of correctness. The first is whether the tests pass. The second is
whether the tests are written well enough to be trustworthy. A test that passes but contains a
bare `assert result` tells you nothing when it fails — you only see `AssertionError` with no
context. A parametrize case written as a plain `dict` loses the structure that makes test output
readable. A skip with no `reason=` argument leaves the next reader guessing why the test was
excluded.

Strict mode makes oxitest enforce conventions that experienced teams apply manually,
automatically, and at the point where violations are cheapest to fix: before the test suite runs.

## The seven checks

### Bare assert

```python
assert result                  # triggers — no comparison operator
assert result == expected      # clean — oxitest captures left/right
assert result, "explanation"   # also clean — message explains intent
```

A bare `assert` without a comparison operator (e.g. `assert result` rather than
`assert result == expected`) prevents oxitest from generating enriched diagnostics that show
both sides of the comparison. When it fails you see only `AssertionError` with no context about
what the actual vs expected values were.

Adding either a comparison operator or a message string satisfies the check.

Detected during test collection.

### Dict parametrize

```python
@oxitest.parametrize(a={"x": 1}, b={"x": 2})   # triggers
@oxitest.parametrize(a=Case(x=1), b=Case(x=2))  # clean
```

Passing plain dicts as parametrize cases loses the structure that makes test IDs, output, and
type checking work correctly. Frozen dataclasses are the intended form: they carry a name, are
hashable, and give type checkers something to reason about.

Detected during test collection.

### Missing mark reason

```python
@oxitest.mark.skip                          # triggers
@oxitest.mark.skip(reason="...")            # clean

@oxitest.mark.skip(when=condition)             # triggers
@oxitest.mark.skip(when=condition, reason="")  # clean

@oxitest.mark.xfail                         # triggers
@oxitest.mark.xfail(reason="...")           # clean
```

Skip and xfail marks without a `reason` argument leave no record of why a test is excluded.

Detected during test collection.

### Marker without description

```toml
[tool.oxitest]
markers = ["db", "slow: tests that hit the real database"]
```

A marker registered in `pyproject.toml` with no description string is a marker that exists
without documentation. The description appears in `oxitest query markers` output and serves as the
only canonical explanation of what the marker means.

Detected when configuration is loaded.

### Missing return annotation

=== "Triggers"

    ```python
    fx = oxitest.Fixtures()

    @fx.fixture
    def db_conn():
        return Connection()
    ```

=== "Clean"

    ```python
    fx = oxitest.Fixtures()

    @fx.fixture
    def db_conn() -> Connection:
        return Connection()
    ```

Fixtures without a return type annotation prevent downstream type checking from
propagating fixture types into test parameters. Strict mode flags these so the
type checker can do its job.

### Single-case parametrize

=== "Triggers"

    ```python
    @oxi.parametrize("x", [42])
    def test_answer(x: int):
        assert x == 42
    ```

=== "Clean"

    ```python
    def test_answer():
        assert 42 == 42
    ```

A `@parametrize` decorator with a single case adds indirection without benefit.
Strict mode flags these — either add more cases or inline the value.

### Unused fixture

=== "Triggers"

    ```python
    # conftest.py
    fx = oxitest.Fixtures()

    @fx.fixture
    def unused_helper() -> str:
        return "never injected"
    ```

=== "Clean"

    Remove the fixture definition, or use it in at least one test.

Conftest fixtures that are never injected into any collected test are flagged as
unused. Autouse fixtures, built-in fixtures, and transitive dependencies of used
fixtures are excluded from this check.

!!! warning "Known limitation: `Fixture[TempDir]` and unused-fixture check"
    The unused-fixture check does not recognise `Fixture[TempDir]` as a usage
    because the strict checker inspects annotation names and excludes built-in
    inner types. If strict mode flags a `TempDir` fixture as unused, annotate
    the test parameter as bare `TempDir` instead (it's `@injectable`, so no
    `Fixture[T]` wrapper is needed).

## Two modes

=== "Abort mode"
    ```console
    $ oxitest --strict
    $ oxitest --strict=abort
    ```

    ```toml
    [tool.oxitest]
    strict = "abort"
    ```

    Abort mode runs collection, then stops. If any violations are found, it prints them and
    exits with code 3 before any tests run. No test is executed when violations are present.

    Use **abort** when the codebase already passes strict checks and the goal is to keep it
    that way. The CI gate catches any regression before it merges.

    Output:

    ```text
    collected 42 items

    STRICT VIOLATIONS
    ══════════════════════════════════════════════════════════════════
      tests/test_foo.py::test_add   bare-assert        lines 12, 18
      tests/test_bar.py::test_mul   dict-parametrize
      markers["db"]                  no description
    strict violations found — aborting (exit 3)
    ```

=== "Enforce mode"
    ```console
    $ oxitest --strict=enforce
    ```

    ```toml
    [tool.oxitest]
    strict = "enforce"
    ```

    Enforce mode runs all tests. Per-test violations (bare assert, dict parametrize, missing
    mark reason) are reported as `ERROR` outcomes for the affected tests. Suite-level violations
    (marker without description) appear in a `STRICT` block after the failure list.

=== "Off"
    ```console
    $ oxitest --strict=off
    ```

    ```toml
    [tool.oxitest]
    strict = "off"
    ```

    Disables strict mode, overriding any project-wide setting. Useful when running
    a subset of tests (e.g. documentation examples) that intentionally use bare asserts.

    !!! note
        `strict = "off"` in `pyproject.toml` is valid but redundant — omitting the
        `strict` key has the same effect. `--strict=off` is primarily useful on the
        CLI to override a project-wide `strict = "abort"` setting.

    Use **enforce** when migrating an existing suite to strict compliance. Tests still run and
    their results are visible; violations appear alongside failures rather than preventing the
    suite from running at all.

    Output:

    ```text
    ..F..E..

    FAILURES ════════════════════════════════════════════════════════
    FAILED  tests/test_foo.py::test_bad
      ...

    ERROR   tests/test_bar.py::test_add
      strict: bare assert on line 12

    STRICT ══════════════════════════════════════════════════════════
      markers["db"]   no description

    1 failed · 1 error · 40 passed
    ```

The project-level setting in `pyproject.toml` sets the default for everyone on the team.
Individual runs can override it with the CLI flag.
