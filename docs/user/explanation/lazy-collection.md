# Lazy Collection

oxitest uses **lazy collection** to dramatically speed up filtered test runs.
When you specify a filter (`-E`, `--failed=only`, node IDs, `--affected`),
oxitest skips importing Python modules that don't contain matching tests.

## How It Works

The pipeline has two phases before Python imports happen:

1. **AST Prescan** — Rust parses every test file's AST (without starting Python)
   and extracts metadata: function names, markers, parametrize case IDs,
   class membership, and fixture parameters.

2. **Metadata Filter** — Your filter expression is evaluated against the prescan
   metadata. Only files with matching tests proceed to Python import.

```
Walk FS → AST prescan (all files, Rust-only) → Filter on metadata
   → Import only matched modules → Schedule → Execute
```

## When It Activates

Lazy collection activates when **any filter** is present:

| Filter | Lazy? |
|--------|-------|
| `oxitest tests/test_auth.py::test_login` | Yes |
| `oxitest -E 'name(login)'` | Yes |
| `oxitest -E 'mark(slow)'` | Yes |
| `oxitest --failed=only` | Yes |
| `oxitest --affected` | Yes |
| `oxitest` (no filter) | No — imports all |

## Eager Fallback

Some files use dynamic patterns that can't be analyzed statically.
These are detected during prescan and fall back to eager import:

- `exec()` or `eval()` at module level
- `globals()[name] = ...` assignments
- Module-level `__getattr__` definition
- `type()` with 3 arguments (dynamic class creation)
- Star imports from non-stdlib modules (`from foo import *`)

Fallback is **per-file** — other files stay on the lazy path.

## Conftest Loading

With lazy collection, only conftest files in the **ancestor chain** of matched
test modules are loaded. If you run `tests/unit/test_auth.py`, conftests in
`tests/integration/` and `tests/e2e/` are skipped entirely.

## Plugin Loading

Plugins declaring only **lazy protocols** (`fixture_provider`, `log_backend`,
`execution_wrapper`, `debugger_backend`) can opt into deferred import:

```toml
[tool.oxitest.plugin_settings.my-plugin]
protocols = ["fixture_provider"]
```

Without this declaration, plugins are imported eagerly (backward compatible).

## Performance

Measured on a synthetic project with 500 test files (2,500 tests total):

| Scenario | Time |
|----------|------|
| Full run (no filter) | 0.510s |
| Single node ID | 0.121s |
| `-E 'name(...)'` | 0.170s |
| `-E 'mark(slow)'` | 0.190s |

Filtered runs skip Python import for non-matching modules, cutting wall-clock
time by 63--76% compared to the unfiltered baseline on this workload.
