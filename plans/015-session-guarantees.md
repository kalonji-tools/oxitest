# Plan 015: Guarantee plugin_registry and module_cache on session; restructure fixture resolution

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 3f6370c..HEAD -- python/oxitest/_bridge/_fixture_session.py python/oxitest/_bridge/_fixture_instantiator.py python/oxitest/_bridge/executor.py python/oxitest/_bridge/importer.py python/oxitest/_bridge/_builtin_context.py python/oxitest/_bridge/plugin_loader.py python/oxitest/_bridge/_fixture_validator.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `3f6370c`, 2026-07-13

## Why this matters

Three session-level attributes are accessed via defensive `getattr(..., None)`
with downstream None guards, even though `FixtureSession` always has them:

1. **`_plugin_registry`** — accessed at 3 sites via `getattr(session, "_plugin_registry", None)`,
   each with `if ... is not None` guard. The `_SessionProtocol` doesn't declare it,
   forcing defensive access.
2. **`_module_cache`** — accessed at 2 sites via `getattr(session, "_module_cache", None)`,
   with 3 nested None checks per site.
3. **Fixture resolution** in `_fixture_instantiator.py:208-230` — uses `defn = None`
   as intermediate state after a caught exception, then 3 nested `is not None`
   checks to decide the resolution path.

Adding `plugin_registry` and `module_cache` to `_SessionProtocol` eliminates
the `getattr` pattern. Restructuring fixture resolution eliminates the
intermediate None.

## Current state

### Plugin registry access pattern

**`executor.py:95`** — debugger resolution:
```python
registry = getattr(session, "_plugin_registry", None)
if registry is not None and registry.debugger_backend is not None:
    return registry.debugger_backend
```

**`executor.py:240-244`** — mark evaluation:
```python
_plugin_registry = getattr(session, "_plugin_registry", None)
_plugin_handlers: list[MarkHandler] = []
if _plugin_registry is not None:
    _plugin_handlers = [
        _PluginMarkHandler(pw) for pw in _plugin_registry.execution_wrappers
    ]
```

**`importer.py:357`** — collection:
```python
cache = getattr(session, "_module_cache", None)
```

### Module cache access pattern

**`executor.py:128-139`** — test loading:
```python
_cache = getattr(session, "_module_cache", None)
_cached = _cache.get(meta.module_path) if _cache is not None else None
if _cached is not None:
    module = _cached
    sys.modules[unique_name] = module
else:
    ...
    if _cache is not None:
        _cache.set(meta.module_path, module)
```

### _SessionProtocol (missing plugin_registry and module_cache)

**`_fixture_session.py:62-99`** — protocol definition:
```python
class _SessionProtocol(Protocol):
    def resolve_for_test(...) -> ...: ...
    def get_fixture(...) -> Any: ...
    def get_fixture_in_namespace(...) -> Any: ...
    def get_namespace_for_func(...) -> str | None: ...
    def inject_builtin(...) -> Any: ...
    # NOTE: no plugin_registry or module_cache properties
```

### FixtureSession (has both attributes)

**`_fixture_session.py:260`**: `self._module_cache = ModuleCache()`
**`_fixture_session.py` (at init)**: `self._plugin_registry = PluginRegistry(...)`

### Fixture resolution None cascade

**`_fixture_instantiator.py:207-230`**:
```python
try:
    defn = self._registry.resolve(inner, qualifier=param_name)
except FixtureNotFoundError:
    defn = None

if defn is not None and not isinstance(defn.source, ConftestSource):
    return True, self._resolve_by_source(...)

resolve_name = (
    param_name
    if self._registry.get(param_name) is not None
    else defn.name
    if defn is not None
    else None
)
if resolve_name is None:
    raise FixtureNotFoundError(param_name)
return True, resolve_user_fixture(resolve_name)
```

### PluginRegistry empty construction

**`plugin_loader.py`** — PluginRegistry is a frozen dataclass. Check if it
can be constructed with no arguments as an empty registry (all fields have
defaults). If not, a factory function or class method is needed.

### Repo conventions

- Protocol classes: `class _SessionProtocol(Protocol):` in `_fixture_session.py`.
- Properties on protocols use `@property` decorator.
- `@dataclass(frozen=True, slots=True)` for all frozen types.

## Commands you will need

| Purpose    | Command              | Expected on success |
|------------|----------------------|---------------------|
| Build      | `just build`         | exit 0              |
| Typecheck  | `just check`         | exit 0, no errors   |
| Py tests   | `just test-python`   | all pass            |
| Preflight  | `just preflight`     | exit 0              |

## Scope

**In scope** (the only files you should modify):

- `python/oxitest/_bridge/_fixture_session.py` (_SessionProtocol, FixtureSession)
- `python/oxitest/_bridge/executor.py` (remove getattr patterns)
- `python/oxitest/_bridge/importer.py` (remove getattr for _module_cache)
- `python/oxitest/_bridge/_fixture_instantiator.py` (resolve_param restructure)
- `python/oxitest/_bridge/_builtin_context.py` (plugin_registry field)
- `python/oxitest/_bridge/plugin_loader.py` (PluginRegistry empty factory, if needed)
- `python/oxitest/_bridge/_fixture_validator.py` (if it uses getattr for module_cache)

**Out of scope** (do NOT touch):
- Rust code — this is a Python-only refactor.
- `_NULL_SESSION` construction in `executor.py:230` — the null session is
  `FixtureSession([])`, which already creates empty registries.

## Git workflow

- Branch: `none-elim/015-session-guarantees`
- Commit per step; style: `fix: add plugin_registry to SessionProtocol (#ISSUE)`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Verify PluginRegistry can be constructed empty

Check `plugin_loader.py` — `PluginRegistry` is `@dataclass(frozen=True, slots=True)`.
List its fields and check if all have defaults. If all fields default to empty
tuples/None/etc., then `PluginRegistry()` works.

If not, create an `_EMPTY_REGISTRY = PluginRegistry(...)` module-level
constant with empty values for all fields.

**Verify**: `python -c "from oxitest._bridge.plugin_loader import PluginRegistry; print(PluginRegistry())"` should work (after `just build`). If it errors, note which fields lack defaults and create the constant.

### Step 2: Add `plugin_registry` and `module_cache` to `_SessionProtocol`

In `_fixture_session.py`, add to `_SessionProtocol`:

```python
class _SessionProtocol(Protocol):
    @property
    def plugin_registry(self) -> PluginRegistry: ...

    @property
    def module_cache(self) -> ModuleCache: ...

    def resolve_for_test(...) -> ...: ...
    # ... rest unchanged
```

Add the necessary imports (`PluginRegistry`, `ModuleCache`) at the top.

Verify that `FixtureSession` satisfies the protocol — it should already have
`_plugin_registry` and `_module_cache` as instance attributes. Add public
`@property` accessors if they don't exist:

```python
@property
def plugin_registry(self) -> PluginRegistry:
    return self._plugin_registry

@property
def module_cache(self) -> ModuleCache:
    return self._module_cache
```

**Verify**: `just build && just check` → exit 0

### Step 3: Update `_BuiltinContext.plugin_registry` to non-optional

In `_builtin_context.py:23`, change:
```python
plugin_registry: PluginRegistry | None = field(default=None, repr=False)
```
to:
```python
plugin_registry: PluginRegistry = field(default_factory=PluginRegistry, repr=False)
```

(Or use the `_EMPTY_REGISTRY` constant from Step 1 if `PluginRegistry()` doesn't work.)

Update any construction sites that pass `plugin_registry=None`.

**Verify**: `just build` → exit 0

### Step 4: Replace `getattr` patterns with direct property access

In `executor.py:95`, change:
```python
registry = getattr(session, "_plugin_registry", None)
if registry is not None and registry.debugger_backend is not None:
```
to:
```python
if session.plugin_registry.debugger_backend is not None:
    return session.plugin_registry.debugger_backend
```

In `executor.py:240-244`, change:
```python
_plugin_registry = getattr(session, "_plugin_registry", None)
_plugin_handlers: list[MarkHandler] = []
if _plugin_registry is not None:
    _plugin_handlers = [...]
```
to:
```python
_plugin_handlers: list[MarkHandler] = [
    _PluginMarkHandler(pw) for pw in session.plugin_registry.execution_wrappers
]
```

In `executor.py:128-139`, change:
```python
_cache = getattr(session, "_module_cache", None)
_cached = _cache.get(meta.module_path) if _cache is not None else None
if _cached is not None:
    module = _cached
    sys.modules[unique_name] = module
else:
    ...
    if _cache is not None:
        _cache.set(meta.module_path, module)
```
to:
```python
_cache = session.module_cache
_cached = _cache.get(meta.module_path)
if _cached is not None:
    module = _cached
    sys.modules[unique_name] = module
else:
    ...
    _cache.set(meta.module_path, module)
```

Note: `_cache.get()` returns `None` for cache misses — that `None` is the
return value of `ModuleCache.get()` and is correct (genuine "not found").

In `importer.py:357`, apply the same pattern.

In `_fixture_validator.py`, if it uses `getattr` for module_cache, update similarly.

**Verify**: `grep -rn "getattr(session" python/oxitest/_bridge/executor.py python/oxitest/_bridge/importer.py` → no matches

### Step 5: Restructure fixture resolution None cascade

In `_fixture_instantiator.py:207-230`, restructure to avoid `defn = None`:

```python
# Unified type-based resolution — try type first
try:
    defn = self._registry.resolve(inner, qualifier=param_name)
except FixtureNotFoundError:
    # No type-based match. Fall back to name-based lookup.
    if self._registry.get(param_name) is not None:
        return True, resolve_user_fixture(param_name)
    raise  # re-raise original FixtureNotFoundError

# For Builtin/Plugin sources found by type, use direct instantiation
if not isinstance(defn.source, ConftestSource):
    return True, self._resolve_by_source(
        defn, meta, fn_teardowns, resolve_user_fixture
    )

# For ConftestSource: prefer name-based (preserves cycle detection),
# fall back to type-resolved name.
resolve_name = (
    param_name
    if self._registry.get(param_name) is not None
    else defn.name
)
return True, resolve_user_fixture(resolve_name)
```

This eliminates:
- `defn = None` intermediate state
- `if defn is not None` at line 214
- `if defn is not None` at line 225
- `if resolve_name is None` at line 228

The logic is equivalent: if `resolve()` raises and name-based lookup also
fails, the original `FixtureNotFoundError` propagates.

**Important**: Verify this restructure preserves the exact same behavior by
tracing through all 4 cases:
1. Type match → non-ConftestSource → `_resolve_by_source` (unchanged)
2. Type match → ConftestSource, name exists → `resolve_user_fixture(param_name)` (unchanged)
3. Type match → ConftestSource, name doesn't exist → `resolve_user_fixture(defn.name)` (unchanged)
4. No type match, name exists → `resolve_user_fixture(param_name)` (unchanged)
5. No type match, name doesn't exist → `FixtureNotFoundError` (unchanged)

**Verify**: `just test-python` → all pass (fixture resolution is heavily tested)

### Step 6: Full verification

**Verify**: `just preflight` → exit 0

## Test plan

- Existing fixture tests cover all resolution paths (type-based, name-based,
  builtin, plugin, conftest, namespaced).
- Existing plugin tests cover empty and populated plugin registries.
- No new tests needed — this is a structural refactor.
- Pattern to follow: `_SessionProtocol` in `_fixture_session.py` is the exemplar
  for protocol property declarations.

## Done criteria

- [ ] `just preflight` exits 0
- [ ] `grep -rn 'getattr(session, "_plugin_registry"' python/oxitest/` → no matches
- [ ] `grep -rn 'getattr(session, "_module_cache"' python/oxitest/` → no matches
- [ ] `_SessionProtocol` declares `plugin_registry` and `module_cache` properties
- [ ] `_BuiltinContext.plugin_registry` is `PluginRegistry` (not `| None`)
- [ ] `_fixture_instantiator.py:resolve_param` has no `defn = None` assignment
- [ ] No files outside the in-scope list are modified (`git diff --name-only`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- `PluginRegistry()` cannot be constructed with no arguments — report which
  fields lack defaults.
- `_NULL_SESSION = FixtureSession([])` breaks because `FixtureSession.__init__`
  now requires a `PluginRegistry` — the null session must work with empty defaults.
- The fixture resolution restructure in Step 5 changes behavior for any of the
  5 traced cases — verify each case explicitly with a test.
- `ty check` rejects the `_SessionProtocol` property declarations.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- `_SessionProtocol` now has `plugin_registry` and `module_cache` as required
  properties. Any mock or test double implementing the protocol must provide
  them (can be empty PluginRegistry / ModuleCache).
- The fixture resolution restructure uses `raise` (re-raise) instead of
  `raise FixtureNotFoundError(param_name)`. This preserves the original
  exception with its original message and traceback. If the original error
  message is important (it says the *type* wasn't found, not the *name*),
  verify the error message is still helpful.
- `ModuleCache.get()` still returns `ModuleType | None` — that None is
  a genuine "cache miss" and is correct. This plan does not change it.
