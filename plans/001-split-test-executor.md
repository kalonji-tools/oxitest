# Plan 001: Split test_executor.py into focused test modules

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 7983e5d..HEAD -- python/tests/test_executor.py`
> If `test_executor.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `7983e5d`, 2026-07-10
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1327

## Why this matters

`test_executor.py` is 1495 lines with ~60 test functions covering 5 distinct subsystems (teardown warning, sync execution, fixture injection, async execution, and timeouts). When a test fails, the filename tells you nothing about which subsystem broke. Splitting into focused modules makes the test suite navigable and failures immediately locatable. This is the largest remaining monolith after the #1315-#1325 consolidation wave.

## Current state

`python/tests/test_executor.py` contains these test function groups (identified by prefix and line range):

**Group A — _warn_teardown unit tests** (lines 21-65, 4 tests):
```
test_warn_teardown_emits_fixture_teardown_warning
test_warn_teardown_includes_node_id
test_warn_teardown_without_node_id
test_warn_teardown_picks_up_contextvar
```

**Group B — Sync execution basics** (lines 68-270, ~10 tests):
```
test_passing_function
test_passing_with_bare_assert_returns_no_message_lines
test_passing_with_message_assert_returns_empty_no_message_lines
test_failing_assertion_with_message
test_failing_bare_assertion
test_error_exception
test_skipped_via_unittest
test_function_not_found_is_error
test_warning_captured_as_warned_status
test_assertion_operands
```

**Group C — Fixture injection + teardown** (lines 309-530, ~9 tests):
```
test_run_test_without_session_backward_compat
test_run_test_with_fixture_injected
test_run_test_fixture_setup_error_returns_error_result
test_run_test_missing_fixture_returns_error_result
test_run_test_fixture_teardown_runs_after_failure
test_yield_fixture_teardown_exception_does_not_affect_test_result
test_yield_fixture_teardown_exception_does_not_block_next_teardown
test_multiple_teardown_failures_all_reported
```

**Group D — Parametrize execution in executor** (lines 533-630, 4 tests):
```
test_compact_parametrize_passes_whole_dataclass
test_expanded_parametrize_still_works
test_compact_parametrize_mixed_with_fixture
test_expanded_parametrize_with_unrelated_annotation
```

**Group E — Sync timeout** (lines 633-690, 4 tests):
```
test_run_test_timeout_mark_fires
test_run_test_timeout_passes_fast_test
test_run_test_default_timeout_fires
test_run_test_no_timeout_by_default
```

**Group F — Async execution** (lines 692-890, ~11 tests):
```
test_async_test_passes ... test_async_fixture_setup_error
test_sync_test_with_async_fixture_produces_error
```

**Group G — Async yield fixtures** (lines 891-1100, ~9 tests):
```
test_async_yield_fixture_provides_value ... test_sync_test_with_async_yield_fixture_produces_error
```

**Group H — Async timeout** (lines 1103-1180, 4 tests):
```
test_async_test_timeout_mark_fires
test_async_test_default_timeout_fires
test_async_test_timeout_passes_fast_test
test_async_yield_fixture_teardown_runs_on_timeout
```

**Group I — Shared async + task groups** (lines 1181-1495, ~9 tests):
```
test_shared_async_fixture_provides_value ... test_shared_async_depending_on_non_shared_async_error
```

Imports at the top of the file:
```python
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass

from oxitest import (
    Fixture,
    FixtureTeardownWarning,
    TempDir,
    WarnCapture,
    helpers,
    parametrize,
)
from oxitest._bridge._fixture_context import _current_teardown_node_id, _warn_teardown
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
```

Repo conventions:
- Every test file has a module-level docstring (e.g. `"""Tests for the executor: sync/async ..."""`)
- `from __future__ import annotations` at the top
- Tests use `helpers.common.exec_inline`, `helpers.common.run_test`, `helpers.common.make_session_with` from conftest
- Every `assert` has a message (strict=abort)
- Standalone `def test_*()` functions, no classes
- Follow existing split examples: `test_fixture_registry.py`, `test_fixture_session_lifecycle.py`, `test_fixture_shared.py` (from the #1316 split)

## Commands you will need

| Purpose   | Command                              | Expected on success |
|-----------|--------------------------------------|---------------------|
| Test all  | `just test`                          | exit 0, all pass    |
| Test file | `just test python/tests/test_executor_sync.py` | exit 0  |
| Check     | `just check`                         | exit 0              |
| Preflight | `just preflight`                     | exit 0              |

## Scope

**In scope** (files to create/delete):
- `python/tests/test_executor.py` — DELETE after split
- `python/tests/test_executor_sync.py` — CREATE (Groups A, B, C, D)
- `python/tests/test_executor_async.py` — CREATE (Groups F, G, I)
- `python/tests/test_executor_timeout.py` — CREATE (Groups E, H)

**Out of scope** (do NOT touch):
- `python/tests/test_executor_internals.py` — already focused
- `python/tests/conftest.py` — no helper changes needed
- Any test logic — only file boundaries change

## Git workflow

- Branch: per repo convention (use `wt switch --create`)
- Conventional commits: `chore: split test_executor.py into focused modules (#1327)`
- Do NOT push or open a PR unless the operator instructs it.

## Steps

### Step 1: Create test_executor_sync.py

Create `python/tests/test_executor_sync.py` containing Groups A, B, C, and D (warn_teardown, sync basics, fixture injection/teardown, parametrize execution). Add a docstring: `"""Tests for sync test execution: pass/fail, fixture injection, teardown safety."""`

Each new file needs only the imports actually used by its tests. Group A needs `_current_teardown_node_id`, `_warn_teardown` from `_fixture_context`. Groups B-D need `TempDir`, `WarnCapture`, `helpers`, `Fixture`, `FixtureTeardownWarning`, `FixtureRegistry`, `FixtureSession`, etc.

**Verify**: `just test python/tests/test_executor_sync.py` → all pass

### Step 2: Create test_executor_async.py

Create `python/tests/test_executor_async.py` containing Groups F, G, and I (async execution, async yield fixtures, shared async + task groups). Docstring: `"""Tests for async test execution: async fixtures, yield teardown, shared sessions."""`

This file will need `AsyncGenerator`, `Generator`, `Fixture`, `TempDir`, `WarnCapture`, `helpers`, `FixtureRegistry`, `FixtureSession`, plus the `dataclass` import for any test doubles.

**Verify**: `just test python/tests/test_executor_async.py` → all pass

### Step 3: Create test_executor_timeout.py

Create `python/tests/test_executor_timeout.py` containing Groups E and H (sync + async timeout mark tests). Docstring: `"""Tests for timeout mark execution during sync and async tests."""`

**Verify**: `just test python/tests/test_executor_timeout.py` → all pass

### Step 4: Delete test_executor.py

Delete the original `python/tests/test_executor.py`.

**Verify**: `just test` → all pass, same test count as before

### Step 5: Verify no tests were lost

Count `def test_` in the three new files combined and confirm it equals ~60 (the original count in `test_executor.py`).

**Verify**: `grep -c 'def test_' python/tests/test_executor_sync.py python/tests/test_executor_async.py python/tests/test_executor_timeout.py` → total matches original count

## Test plan

- No new tests to write — this is a pure file reorganization.
- Run `just test` after deletion to confirm zero test regressions.
- Run `just check` to confirm ruff/ty pass on new file names.

## Done criteria

- [ ] `python/tests/test_executor.py` no longer exists
- [ ] `python/tests/test_executor_sync.py` exists and passes
- [ ] `python/tests/test_executor_async.py` exists and passes
- [ ] `python/tests/test_executor_timeout.py` exists and passes
- [ ] `just test` exits 0 with no test count regression
- [ ] `just check` exits 0
- [ ] Each new file has a module-level docstring
- [ ] No test logic was changed — only file boundaries moved

## STOP conditions

Stop and report back (do not improvise) if:

- `test_executor.py` has changed since commit `7983e5d` (drift check fails)
- Any test references a fixture or import that doesn't resolve in its new file
- The test count after split doesn't match the original count
- `just check` reports new ruff/ty errors unrelated to the split

## Maintenance notes

- `test_executor_internals.py` remains unchanged — it tests internal helpers (`_handle_assertion_error`, `_compose`, `_repr_safe`)
- Future executor tests should go in the module matching their concern
- If timeout tests are later migrated from `test_timeout.py` (#1331), they should land in `test_executor_timeout.py`
