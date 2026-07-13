# Plan 006: Fix conftest module sys.modules poisoning on exec_module failure

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat f60b5a0..HEAD -- python/oxitest/_bridge/conftest_loader.py python/tests/test_conftest_loader.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `f60b5a0`, 2026-07-11
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1393

## Why this matters

`_load_conftest_module()` registers a module in `sys.modules` under two keys
*before* executing the module code. If `exec_module()` raises (syntax error,
import error, or runtime exception in conftest.py), the partially-initialized
module remains in `sys.modules` under both its unique name and the `"conftest"`
alias. Subsequent conftest loads or test imports from `conftest` see a
poisoned module with incomplete `__dict__`, producing cascading errors that
obscure the real failure. In parallel mode, one worker's conftest failure
contaminates the process-wide module cache for all subsequent tasks.

The sister module `_loader.py` already handles this correctly — it wraps
`exec()` in try/except and pops the module from `sys.modules` on failure.

## Current state

The fix target is `python/oxitest/_bridge/conftest_loader.py`, function
`_load_conftest_module()` at lines 111–121:

```python
# conftest_loader.py:111-121
def _load_conftest_module(path: str) -> ModuleType | None:
    """Load a conftest.py and register it as sys.modules['conftest']."""
    unique_name = f"_oxitest_conftest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module          # ← registered BEFORE exec
    spec.loader.exec_module(module)            # ← can raise
    sys.modules["conftest"] = module           # ← alias set unconditionally
    return module
```

The exemplar pattern is in `_loader.py:64-74`:

```python
# _loader.py:64-74
module = importlib.util.module_from_spec(spec)
# ...
sys.modules[unique_name] = module
try:
    exec(code, module.__dict__)
except Exception as exc:
    sys.modules.pop(unique_name, None)
    raise _LoadError(_error_result(traceback.format_exc())) from exc
return module
```

**Repo conventions:**
- Error handling wraps with try/except and re-raises with context (see `_loader.py`).
- Assertion messages explain *why* (oxitest `strict = "abort"`).
- Tests follow Arrange/Act/Assert with oxitest dogfooding (`oxi.raises()` not try/except).
- Tests live in `python/tests/test_conftest_loader.py`.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Build     | `just build`                                         | exit 0              |
| Tests     | `just test python/tests/test_conftest_loader.py`     | all pass            |
| Full test | `just test`                                          | all pass            |
| Typecheck | `ty check`                                           | exit 0              |
| Lint      | `ruff check python/oxitest/_bridge/conftest_loader.py` | exit 0            |

## Scope

**In scope** (the only files you should modify):
- `python/oxitest/_bridge/conftest_loader.py`
- `python/tests/test_conftest_loader.py`

**Out of scope** (do NOT touch):
- `python/oxitest/_bridge/_loader.py` — exemplar only, do not modify
- Any other bridge modules — this fix is isolated to conftest loading
- Rust code — no changes needed

## Git workflow

- Branch: `fix/006-conftest-module-poisoning`
- Commit style: conventional commits with issue number, e.g. `fix: clean up sys.modules on conftest exec failure (#ISSUE)`
- Commit trailer: `Assisted-by: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Wrap exec_module in try/except with sys.modules cleanup

In `conftest_loader.py`, modify `_load_conftest_module()` to:
1. Keep `sys.modules[unique_name] = module` before exec (CPython expects this for circular imports).
2. Wrap `spec.loader.exec_module(module)` in try/except.
3. On exception: pop both `unique_name` and `"conftest"` from `sys.modules`, then re-raise.
4. Only set `sys.modules["conftest"] = module` after successful exec.

Target shape:

```python
def _load_conftest_module(path: str) -> ModuleType | None:
    """Load a conftest.py and register it as sys.modules['conftest']."""
    unique_name = f"_oxitest_conftest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(unique_name, None)
        sys.modules.pop("conftest", None)
        raise
    sys.modules["conftest"] = module
    return module
```

Note: re-raise the original exception without wrapping — the caller
(`load_conftest_chain`) will surface it as a collection error.

**Verify**: `ruff check python/oxitest/_bridge/conftest_loader.py` → exit 0
**Verify**: `ty check` → exit 0

### Step 2: Add test for conftest exec failure cleanup

In `python/tests/test_conftest_loader.py`, add a test that:
1. Creates a temp conftest.py with a `raise RuntimeError("boom")` at module level.
2. Calls `_load_conftest_module()` with that path.
3. Asserts the RuntimeError propagates (use `oxi.raises(RuntimeError)`).
4. Asserts the unique name key is NOT in `sys.modules` after the failure.
5. Asserts `sys.modules.get("conftest")` is not the poisoned module.

Follow the existing test patterns in `test_conftest_loader.py`. Use `TempDir`
fixture for the temp file if available, otherwise a bare `pathlib.Path` in a
temp directory.

**Verify**: `just test python/tests/test_conftest_loader.py` → all pass, including the new test

### Step 3: Run full test suite

**Verify**: `just test` → all pass
**Verify**: `just check` → exit 0

## Test plan

- New test: conftest with `raise RuntimeError` → exception propagates, `sys.modules` cleaned up
- Edge case: conftest with `SyntaxError` (malformed Python) — if easy to add, include it
- Pattern: model after existing tests in `python/tests/test_conftest_loader.py`
- Verification: `just test python/tests/test_conftest_loader.py` → all pass

## Done criteria

- [ ] `_load_conftest_module()` wraps `exec_module` in try/except
- [ ] On failure, both `unique_name` and `"conftest"` are popped from `sys.modules`
- [ ] `sys.modules["conftest"]` is only set after successful exec
- [ ] New test verifying cleanup on conftest failure exists and passes
- [ ] `just test` exits 0
- [ ] `just check` exits 0 (includes ty, ruff, clippy)
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- The code at `conftest_loader.py:111-121` doesn't match the excerpt above.
- The existing tests in `test_conftest_loader.py` fail before your changes.
- Wrapping with try/except causes import-order side effects (tests that depend on conftest loading order start failing).
- You discover that `sys.modules["conftest"]` is read between the assignment at line 118 and the exec at line 119 (circular import during conftest loading).

## Maintenance notes

- If conftest loading is refactored to support async conftest in the future, the try/except pattern must be preserved.
- The `"conftest"` alias in `sys.modules` is a compatibility shim — if it's ever removed, the cleanup can be simplified.
- Reviewer should verify: does the bare `raise` preserve the original traceback? (Yes, bare `raise` re-raises without wrapping.)
