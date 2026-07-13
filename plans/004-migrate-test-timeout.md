# Plan 004: Replace test_timeout.py private-internal tests with behavioral tests

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise.
>
> **Drift check (run first)**: `git diff --stat 7983e5d..HEAD -- python/tests/test_timeout.py python/tests/test_markers.py`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (but coordinates with plan 001 if test_executor_timeout.py exists)
- **Category**: tests
- **Planned at**: commit `7983e5d`, 2026-07-10
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1331

## Why this matters

`test_timeout.py` (148 lines, 12 tests) tests exclusively private internals: `_timeout_context()`, `_UnixTimeoutContext`/`_WindowsTimeoutContext`, `_TimeoutHandler`. If the timeout implementation is refactored (e.g., from signal-based to thread-based), these tests break even if the public `@mark.timeout` behavior is unchanged. The handler tests (lines 105-148) are well-written but belong in `test_markers.py` alongside the skip/xfail handler tests — this also closes #1333.

## Current state

`python/tests/test_timeout.py` contains:

**Private mechanism tests** (lines 21-63, 5 tests):
```python
test_timeout_context_raises_on_expiry          # tests _timeout_context directly
test_timeout_context_does_not_raise_when_fast   # tests _timeout_context directly
test_timeout_context_cancels_after_block        # tests signal.getitimer (platform detail)
test_oxitest_timeout_error_is_exception         # tests OxitestTimeoutError class hierarchy
test_timeout_context_type_matches_platform      # tests _UnixTimeoutContext/_WindowsTimeoutContext type
```

**Public mark tests** (lines 66-102, 3 tests):
```python
test_timeout_mark_rejects_invalid_seconds       # tests @mark.timeout validation (public API)
test_timeout_mark_stores_seconds                # tests mark metadata (public API)
```

**Handler tests** (lines 105-148, 3 tests):
```python
test_timeout_handler_returns_wrapper            # tests _TimeoutHandler.handle() — same pattern as skip/xfail in test_markers.py
test_timeout_handler_wrapper_passes_fast_test   # tests wrapper pass-through
test_timeout_handler_wrapper_returns_timeout_on_expiry  # tests wrapper timeout
```

`test_markers.py` has handler tests for skip (line 415) and xfail (line 445) but NOT timeout. The handler tests in `test_timeout.py` follow the exact same pattern.

Timeout *execution* tests exist in `test_executor.py` (lines 633-690 sync, 1103-1180 async) — these test `@mark.timeout` through `exec_inline` and verify the test result.

## Commands you will need

| Purpose   | Command                                       | Expected on success |
|-----------|-----------------------------------------------|---------------------|
| Test file | `just test python/tests/test_markers.py`      | exit 0              |
| Test all  | `just test`                                   | exit 0              |
| Check     | `just check`                                  | exit 0              |

## Scope

**In scope**:
- `python/tests/test_timeout.py` — DELETE
- `python/tests/test_markers.py` — ADD handler tests + public mark tests

**Out of scope**:
- `test_executor.py` timeout tests (or `test_executor_timeout.py` if plan 001 ran first)
- The timeout implementation itself

## Git workflow

- Conventional commits: `chore: migrate timeout tests to test_markers.py (#1331)`

## Steps

### Step 1: Audit executor timeout coverage

Read the timeout tests in `test_executor.py` (or `test_executor_timeout.py` if plan 001 already ran). Confirm these behaviors are tested:
- Timeout fires on slow test → TimeoutResult
- Fast test passes → PassedResult
- Default timeout fires
- No timeout when none configured
- Async timeout fires
- Async default timeout fires
- Async fast test passes
- Teardown runs even on timeout

All 8 should already exist. If any are missing, add them before deleting `test_timeout.py`.

**Verify**: Count timeout-related tests in executor file(s) — expect ≥8

### Step 2: Move handler tests to test_markers.py

Move the 3 handler tests from `test_timeout.py` (lines 105-148) to `test_markers.py`, placing them after the xfail handler tests. Add the necessary imports (`_TimeoutHandler`, `MappingProxyType`, `PassedResult`, `TimeoutResult`, `time`).

Also move the 2 public mark tests (lines 66-102): `test_timeout_mark_rejects_invalid_seconds` and `test_timeout_mark_stores_seconds`.

**Verify**: `just test python/tests/test_markers.py` → all pass, including the 5 moved tests

### Step 3: Verify private mechanism tests are already covered behaviorally

The 5 private mechanism tests (lines 21-63) test:
1. `_timeout_context` raises on expiry → covered by executor's `test_run_test_timeout_mark_fires`
2. `_timeout_context` doesn't raise when fast → covered by executor's `test_run_test_timeout_passes_fast_test`
3. Signal cancellation after block → platform implementation detail, not worth keeping
4. `OxitestTimeoutError` is `Exception` subclass → trivial type hierarchy check, not worth keeping
5. Platform-specific type matching → implementation detail, not worth keeping

If any behavior is NOT covered by executor tests, add a behavioral test via `exec_inline` before deleting.

### Step 4: Delete test_timeout.py

Delete `python/tests/test_timeout.py`.

**Verify**: `just test` → all pass

## Test plan

- No new tests needed (existing executor tests cover behavioral timeout).
- 5 tests migrate to `test_markers.py`, 5 are deleted (covered elsewhere or testing implementation details), 2 are deleted (platform/class hierarchy details).
- Net: 12 tests in `test_timeout.py` → 5 added to `test_markers.py`, 7 deleted.

## Done criteria

- [ ] `python/tests/test_timeout.py` no longer exists
- [ ] `test_markers.py` contains timeout handler tests alongside skip/xfail handler tests
- [ ] `test_markers.py` contains timeout mark validation tests
- [ ] `just test` exits 0
- [ ] `just check` exits 0
- [ ] No test imports `_timeout_context` or `_UnixTimeoutContext`/`_WindowsTimeoutContext`

## STOP conditions

- `test_timeout.py` or `test_markers.py` have changed since commit `7983e5d`
- An executor timeout behavior is missing coverage (add it first)
- Moving handler tests to `test_markers.py` causes import cycles

## Maintenance notes

- This plan also closes #1333 (coverage gap: timeout handler in test_markers.py)
- Future timeout handler changes should be tested in `test_markers.py` alongside skip/xfail
- Future timeout execution changes should be tested in the executor timeout test file
