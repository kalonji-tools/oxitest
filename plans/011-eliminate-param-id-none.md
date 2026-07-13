# Plan 011: Eliminate `param_id: str | None` across the test pipeline

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 3f6370c..HEAD -- python/oxitest/_bridge/_test_meta.py python/oxitest/_bridge/_fn_metadata.py python/oxitest/_bridge/result.py python/oxitest/_bridge/worker.py python/oxitest/_bridge/parametrize.py python/oxitest/_bridge/executor.py python/oxitest/_bridge/importer.py python/oxitest/_bridge/_builtin_context.py src/bridge.rs src/types/item.rs src/types/node_id.rs src/worker_result/wire.rs`
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

`param_id` flows through the entire test pipeline — collection, scheduling,
Rust→Python bridge, execution, and worker subprocess IPC. Today it is
`str | None` (Python) / `Option<String>` (Rust) everywhere, with `None`
meaning "not parametrized". But empty string `""` is an equally valid
zero-value: it is falsy in Python, `.is_empty()` in Rust, and parametrize
case IDs are never empty strings. Eliminating the `Option` removes:

- A `match` block in `bridge.rs` that converts `Option<String>` → `PyAny`
- `None` checks in `parametrize.py`, `worker.py`, and `executor.py`
- `Option<&str>` threading in `NodeId::new` and `wire.rs`

This is the single highest-fan-out None elimination in the codebase.

## Current state

### Python types carrying `param_id: str | None`

**`_test_meta.py:22`** — TestMeta (immutable identity bundle):
```python
@dataclass(frozen=True, slots=True)
class TestMeta:
    module_path: str
    fn_name: str
    node_id: str
    param_id: str | None = None
    markers: frozenset[str] = frozenset()
```

**`_fn_metadata.py:29`** — FunctionMetadata (decorator metadata):
```python
@dataclass(frozen=True, slots=True)
class FunctionMetadata:
    marks: tuple[MarkInfo, ...] = ()
    param_cases: tuple[ResolvedCases, ...] | None = None
    fixture_name: str | None = None
```
(Note: `param_cases` and `fixture_name` are also `| None` — see plan 013.)

**`result.py:318`** — CollectedItem (Python → Rust bridge):
```python
@dataclass(frozen=True, slots=True)
class CollectedItem:
    fn_name: str
    lineno: int
    markers: tuple[str, ...]
    param_id: str | None
    ...
```

**`worker.py:62`** — WorkerTaskItem (JSON wire protocol):
```python
class WorkerTaskItem(TypedDict):
    fn_name: str
    param_id: str | None
    node_id: str
    markers: list[str]
```

### Rust types carrying `param_id: Option<String>`

**`bridge.rs:163`** — CollectedItem (PyO3 extraction):
```rust
#[derive(FromPyObject)]
struct CollectedItem {
    fn_name: String,
    lineno: usize,
    markers: Vec<String>,
    param_id: Option<String>,
    ...
}
```

**`types/item.rs:216`** — TestItem (core type):
```rust
pub(crate) param_id: Option<String>,
```

**`types/node_id.rs:12`** — NodeId construction:
```rust
pub fn new(module_path: &str, fn_name: &str, param_id: Option<&str>) -> Self {
    let extra = param_id.map_or(0, |id| id.len() + 2);
    ...
    if let Some(id) = param_id {
        let _ = write!(s, "[{}]", id);
    }
}
```

**`worker_result/wire.rs:35`** — Wire protocol:
```rust
pub param_id: Option<&'a str>,
```

### Bridge conversion (the `match` this plan removes)

**`bridge.rs:561-564`**:
```rust
let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
    Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
    None => py.None().into_bound(py),
};
```

### Python consumption sites

**`parametrize.py:556`**:
```python
if param_id is None:
    return {}, frozenset()
```

**`worker.py:122`**:
```python
param_id=item.get("param_id"),
```

**`_builtin_context.py:80-81`** (TestContext property):
```python
@property
def param_id(self) -> str | None:
    return self._meta.param_id
```

### Repo conventions

- Frozen dataclasses: `@dataclass(frozen=True, slots=True)` — see `_test_meta.py` as exemplar.
- Conventional commits: `feat: add Foo (#42)` / `fix: bar (#43)`.
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
- `python/oxitest/_bridge/_test_meta.py`
- `python/oxitest/_bridge/result.py` (CollectedItem only)
- `python/oxitest/_bridge/worker.py` (WorkerTaskItem + run())
- `python/oxitest/_bridge/parametrize.py` (resolve_parametrize)
- `python/oxitest/_bridge/executor.py` (any `param_id is None` guards)
- `python/oxitest/_bridge/_builtin_context.py` (TestContext.param_id property)
- `python/oxitest/_bridge/importer.py` (CollectedItem construction)

Rust:
- `src/bridge.rs` (CollectedItem struct, param_id_obj match block)
- `src/types/item.rs` (TestItem.param_id)
- `src/types/node_id.rs` (NodeId::new signature)
- `src/types/test_support.rs` (TestItemBuilder)
- `src/worker_result/wire.rs` (WireResult.param_id)
- `src/parallel/drain.rs` (dummy param_id: None → "")
- `src/inspect/nav.rs` (dummy param_id: None → "")

**Out of scope** (do NOT touch):
- `_fn_metadata.py` `param_cases` and `fixture_name` — those are plan 013.
- Any public API surface or user-facing documentation — `TestContext.param_id`
  returns `str` instead of `str | None`, which is a narrowing (non-breaking).

## Git workflow

- Branch: `none-elim/011-param-id`
- Commit per logical unit; style: `fix: eliminate param_id Option across pipeline (#ISSUE)`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Change Python `param_id` types from `str | None` to `str`

In `_test_meta.py:22`, change:
```python
param_id: str | None = None
```
to:
```python
param_id: str = ""
```

In `result.py:318`, change:
```python
param_id: str | None
```
to:
```python
param_id: str
```

In `worker.py:62`, change:
```python
param_id: str | None
```
to:
```python
param_id: str
```

In `_builtin_context.py:80-81`, change the TestContext property return type:
```python
@property
def param_id(self) -> str | None:
```
to:
```python
@property
def param_id(self) -> str:
```

**Verify**: `just build` → exit 0 (Python changes don't break the Rust build)

### Step 2: Update Python consumption sites

In `parametrize.py:556`, change:
```python
if param_id is None:
    return {}, frozenset()
```
to:
```python
if not param_id:
    return {}, frozenset()
```

In `worker.py:122`, change:
```python
param_id=item.get("param_id"),
```
to:
```python
param_id=item.get("param_id", ""),
```

In `importer.py`, find all `CollectedItem(...)` construction sites and ensure
`param_id` receives a `str` (not `None`). Non-parametrized items should pass
`param_id=""`.

Search for any remaining `param_id is None` or `param_id is not None` in
`python/oxitest/_bridge/` and update to truthiness checks (`if param_id` /
`if not param_id`).

**Verify**: `grep -rn "param_id is None\|param_id is not None" python/oxitest/_bridge/` → no matches

### Step 3: Change Rust `param_id` types from `Option<String>` to `String`

In `bridge.rs:163`, change:
```rust
param_id: Option<String>,
```
to:
```rust
param_id: String,
```

In `types/item.rs:216`, change:
```rust
pub(crate) param_id: Option<String>,
```
to:
```rust
pub(crate) param_id: String,
```

In `worker_result/wire.rs:35`, change:
```rust
pub param_id: Option<&'a str>,
```
to:
```rust
pub param_id: &'a str,
```

### Step 4: Update Rust `NodeId::new` to accept `&str`

In `types/node_id.rs:12`, change:
```rust
pub fn new(module_path: &str, fn_name: &str, param_id: Option<&str>) -> Self {
    let extra = param_id.map_or(0, |id| id.len() + 2);
    ...
    if let Some(id) = param_id {
        let _ = write!(s, "[{}]", id);
    }
}
```
to:
```rust
pub fn new(module_path: &str, fn_name: &str, param_id: &str) -> Self {
    let extra = if param_id.is_empty() { 0 } else { param_id.len() + 2 };
    ...
    if !param_id.is_empty() {
        let _ = write!(s, "[{}]", param_id);
    }
}
```

### Step 5: Update all Rust call sites

In `bridge.rs:267`, change:
```rust
node_id: NodeId::new(path_str, &item.fn_name, item.param_id.as_deref()),
```
to:
```rust
node_id: NodeId::new(path_str, &item.fn_name, &item.param_id),
```

In `bridge.rs:561-564`, remove the match block entirely:
```rust
// BEFORE:
let param_id_obj: Bound<'_, PyAny> = match &item.param_id {
    Some(pid) => pid.as_str().into_pyobject(py)?.into_any(),
    None => py.None().into_bound(py),
};
// AFTER:
let param_id_obj = item.param_id.as_str().into_pyobject(py)?;
```

Update `bridge.rs:574` if needed — the `param_id_obj` is passed positionally
to `TestMeta(...)`.

In `types/test_support.rs`, update the builder:
- Change `param_id: Option<String>` → `param_id: String` (default `String::new()`)
- Change `fn param_id(mut self, id: String)` to set directly (no `Some()`)
- Update `NodeId::new` call at line 69

In `parallel/drain.rs:420` and `inspect/nav.rs:171,180`, change
`param_id: None` to `param_id: String::new()`.

Fix all remaining `Option`-related call sites (`.as_deref()`, `.is_none()`,
`Some(...)` wrapping) — the compiler will flag them.

**Verify**: `cargo check 2>&1 | head -50` → no errors

### Step 6: Update Rust tests

In `types/item.rs` tests (~lines 289-356), update:
- `param_id: Some("basic".to_string())` → `param_id: "basic".to_string()`
- `param_id: None` → `param_id: String::new()`
- `assert_eq!(item.param_id, Some("basic".to_string()))` → `assert_eq!(item.param_id, "basic")`
- `assert!(item.param_id.is_none())` → `assert!(item.param_id.is_empty())`
- Remove the `test_item_non_parametrize_has_none_param_id` test or rename to
  `test_item_non_parametrize_has_empty_param_id` and assert `.is_empty()`.

**Verify**: `just test-rust` → all pass

### Step 7: Full verification

**Verify**: `just preflight` → exit 0

## Test plan

- Existing tests cover parametrized and non-parametrized paths extensively.
  No new tests needed — this is a type narrowing, not a behavior change.
- The Rust test updates in Step 6 verify the new representation.
- Python integration tests (`just test-python`) exercise the full pipeline
  including worker subprocess IPC.

## Done criteria

- [ ] `just preflight` exits 0
- [ ] `grep -rn "param_id: Option" src/` returns no matches except in
      config-level code (CLI parsing where Option means "not set")
- [ ] `grep -rn "param_id.*None" python/oxitest/_bridge/` returns no matches
- [ ] No files outside the in-scope list are modified (`git diff --name-only`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- `CollectedItem.param_id` is used in Python as a dict key or set member
  where empty string would collide with a real value.
- Any Rust `serde` deserialization depends on `Option<String>` for JSON
  `null` handling in the worker wire protocol — check `wire.rs` carefully.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- If parametrize ever supports empty-string case IDs, this decision must be
  revisited. Currently oxitest requires non-empty case names (enforced in
  `parametrize.py`), so empty string is a safe sentinel.
- The worker JSON wire protocol change (`param_id: null` → `param_id: ""`)
  is backward-incompatible if old workers talk to new coordinators. This is
  fine because workers are always spawned by the same oxitest version.
- `TestContext.param_id` return type narrows from `str | None` to `str`.
  This is source-compatible for all callers (str is a subtype of str | None).
