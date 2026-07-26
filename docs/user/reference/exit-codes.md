# Exit Codes

!!! abstract "Reference"
    Complete reference for oxitest exit codes.

!!! info "Deep dive"
    See [Architecture Overview](../../../internals/book/architecture.html#module-reference-table) for the ExitCode enum definition and how reporters vote on the final exit code.

| Code | Meaning |
|------|---------|
| `0` | All tests passed (or no tests were collected). Also exits with 0 when `oxitest env` is used (no tests are run). Flaky tests (failed on first attempt, passed on retry) are not counted as failures and do not affect this code. |
| `1` | One or more tests failed or errored |
| `2` | Run interrupted (e.g. `-x` or `--maxfail` reached) |
| `3` | Collection error (a test file could not be imported) or strict violations detected when using `--strict=abort` |
| `4` | `UsageError` — oxitest exits before running any tests. Sources: invalid CLI arguments; `--json` output file cannot be written; `[tool.oxitest]` in `pyproject.toml` has unknown fields, wrong types, or values pointing at removed options. See [Error reference — Configuration errors](errors.md#configuration-errors) and [ADR-0008](../../adr/0008-config-fail-closed-narrow-scope.md). |

## See also

- [Error reference](errors.md) — catalog of error messages with causes and fixes
