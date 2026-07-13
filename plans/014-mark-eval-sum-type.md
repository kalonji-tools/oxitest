# Plan 014: Replace `MarkEvalResult` dual-None with sum type

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 3f6370c..HEAD -- python/oxitest/_bridge/_mark_registry.py python/oxitest/_bridge/executor.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `3f6370c`, 2026-07-13

## Why this matters

`MarkEvalResult` encodes a 3-variant sum type as a product with two
`| None` fields:

```python
@dataclass(frozen=True, slots=True)
class MarkEvalResult:
    short_circuit: TestResult | None = None  # variant 1: skip execution
    wrapper: MarkWrapper | None = None       # variant 2: wrap execution
    # implicit variant 3: both None = pass through
```

Both fields are mutually exclusive — a mark either short-circuits OR wraps,
never both. The consumer (`evaluate_marks`) checks each field with
`is not None`. This same dual-None pattern extends to the return type:
`tuple[TestResult | None, list[MarkWrapper]]`.

Replacing this with a proper discriminated union makes the three states
explicit and removes 4 `is not None` checks.

## Current state

**`_mark_registry.py:43-52`** — MarkEvalResult:
```python
@dataclasses.dataclass(frozen=True, slots=True)
class MarkEvalResult:
    short_circuit: TestResult | None = None
    wrapper: MarkWrapper | None = None
```

**`_mark_registry.py:67-70`** — _SkipHandler returns short_circuit:
```python
def handle(self, mark: MarkInfo) -> MarkEvalResult:
    reason = mark.kwargs.get("reason") or (mark.args[0] if mark.args else "")
    return MarkEvalResult(short_circuit=SkippedResult(message=str(reason)))
```

**`_mark_registry.py:101`** — _TimeoutHandler returns wrapper:
```python
return MarkEvalResult(wrapper=make_timeout_wrapper(seconds))
```

**`_mark_registry.py:131`** — _PluginMarkHandler returns wrapper:
```python
return MarkEvalResult(wrapper=wrapper)
```

**`_mark_registry.py:153-184`** — evaluate_marks function:
```python
def evaluate_marks(
    marks: Sequence[MarkInfo],
    session: _SessionProtocol,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    plugin_handlers: list[MarkHandler] | None = None,
) -> tuple[TestResult | None, list[MarkWrapper]]:
    ...
    for mark in marks:
        ...
        result = handler.handle(mark)
        if result.short_circuit is not None:
            return result.short_circuit, []
        if result.wrapper is not None:
            wrappers.append(result.wrapper)
    return None, wrappers
```

**`executor.py:233-253`** — _evaluate_marks_phase:
```python
def _evaluate_marks_phase(
    resolved: _ResolvedTest,
    session: _SessionProtocol,
    module_path: str,
    marks: Sequence[MarkInfo],
) -> tuple[TestResult | None, list[MarkWrapper]]:
    ...
    return evaluate_marks(
        marks,
        session,
        module_path,
        resolved.fn_teardowns,
        plugin_handlers=_plugin_handlers or None,
    )
```

### Repo conventions

- Union types use `X | Y` syntax (not `Union[X, Y]`).
- Frozen dataclasses: `@dataclass(frozen=True, slots=True)`.
- See `result.py` `TestResult` union as exemplar for type union pattern.

## Commands you will need

| Purpose    | Command              | Expected on success |
|------------|----------------------|---------------------|
| Build      | `just build`         | exit 0              |
| Typecheck  | `just check`         | exit 0, no errors   |
| Py tests   | `just test-python`   | all pass            |
| Preflight  | `just preflight`     | exit 0              |

## Scope

**In scope** (the only files you should modify):
- `python/oxitest/_bridge/_mark_registry.py`
- `python/oxitest/_bridge/executor.py` (_evaluate_marks_phase, run_test)

**Out of scope** (do NOT touch):
- `_mark_api.py` — mark definition, not evaluation.
- Plugin mark handler interface — the `MarkHandler.handle()` return type
  changes, but the ABC is in-scope. External plugin implementations do not
  exist yet (the system is internal).
- Rust code — mark evaluation is Python-only.

## Git workflow

- Branch: `none-elim/014-mark-eval`
- Commit per step; style: `fix: replace MarkEvalResult dual-None with sum type (#ISSUE)`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Define the `MarkAction` sum type

In `_mark_registry.py`, replace `MarkEvalResult` with three frozen dataclasses
and a union type:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class ShortCircuit:
    """Mark evaluation result: skip test execution, return this result."""
    result: TestResult

@dataclasses.dataclass(frozen=True, slots=True)
class Wrap:
    """Mark evaluation result: wrap the test execution with this callable."""
    wrapper: MarkWrapper

@dataclasses.dataclass(frozen=True, slots=True)
class PassThrough:
    """Mark evaluation result: no effect on execution."""

MarkAction = ShortCircuit | Wrap | PassThrough
_PASS_THROUGH = PassThrough()
```

Update `__all__` to export `MarkAction`, `ShortCircuit`, `Wrap`, `PassThrough`
instead of `MarkEvalResult`.

**Verify**: File parses without syntax errors: `python -c "import oxitest._bridge._mark_registry"` (after `just build`)

### Step 2: Update mark handlers to return `MarkAction`

Change the `MarkHandler` ABC:
```python
class MarkHandler(ABC):
    mark_name: str = ""
    @abstractmethod
    def handle(self, mark: MarkInfo) -> MarkAction: ...
```

Update each handler:

**_SkipHandler.handle** — return `ShortCircuit(SkippedResult(...))`:
```python
def handle(self, mark: MarkInfo) -> MarkAction:
    reason = mark.kwargs.get("reason") or (mark.args[0] if mark.args else "")
    return ShortCircuit(SkippedResult(message=str(reason)))
```

**_XFailHandler.handle** — return `Wrap(xfail_wrapper)`:
```python
return Wrap(xfail_wrapper)
```

**_TimeoutHandler.handle** — return `Wrap(make_timeout_wrapper(...))`:
```python
return Wrap(make_timeout_wrapper(seconds))
```

**_PluginMarkHandler.handle** — return `Wrap(wrapper)`:
```python
return Wrap(wrapper)
```

**Verify**: `just build` → exit 0

### Step 3: Update `evaluate_marks` to use pattern matching

Replace the `is not None` checks with `isinstance` dispatch:

```python
def evaluate_marks(
    marks: Sequence[MarkInfo],
    session: _SessionProtocol,
    module_path: str,
    fn_teardowns: list[Callable[[], None]],
    plugin_handlers: Sequence[MarkHandler] = (),
) -> tuple[TestResult | None, list[MarkWrapper]]:
    registry = _MARK_REGISTRY
    if plugin_handlers:
        registry = {**_MARK_REGISTRY, **{h.mark_name: h for h in plugin_handlers}}
    wrappers: list[MarkWrapper] = []
    for mark in marks:
        if mark.name == "usefixtures":
            for fx_name in mark.args:
                session.get_fixture(str(fx_name), module_path, fn_teardowns)
            continue
        handler = registry.get(mark.name)
        if handler is None:
            continue
        action = handler.handle(mark)
        if isinstance(action, ShortCircuit):
            return action.result, []
        if isinstance(action, Wrap):
            wrappers.append(action.wrapper)
    return None, wrappers
```

Note: The `evaluate_marks` return type `tuple[TestResult | None, ...]` still
uses `None` in its return. This is acceptable — the function returns to the
executor which uses a simple `if short_circuit is not None` check. Changing
this return type would ripple into the executor and is a lower-leverage change.
The primary win is eliminating the dual-None on `MarkEvalResult`.

Also note: `plugin_handlers` parameter changes from `list[...] | None` to
`Sequence[...] = ()` — this eliminates one more `| None`.

**Verify**: `just build && just test-python` → all pass

### Step 4: Update executor

In `executor.py:252`, the call `plugin_handlers=_plugin_handlers or None`
becomes `plugin_handlers=_plugin_handlers` (since the parameter now defaults
to `()`).

**Verify**: `just preflight` → exit 0

## Test plan

- Existing tests cover skip, xfail, timeout, usefixtures, and plugin marks.
- No new tests needed — this is a type-level refactor.
- `just test-python` exercises all mark handler paths.

## Done criteria

- [ ] `just preflight` exits 0
- [ ] `MarkEvalResult` class no longer exists in `_mark_registry.py`
- [ ] `grep -rn "MarkEvalResult" python/oxitest/` → no matches
- [ ] `grep -rn "short_circuit is not None\|\.wrapper is not None" python/oxitest/_bridge/_mark_registry.py` → no matches
- [ ] No files outside the in-scope list are modified (`git diff --name-only`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- Any external code (outside `_bridge/`) imports `MarkEvalResult` — check
  with `grep -rn "MarkEvalResult" python/`.
- `ty check` complains about the `isinstance` dispatch — ty may not narrow
  union types through isinstance the same way mypy does. If so, report the
  specific error.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- The `evaluate_marks` return type still uses `TestResult | None`. A follow-up
  could wrap this in a `MarkEvaluation` dataclass, but the current return
  is consumed in exactly one place (`_evaluate_marks_phase`) and is simple
  enough.
- Future mark handlers must return one of `ShortCircuit`, `Wrap`, or
  `PassThrough` (via `_PASS_THROUGH` singleton). The ABC enforces the
  return type.
- `_PASS_THROUGH` is a module-level singleton for the common case. Custom
  marks that have no effect should return it.
