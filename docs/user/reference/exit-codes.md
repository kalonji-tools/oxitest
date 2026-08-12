# Exit Codes

!!! abstract "Reference"
    Complete reference for oxitest exit codes.

!!! info "Deep dive"
    See [Architecture Overview](../../../internals/book/architecture.html#module-reference-table) for the ExitCode enum definition and how reporters vote on the final exit code.

| Code | Meaning |
|------|---------|
| `0` | All tests passed, or a **valid** target collected no tests. A target that does not exist exits `4`, not `0` — see below. Also exits with 0 when `oxitest env` is used (no tests are run). Flaky tests (failed on first attempt, passed on retry) are not counted as failures and do not affect this code. |
| `1` | One or more tests failed or errored |
| `2` | Run interrupted (e.g. `-x` or `--maxfail` reached) |
| `3` | Collection error — a test file could not be imported, **or a declaration inside it was refused** (see [Malformed test declarations](#malformed-test-declarations)) — or strict violations detected under `strict = "abort"` |
| `4` | `UsageError` — the request itself was invalid. Defined by the **class** of the error, not by when oxitest detects it. Sources: invalid CLI arguments; **a target that does not exist** (a path, a directory, or a literal node ID matching no test); `--json` output file cannot be written; `[tool.oxitest]` in `pyproject.toml` has unknown fields, wrong types, or values pointing at removed options; **a fixture wiring error found while a test runs** (see below). See [Error reference — Configuration errors](errors.md#configuration-errors), [ADR-0008](../../adr/0008-config-fail-closed-narrow-scope.md) and [ADR-0014](../../adr/0014-target-validation.md). |

## Targets

A **target** is a path, a directory, or a node ID given as a command-line argument.

A target that names something absent is a usage error rather than an empty run, so the whole run is refused and no test executes:

```bash
oxitest tests/test_typo.py            # exit 4 — no such path
oxitest tests/test_real.py::test_typo # exit 4 — no such test
oxitest tests/test_real.py missing.py # exit 4 — one bad target refuses everything
```

Every bad target in one invocation is reported, not just the first.

Two cases stay at exit `0`, deliberately:

- **A valid target that holds no tests** — an empty directory, a non-test file, or a run where `-E` deselected everything. Nothing was wrong, so nothing is reported.
- **A glob node ID that matches nothing** — `oxitest 'tests/test_a.py::test_slow_*'`. A glob asks to match what is present, so only a *literal* target asserts existence.

`--affected`, `--failed=only` and `--failed=first` narrow a list that collection already produced. They never supply a target, so a run they legitimately narrow to zero tests still exits `0`.

## Fixture wiring errors

A **fixture wiring error** is refused at whichever of two points can catch it, and the exit code is `4` either way — the code is fixed by the *class* of the error, not by when oxitest detects it.

A `fx.` access written literally is read out of the test body during collection, so it is refused **before any test runs** and the whole run stops. An access oxitest cannot see until it executes — `getattr(fx, name)` — is found while a test runs, because a fixture is resolved at the moment the test asks for it.

In that second case the run is **not** stopped. Every test still executes and still reports its own outcome. Only the final exit code changes:

```bash
oxitest                      # exit 4 — "3 errors · 1 passed"
```

Three kinds count:

- **A fixture the test cannot see** — the fixture exists, but not in this test's anchor package or below it. On `fx.<namespace>.<name>` this reports as `BoundaryError`; on a `Fixture[T]` parameter it reports as `FixtureNotFoundError`, because a bare name has no namespace segment to attribute.
- **An async fixture reached from a sync test.**
- **A fixture dependency whose lifetime cannot hold** — a fixture that outlives the test depending on a shorter-lived async one.

A misspelt fixture name on a `Fixture[T]` parameter is different: it is refused at collection and exits `3`, because the collection validator checks names before anything runs.

Exit `4` outranks both `1` and `3`. A run that holds a wiring error *and* a failed assertion exits `4`: a suite that is wired wrong makes its own assertion results untrustworthy.

## Malformed test declarations

Some declarations are refused **before any test runs**, and the run stops with exit `3`. These are collection errors, but nothing failed to import — the file parsed and imported cleanly, and what oxitest refused was the shape of a declaration inside it.

Two exist today:

- **A generator test function.** A test containing `yield` is a generator function: calling it returns a generator and runs none of the body, so the test would be reported as passed having executed nothing. Refused with a message naming the function and the line. Applies to `async def` and to methods of a `Test*` class, and a `@mark.skip` does not suppress it — a skip is a decision about a test that could have run.
- **An inline fixture above its lifetime cap.** A fixture declared in a test file is capped at `lifetime="module"`; `"package"` and `"process"` are refused with a message naming the sibling `__fixtures__.py` to move it to.

The generator case has a **runtime counterpart that exits `4`**, and the split is the same one the fixture-wiring section describes. Two routes reach it, and neither is visible to collection: a decorator built with `functools.wraps` leaves `inspect.isgeneratorfunction` answering `False` on the wrapper, and an `async def` that *returns* a generator only produces one when it is awaited. Both are caught while the test runs, as a per-test error, and the run is **not** stopped:

```bash
oxitest                      # exit 4 — "1 error · 1 passed"
```

A **`return <value>`** in a test body is different again, and milder: the body did run. It is reported only under `strict` as `test-returns-value`, and exits `3` in abort mode with the other strict violations. See [Strict Mode](../explanation/strict-mode.md) and [ADR-0017](../../adr/0017-a-test-function-returns-none.md).

## See also

- [Error reference](errors.md) — catalog of error messages with causes and fixes
