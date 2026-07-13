# Plan 012: Replace `keep_tmp: str | None` with tri-state Literal

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 3f6370c..HEAD -- python/oxitest/_bridge/_fixture_context.py python/oxitest/_bridge/_builtin_context.py python/oxitest/_bridge/executor.py python/oxitest/_bridge/worker.py python/oxitest/_bridge/_fixture_instantiator.py python/oxitest/_bridge/_builtins/ src/bridge.rs src/config/mod.rs src/worker_session.rs`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `3f6370c`, 2026-07-13

## Why this matters

`keep_tmp` controls TempDir cleanup behavior with three states: clean up
(default), keep on failure, keep always. Today this is encoded as
`str | None` — `None` means "cleanup", `"failed"` means keep on failure,
`"always"` means keep unconditionally. The None encoding cascades:

1. `executor.py:305-312` — conditional `TestRunContext` creation
2. `_fixture_context.py:44-45` — `result_cell: list[Any] | None` is linked
3. `_fixture_instantiator.py:403-405` — `run_ctx.keep_tmp if run_ctx else None`
4. `bridge.rs:583-586` — `match opts.keep_tmp` Option→PyAny conversion

Replacing `None` with `"cleanup"` eliminates the conditional creation of
`TestRunContext` and makes `result_cell` always present (empty list vs
populated list, not None vs list).

## Current state

### Python types

**`_fixture_context.py:40-49`** — TestRunContext and its ContextVar:
```python
@dataclass(frozen=True, slots=True)
class TestRunContext:
    keep_tmp: str | None = None
    result_cell: list[Any] | None = None

_test_run_context: ContextVar[TestRunContext | None] = ContextVar(
    "_test_run_context", default=None
)
```

**`_builtin_context.py:24-25`** — _BuiltinContext:
```python
keep_tmp: str | None = None
result_cell: list[Any] | None = field(default=None, repr=False)
```

**`executor.py:267-312`** — run_test signature and TestRunContext creation:
```python
def run_test(
    meta: TestMeta,
    session: _SessionProtocol | None = None,
    default_timeout: int | None = None,
    keep_tmp: str | None = None,
    ...
) -> TestResult:
    ...
    _run_ctx = (
        TestRunContext(
            keep_tmp=keep_tmp,
            result_cell=[None] if keep_tmp else None,
        )
        if keep_tmp is not None
        else None
    )
    _run_ctx_token = _test_run_context.set(_run_ctx)
```

**`_fixture_instantiator.py:403-405`** — builtin injection:
```python
run_ctx = _test_run_context.get()
_keep_tmp = run_ctx.keep_tmp if run_ctx else None
_result_cell = run_ctx.result_cell if run_ctx else None
```

**`worker.py:101`** — worker task extraction:
```python
keep_tmp: str | None = task.get("keep_tmp")
```

### Rust types

**`config/mod.rs:155-160`** — KeepTmpMode enum (already exists!):
```rust
pub enum KeepTmpMode {
    Failed,
    Always,
}
```

**`bridge.rs:583-586`** — Option→PyAny conversion:
```rust
let keep_tmp_obj: Bound<'_, PyAny> = match opts.keep_tmp {
    Some(mode) => mode.into_pyobject(py)?.into_any(),
    None => py.None().into_bound(py),
};
```

**`worker_session.rs:169`**:
```rust
pub keep_tmp: Option<std::sync::Arc<str>>,
```

### Repo conventions

- Frozen dataclasses: `@dataclass(frozen=True, slots=True)`
- `Literal` types used sparingly; `StrEnum` preferred for multi-use enums
  (see `StatusKind` in `result.py:37` as exemplar).
- `just preflight` is the pre-push gate.

## Commands you will need

| Purpose    | Command                        | Expected on success         |
|------------|--------------------------------|-----------------------------|
| Build      | `just build`                   | exit 0                      |
| Typecheck  | `just check`                   | exit 0, no errors           |
| Rust tests | `just test-rust`               | all pass                    |
| Py tests   | `just test-python`             | all pass                    |
| Preflight  | `just preflight`               | exit 0                      |

## Scope

**In scope** (the only files you should modify):

Python:
- `python/oxitest/_bridge/_fixture_context.py` (TestRunContext, ContextVar)
- `python/oxitest/_bridge/_builtin_context.py` (_BuiltinContext fields)
- `python/oxitest/_bridge/executor.py` (run_test, _fixture_instantiator calls)
- `python/oxitest/_bridge/_fixture_instantiator.py` (inject_builtin)
- `python/oxitest/_bridge/worker.py` (WorkerTask, run())
- Any `_builtins/` file that reads `keep_tmp` or `result_cell`

Rust:
- `src/config/mod.rs` (add `Cleanup` variant to `KeepTmpMode`)
- `src/bridge.rs` (remove match block, send mode string directly)
- `src/worker_session.rs` (change `keep_tmp` type)
- `src/config/merge.rs` (update merge logic for non-optional `keep_tmp`)

**Out of scope** (do NOT touch):
- CLI parsing (`config/cli/run.rs`, `config/cli/debug.rs`) — these correctly
  use `Option<KeepTmpMode>` for "user didn't set this flag".
- `pyproject.toml` config parsing — same reasoning.

## Git workflow

- Branch: `none-elim/012-keep-tmp`
- Commit per logical unit; style: `fix: eliminate keep_tmp None with Cleanup variant (#ISSUE)`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add `Cleanup` variant to Rust `KeepTmpMode`

In `src/config/mod.rs:155`, change:
```rust
pub enum KeepTmpMode {
    Failed,
    Always,
}
```
to:
```rust
pub enum KeepTmpMode {
    /// Clean up temp dirs after the test (default).
    Cleanup,
    /// Preserve temp dirs only when the test fails.
    Failed,
    /// Preserve every temp dir regardless of outcome.
    Always,
}
```

Update the `Default` impl or any default-construction sites to use
`KeepTmpMode::Cleanup`.

In `src/config/mod.rs`, change `OutputConfig.keep_tmp` from
`Option<KeepTmpMode>` to `KeepTmpMode` with `#[serde(default)]` using
`Cleanup` as the default. Update `OutputConfig::default()` accordingly.

**Important**: Config *merging* (`merge.rs`) still needs to distinguish
"user set this" from "not set" during CLI/pyproject merge. The merge
intermediary struct can keep `Option<KeepTmpMode>`, but the final resolved
`OutputConfig` should have `KeepTmpMode` (never None). Check how other
fields handle this pattern — follow the existing convention.

**Verify**: `cargo check` → no errors

### Step 2: Update Rust bridge to send mode string directly

In `bridge.rs:583-586`, change:
```rust
let keep_tmp_obj: Bound<'_, PyAny> = match opts.keep_tmp {
    Some(mode) => mode.into_pyobject(py)?.into_any(),
    None => py.None().into_bound(py),
};
```
to send the mode string directly (e.g., `"cleanup"`, `"failed"`, `"always"`).
Since `KeepTmpMode` likely implements `Display` or can be converted to `&str`,
use that. If not, add a `.as_str()` method or `Display` impl.

Update `worker_session.rs:169` — change `keep_tmp: Option<Arc<str>>` to
`keep_tmp: Arc<str>` (defaulting to `"cleanup"`). Update all construction
and usage sites.

**Verify**: `cargo check` → no errors

### Step 3: Update Python `TestRunContext` and `_BuiltinContext`

In `_fixture_context.py:40-49`, change:
```python
@dataclass(frozen=True, slots=True)
class TestRunContext:
    keep_tmp: str | None = None
    result_cell: list[Any] | None = None

_test_run_context: ContextVar[TestRunContext | None] = ContextVar(
    "_test_run_context", default=None
)
```
to:
```python
@dataclass(frozen=True, slots=True)
class TestRunContext:
    keep_tmp: str = "cleanup"
    result_cell: list[Any] = field(default_factory=list)

_test_run_context: ContextVar[TestRunContext] = ContextVar(
    "_test_run_context", default=TestRunContext()
)
```

In `_builtin_context.py:24-25`, change:
```python
keep_tmp: str | None = None
result_cell: list[Any] | None = field(default=None, repr=False)
```
to:
```python
keep_tmp: str = "cleanup"
result_cell: list[Any] = field(default_factory=list, repr=False)
```

**Verify**: `just build` → exit 0

### Step 4: Update `executor.py` — always create TestRunContext

In `executor.py:267-312`, change the `run_test` signature:
```python
keep_tmp: str | None = None,
```
to:
```python
keep_tmp: str = "cleanup",
```

Replace the conditional TestRunContext creation:
```python
_run_ctx = (
    TestRunContext(
        keep_tmp=keep_tmp,
        result_cell=[None] if keep_tmp else None,
    )
    if keep_tmp is not None
    else None
)
```
with unconditional creation:
```python
_run_ctx = TestRunContext(
    keep_tmp=keep_tmp,
    result_cell=[None] if keep_tmp != "cleanup" else [],
)
```

The `result_cell` is `[None]` when keep_tmp is active (to hold the result
for TempDir cleanup decisions), and `[]` (empty) when cleanup mode.

**Verify**: `just build` → exit 0

### Step 5: Update `_fixture_instantiator.py` — remove None guards

In `_fixture_instantiator.py:403-405`, change:
```python
run_ctx = _test_run_context.get()
_keep_tmp = run_ctx.keep_tmp if run_ctx else None
_result_cell = run_ctx.result_cell if run_ctx else None
```
to:
```python
run_ctx = _test_run_context.get()
_keep_tmp = run_ctx.keep_tmp
_result_cell = run_ctx.result_cell
```

Update any downstream code that checks `if _keep_tmp is not None` to check
`if _keep_tmp != "cleanup"` or simply `if _keep_tmp` (but NOT truthiness —
`"cleanup"` is truthy). Use `!= "cleanup"` for clarity.

Search all `_builtins/` files for `keep_tmp` and `result_cell` usage and
update None guards similarly.

**Verify**: `grep -rn "keep_tmp is None\|keep_tmp is not None\|result_cell is None\|result_cell is not None" python/oxitest/_bridge/` → no matches

### Step 6: Update `worker.py`

In `worker.py:101`, change:
```python
keep_tmp: str | None = task.get("keep_tmp")
```
to:
```python
keep_tmp: str = task.get("keep_tmp", "cleanup")
```

In `WorkerTask` TypedDict, `keep_tmp` is already `NotRequired[str]` (line 77).
The default via `.get("keep_tmp", "cleanup")` handles the missing-key case.

**Verify**: `just build && just test-python` → all pass

### Step 7: Update Rust tests and full verification

Update any Rust tests that construct `keep_tmp: None` to use
`KeepTmpMode::Cleanup` or the string `"cleanup"`.

**Verify**: `just preflight` → exit 0

## Test plan

- Existing tests cover all three keep_tmp modes (cleanup, failed, always).
- No new tests needed — this is a representation change, not a behavior change.
- The Python integration tests exercise TempDir preservation end-to-end.
- Pattern to follow: see `result.py:37` (`StatusKind` StrEnum) for enum exemplar.

## Done criteria

- [ ] `just preflight` exits 0
- [ ] `grep -rn "keep_tmp.*None\|keep_tmp is None" python/oxitest/_bridge/` → no matches
- [ ] `grep -rn "result_cell.*None\|result_cell is None" python/oxitest/_bridge/` → no matches
- [ ] `KeepTmpMode` in Rust has three variants (Cleanup, Failed, Always)
- [ ] No files outside the in-scope list are modified (`git diff --name-only`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- The Rust config merge system (`merge.rs`) breaks because it depends on
  `Option<KeepTmpMode>` semantics — investigate how other config fields
  handle the "not set" vs "default" distinction.
- Any `_builtins/` file uses `result_cell is None` as a semantic signal
  (meaning "TempDir result tracking disabled") — the replacement is
  checking for empty list, which must be equivalent.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- The config merge system distinguishes "user didn't set keep_tmp" from
  "user set cleanup". If a future feature needs this distinction on the
  Python/execution side, it would need a separate mechanism. Currently
  the distinction is only needed during config resolution.
- The `result_cell` pattern (`[None]` as a mutable slot for result storage)
  is unusual. A future refactor could replace it with a proper callback
  or return-value channel. That's out of scope for this plan.
- `keep_tmp != "cleanup"` is the new idiom for "keep_tmp is active".
  All builtins that check this must use the string comparison, not
  truthiness (since `"cleanup"` is truthy).
