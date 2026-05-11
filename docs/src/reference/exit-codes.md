# Exit Codes

!!! abstract "Reference"
    Complete reference for oxitest exit codes.

| Code | Meaning |
|------|---------|
| `0` | All tests passed (or no tests were collected) |
| `1` | One or more tests failed or errored |
| `2` | Run interrupted (e.g. `-x` or `--maxfail` reached) |
| `3` | Collection error (a test file could not be imported) or strict violations detected when using `--strict=abort` |
| `4` | Invalid CLI arguments — oxitest exits before running any tests |
