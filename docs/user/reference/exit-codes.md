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
| `4` | Invalid CLI arguments — oxitest exits before running any tests. Also returned when `--json` output file cannot be written. |

## See also

- [Error reference](errors.md) — catalog of error messages with causes and fixes
