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
| `3` | Collection error (a test file could not be imported) or strict violations detected when using `--strict=abort` |
| `4` | `UsageError` — the request itself was invalid, so no test runs. Sources: invalid CLI arguments; **a target that does not exist** (a path, a directory, or a literal node ID matching no test); `--json` output file cannot be written; `[tool.oxitest]` in `pyproject.toml` has unknown fields, wrong types, or values pointing at removed options. See [Error reference — Configuration errors](errors.md#configuration-errors), [ADR-0008](../../adr/0008-config-fail-closed-narrow-scope.md) and [ADR-0014](../../adr/0014-target-validation.md). |

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

## See also

- [Error reference](errors.md) — catalog of error messages with causes and fixes
