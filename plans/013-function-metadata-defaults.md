# Plan 013: Default FunctionMetadata optional fields and use no-op teardown

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 3f6370c..HEAD -- python/oxitest/_bridge/_fn_metadata.py python/oxitest/_bridge/_fixture_instantiator.py python/oxitest/_bridge/parametrize.py python/oxitest/_bridge/_fixture_registry.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `3f6370c`, 2026-07-13

## Why this matters

`FunctionMetadata` has two `| None` fields that have natural zero-values:

- `param_cases: tuple[ResolvedCases, ...] | None = None` — empty tuple `()`
  means "no parametrize cases", identical to None semantically.
- `fixture_name: str | None = None` — empty string `""` means "not a fixture
  definition", since fixture names are always non-empty.

Additionally, `_FixtureOutcome.teardown: Callable | None = None` forces a
None guard at every use site when a simple no-op callable would suffice.

These are three small, independent fixes that collectively remove 3 `| None`
types and their associated guards.

## Current state

**`_fn_metadata.py:26-31`** — FunctionMetadata:
```python
@dataclass(frozen=True, slots=True)
class FunctionMetadata:
    marks: tuple[MarkInfo, ...] = ()
    param_cases: tuple[ResolvedCases, ...] | None = None
    fixture_name: str | None = None
```

**`parametrize.py:556-566`** — `resolve_parametrize` consumes `param_cases`:
```python
if param_id is None:
    return {}, frozenset()
layers = get_metadata(fn_raw).param_cases
if layers is None:
    fn_name = getattr(fn_raw, "__name__", repr(fn_raw))
    msg = (
        f"resolve_parametrize: {fn_name!r} has no parametrize cases"
        f" but param_id={param_id!r} was requested."
        " Use @oxitest.parametrize to register cases."
    )
    raise ParametrizeError(msg)
```

**`_fixture_instantiator.py:110-131`** — _FixtureOutcome:
```python
@dataclass(frozen=True, slots=True)
class _FixtureOutcome:
    value: Any
    teardown: Callable[[], None] | None = None

def _unpack_sync(result: Any, name: str) -> _FixtureOutcome:
    if inspect.isgenerator(result):
        value = next(result)
        def teardown(gen: Any = result, fixture_name: str = name) -> None:
            def _drain() -> None:
                with contextlib.suppress(StopIteration):
                    next(gen)
            safe_teardown(_drain, fixture_name, warn=_warn_teardown)
        return _FixtureOutcome(value, teardown)
    return _FixtureOutcome(result)
```

**`_fixture_instantiator.py:375`** — teardown None check:
```python
if outcome.teardown is not None:
    _original_td = outcome.teardown
    ...
    scope_teardowns.append(_timed_teardown)
```

**`_fixture_registry.py`** — `fixture_name` is used in conftest loader to
tag functions decorated with `@fixtures.fixture`. Search for
`fixture_name` to find all assignment/read sites.

### Repo conventions

- Frozen dataclasses: `@dataclass(frozen=True, slots=True)` — see
  `_fn_metadata.py` as exemplar.
- No-op callables: use `lambda: None` or a named no-op function.

## Commands you will need

| Purpose    | Command              | Expected on success |
|------------|----------------------|---------------------|
| Build      | `just build`         | exit 0              |
| Typecheck  | `just check`         | exit 0, no errors   |
| Py tests   | `just test-python`   | all pass            |
| Preflight  | `just preflight`     | exit 0              |

## Scope

**In scope** (the only files you should modify):
- `python/oxitest/_bridge/_fn_metadata.py`
- `python/oxitest/_bridge/parametrize.py`
- `python/oxitest/_bridge/_fixture_instantiator.py`
- Any file that reads `FunctionMetadata.fixture_name` with `is None` /
  `is not None` checks (search with grep)

**Out of scope** (do NOT touch):
- `_test_meta.py` `param_id` — that is plan 011.
- Rust code — these are Python-only changes.

## Git workflow

- Branch: `none-elim/013-fn-metadata`
- Commit per step; style: `fix: default FunctionMetadata fields (#ISSUE)`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Change `param_cases` from `| None` to `= ()`

In `_fn_metadata.py:29`, change:
```python
param_cases: tuple[ResolvedCases, ...] | None = None
```
to:
```python
param_cases: tuple[ResolvedCases, ...] = ()
```

In `parametrize.py:559`, change:
```python
if layers is None:
```
to:
```python
if not layers:
```

Search for any other `param_cases is None` or `param_cases is not None`
checks and update to truthiness.

**Verify**: `grep -rn "param_cases is None\|param_cases is not None" python/oxitest/` → no matches

### Step 2: Change `fixture_name` from `| None` to `= ""`

In `_fn_metadata.py:30`, change:
```python
fixture_name: str | None = None
```
to:
```python
fixture_name: str = ""
```

Search for all `fixture_name is None` / `fixture_name is not None` checks:
```bash
grep -rn "fixture_name is None\|fixture_name is not None" python/oxitest/
```

Update each site to use truthiness (`if fixture_name` / `if not fixture_name`).

**Verify**: `grep -rn "fixture_name is None\|fixture_name is not None" python/oxitest/` → no matches

### Step 3: Replace `_FixtureOutcome.teardown` None with no-op

In `_fixture_instantiator.py:110-115`, change:
```python
@dataclass(frozen=True, slots=True)
class _FixtureOutcome:
    value: Any
    teardown: Callable[[], None] | None = None
```
to:
```python
def _noop() -> None:
    """No-op teardown for non-generator fixtures."""

@dataclass(frozen=True, slots=True)
class _FixtureOutcome:
    value: Any
    teardown: Callable[[], None] = _noop
```

Define `_noop` as a module-level function (not a lambda) for clarity and
debuggability.

In `_fixture_instantiator.py:375`, change:
```python
if outcome.teardown is not None:
```
to:
```python
if outcome.teardown is not _noop:
```

This preserves the optimization of not wrapping no-op teardowns in the
timing decorator. Using identity check (`is not _noop`) is correct because
`_noop` is a singleton module-level function.

**Verify**: `grep -rn "teardown is None\|teardown is not None" python/oxitest/_bridge/_fixture_instantiator.py` → no matches

### Step 4: Full verification

**Verify**: `just preflight` → exit 0

## Test plan

- Existing tests cover parametrized, non-parametrized, generator fixtures,
  and plain fixtures extensively. No new tests needed.
- Pattern to follow: `marks: tuple[MarkInfo, ...] = ()` on the same
  dataclass already uses the empty-tuple default.

## Done criteria

- [ ] `just preflight` exits 0
- [ ] `grep -rn "param_cases.*None\|fixture_name.*None" python/oxitest/_bridge/_fn_metadata.py` → no matches
- [ ] `grep -rn "teardown.*None" python/oxitest/_bridge/_fixture_instantiator.py` → no matches (in type annotations)
- [ ] No files outside the in-scope list are modified (`git diff --name-only`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- Any code path distinguishes between `param_cases = ()` and
  `param_cases = None` semantically (i.e., "no decorator" vs "decorator
  with zero cases" — if these are different states, the None is meaningful).
- The `_noop` identity check breaks because `_FixtureOutcome` is serialized
  or crosses a process boundary where identity is lost.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- `_noop` is a module-level singleton. If `_FixtureOutcome` ever crosses
  a pickle/JSON boundary (e.g., for distributed testing), the identity
  check would need a different approach.
- The `fixture_name = ""` convention means empty string is "not a fixture".
  This is consistent with how `param_id = ""` works after plan 011.
