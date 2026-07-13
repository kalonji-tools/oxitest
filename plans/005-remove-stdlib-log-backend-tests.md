# Plan 005: Remove direct StdlibLogBackend tests from test_builtins.py

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise.
>
> **Drift check (run first)**: `git diff --stat 7983e5d..HEAD -- python/tests/test_builtins.py`

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `7983e5d`, 2026-07-10
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1332

## Why this matters

`test_builtins.py` lines ~708-772 test `StdlibLogBackend` (a private implementation class) directly. The public API is `LogCapture`, which has its own tests starting at line 774. The private backend tests add coupling to the implementation without testing user-visible behavior. If the backend is refactored or replaced, these tests break even though `LogCapture` still works.

## Current state

**Private backend tests** (lines 708-771, 4 tests):
```python
test_stdlib_backend_captures_records         # StdlibLogBackend.install() + logging → records
test_stdlib_backend_uninstall_removes_handler  # handler count before/after
test_stdlib_backend_uninstall_restores_level   # root logger level restoration
test_stdlib_backend_set_level_filters_records   # set_level() changes capture threshold
```

**Public LogCapture tests** (lines 774+, ~8 tests):
```python
test_logcapture_records_aggregates_backends
test_logcapture_text_formats_records
# ... more LogCapture tests
```

The behaviors tested by the private backend tests:
1. **Capture records** — covered by `test_logcapture_records_aggregates_backends` (line 777)
2. **Uninstall removes handler** — NOT directly covered by LogCapture tests (LogCapture.close() calls backend.uninstall(), but no test verifies handler count)
3. **Uninstall restores level** — NOT directly covered
4. **Set level filters** — need to check if LogCapture exposes level control

## Commands you will need

| Purpose   | Command                                       | Expected on success |
|-----------|-----------------------------------------------|---------------------|
| Test file | `just test python/tests/test_builtins.py`     | exit 0              |
| Check     | `just check`                                  | exit 0              |

## Scope

**In scope**: `python/tests/test_builtins.py` — remove private backend tests, add LogCapture behavioral equivalents if needed

**Out of scope**: `StdlibLogBackend` implementation, `LogCapture` implementation, `LogBackend` protocol

## Git workflow

- Conventional commits: `chore: remove direct StdlibLogBackend tests (#1332)`

## Steps

### Step 1: Read LogCapture tests and identify coverage gaps

Read the full LogCapture test section in `test_builtins.py` (from the `# ── LogCapture ──` comment to the end of the LogCapture section). For each private backend behavior (capture, uninstall/handler-removal, level-restoration, level-filtering), determine if it's covered through `LogCapture`.

**Verify**: Document which of the 4 behaviors have LogCapture coverage

### Step 2: Add missing behavioral tests through LogCapture

For any gap identified in Step 1, add a behavioral test that exercises the behavior through `LogCapture` (not through the private backend). Examples:

- If handler cleanup isn't tested: test that after `LogCapture.close()`, logging output goes back to normal
- If level filtering isn't tested: test `LogCapture` with a level parameter (if the public API supports it)

If `LogCapture`'s public API doesn't expose level control, that behavior is an implementation detail and doesn't need a test.

**Verify**: `just test python/tests/test_builtins.py` → all pass

### Step 3: Remove private backend tests

Delete the 4 `test_stdlib_backend_*` tests and the `StdlibLogBackend` import (if it's no longer needed).

**Verify**: `just test python/tests/test_builtins.py` → all pass

### Step 4: Clean up imports

Remove the `StdlibLogBackend` import from the test file if no remaining test uses it. Keep the `logging` import if LogCapture tests use it.

**Verify**: `just check` → exit 0

## Done criteria

- [ ] No test directly imports or instantiates `StdlibLogBackend`
- [ ] All user-visible LogCapture behaviors are still tested
- [ ] `just test python/tests/test_builtins.py` exits 0
- [ ] `just check` exits 0

## STOP conditions

- `test_builtins.py` has changed since commit `7983e5d`
- A private backend behavior has no LogCapture equivalent AND is user-visible (e.g., level filtering is part of the LogCapture public API)
- Removing tests causes other tests to fail

## Maintenance notes

- Future log backend changes should be tested through `LogCapture`, not through direct backend instantiation
- Plugin-provided log backends have their own test section and are unaffected
