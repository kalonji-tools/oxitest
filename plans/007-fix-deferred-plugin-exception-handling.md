# Plan 007: Add exception handling to deferred plugin ensure_loaded()

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat f60b5a0..HEAD -- python/oxitest/_bridge/plugin_loader.py python/tests/test_plugin_loader.py python/tests/test_plugin_lazy.py`
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
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1394

## Why this matters

`PluginEntry.ensure_loaded()` is the deferred plugin activation path. When a
plugin is deferred (lazy-loaded), its module hasn't been imported and
`oxitest_plugin()` hasn't been validated. The current code calls
`importlib.import_module()`, `getattr(module, "oxitest_plugin")`, and
`entry_fn()` with no exception handling. If a deferred plugin:

- Can't be imported → unhandled `ImportError`
- Lacks `oxitest_plugin()` → unhandled `AttributeError`
- Has an `oxitest_plugin()` that raises → unhandled arbitrary exception

All three crash the test session with a raw traceback instead of a clean
`PluginLoadError` with the plugin name and context.

The eager plugin loading path at lines 308–322 already handles all three
cases with proper validation and `PluginLoadError` wrapping.

## Current state

The deferred activation path in `plugin_loader.py:106-120`:

```python
# plugin_loader.py:106-120
def ensure_loaded(self) -> tuple[PluginEntry, Plugin]:
    """Import and initialise the plugin, returning (updated_entry, plugin)."""
    if self.plugin is not None:
        return self, self.plugin
    module = importlib.import_module(self.module_name)           # can raise ImportError
    entry_fn = getattr(module, "oxitest_plugin")                 # can raise AttributeError
    result = entry_fn()                                          # can raise anything
    if not isinstance(result, Plugin):
        msg = (
            f"oxitest_plugin() in {self.module_name!r} must return"
            f" oxitest.Plugin, got {type(result).__name__}"
        )
        raise PluginLoadError(msg)
    new_entry = dataclasses.replace(self, plugin=result)
    return new_entry, result
```

The eager path at `plugin_loader.py:308-322` (the exemplar):

```python
# plugin_loader.py:308-322
try:
    module = importlib.import_module(module_name)
except ImportError as e:
    msg = f'plugin "{module_name}" not found. Is it installed?\n  {e}'
    raise PluginLoadError(msg) from e

entry_fn = getattr(module, "oxitest_plugin", None)
if entry_fn is None:
    msg = f'plugin "{module_name}" has no oxitest_plugin() function'
    raise PluginLoadError(msg)
if not callable(entry_fn):
    msg = f'plugin "{module_name}" oxitest_plugin is not callable'
    raise PluginLoadError(msg)
```

The caller `activate_deferred_plugins()` at lines 438-441:

```python
# plugin_loader.py:438-441
else:
    # Activate remaining deferred plugins (e.g., fixture_provider)
    loaded, _ = entry.ensure_loaded()
    builder.replace_entry(i, loaded)
```

No try/except around `ensure_loaded()` here either.

**Repo conventions:**
- `PluginEntry` is a frozen dataclass (`@dataclass(frozen=True, slots=True)`) per ADR-0005.
- Error handling wraps with descriptive `PluginLoadError` messages naming the plugin module.
- Tests for plugin loading are in `python/tests/test_plugin_loader.py` and `python/tests/test_plugin_lazy.py`.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Build     | `just build`                                         | exit 0              |
| Tests     | `just test python/tests/test_plugin_loader.py python/tests/test_plugin_lazy.py` | all pass |
| Full test | `just test`                                          | all pass            |
| Typecheck | `ty check`                                           | exit 0              |
| Lint      | `ruff check python/oxitest/_bridge/plugin_loader.py` | exit 0              |

## Scope

**In scope** (the only files you should modify):
- `python/oxitest/_bridge/plugin_loader.py`
- `python/tests/test_plugin_lazy.py` (or `test_plugin_loader.py` — whichever is more appropriate for deferred plugin tests)

**Out of scope** (do NOT touch):
- The eager plugin loading path (lines 308–322) — already correct
- `activate_deferred_plugins()` — the fix goes in `ensure_loaded()` itself
- Rust bridge code
- Other plugin test files (`test_plugin_config.py`, `test_plugin_integration.py`, etc.)

## Git workflow

- Branch: `fix/007-deferred-plugin-exception-handling`
- Commit style: conventional commits with issue number, e.g. `fix: wrap deferred plugin ensure_loaded with PluginLoadError (#ISSUE)`
- Commit trailer: `Assisted-by: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add exception handling to ensure_loaded()

Modify `ensure_loaded()` to wrap the three failure points:

1. `importlib.import_module()` — catch `ImportError`, raise `PluginLoadError`
2. `getattr()` — use default `None`, check, raise `PluginLoadError` (match eager path)
3. `entry_fn()` — catch `Exception`, raise `PluginLoadError` with context

Target shape:

```python
def ensure_loaded(self) -> tuple[PluginEntry, Plugin]:
    """Import and initialise the plugin, returning (updated_entry, plugin)."""
    if self.plugin is not None:
        return self, self.plugin

    try:
        module = importlib.import_module(self.module_name)
    except ImportError as e:
        msg = f'plugin "{self.module_name}" not found. Is it installed?\n  {e}'
        raise PluginLoadError(msg) from e

    entry_fn = getattr(module, "oxitest_plugin", None)
    if entry_fn is None:
        msg = f'plugin "{self.module_name}" has no oxitest_plugin() function'
        raise PluginLoadError(msg)
    if not callable(entry_fn):
        msg = f'plugin "{self.module_name}" oxitest_plugin is not callable'
        raise PluginLoadError(msg)

    try:
        result = entry_fn()
    except Exception as e:
        msg = f'plugin "{self.module_name}" oxitest_plugin() raised: {e}'
        raise PluginLoadError(msg) from e

    if not isinstance(result, Plugin):
        msg = (
            f"oxitest_plugin() in {self.module_name!r} must return"
            f" oxitest.Plugin, got {type(result).__name__}"
        )
        raise PluginLoadError(msg)
    new_entry = dataclasses.replace(self, plugin=result)
    return new_entry, result
```

Remove the `# noqa: B009` comment since `getattr` now uses a default.

**Verify**: `ruff check python/oxitest/_bridge/plugin_loader.py` → exit 0
**Verify**: `ty check` → exit 0

### Step 2: Add tests for deferred plugin failure modes

In the appropriate test file (`test_plugin_lazy.py` or `test_plugin_loader.py`),
add tests for the three failure cases:

1. **Import failure**: Create a `PluginEntry.deferred("nonexistent_module_xyz", [...])`, call `ensure_loaded()`, assert `PluginLoadError` is raised with "not found" in the message.
2. **Missing entry point**: Create a deferred entry pointing to a real module that lacks `oxitest_plugin()` (e.g., `"json"`), call `ensure_loaded()`, assert `PluginLoadError` with "no oxitest_plugin() function".
3. **Entry point raises**: Create a test fixture module (in `python/tests/fixtures/`) with `def oxitest_plugin(): raise ValueError("broken")`, create a deferred entry for it, call `ensure_loaded()`, assert `PluginLoadError` with "raised".

Use `oxi.raises(PluginLoadError)` for assertions (dogfood oxitest).

**Verify**: `just test python/tests/test_plugin_lazy.py` → all pass, including new tests

### Step 3: Run full test suite

**Verify**: `just test` → all pass
**Verify**: `just check` → exit 0

## Test plan

- New test 1: deferred plugin with non-existent module → `PluginLoadError` "not found"
- New test 2: deferred plugin pointing to module without `oxitest_plugin` → `PluginLoadError` "no oxitest_plugin"
- New test 3: deferred plugin whose `oxitest_plugin()` raises → `PluginLoadError` "raised"
- Pattern: model after existing tests in `test_plugin_lazy.py`
- Verification: `just test python/tests/test_plugin_lazy.py` → all pass

## Done criteria

- [ ] `ensure_loaded()` catches `ImportError` and raises `PluginLoadError`
- [ ] `ensure_loaded()` validates `oxitest_plugin` exists and is callable
- [ ] `ensure_loaded()` catches exceptions from `entry_fn()` and wraps in `PluginLoadError`
- [ ] All error messages include the plugin module name
- [ ] Three new tests covering the three failure modes exist and pass
- [ ] `just test` exits 0
- [ ] `just check` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- The code at `plugin_loader.py:106-120` doesn't match the excerpt above.
- `PluginEntry` is no longer a frozen dataclass (ADR-0005 may have been reverted).
- The existing tests in `test_plugin_lazy.py` or `test_plugin_loader.py` fail before your changes.
- Adding a test fixture module to `python/tests/fixtures/` causes conftest discovery issues.

## Maintenance notes

- If new plugin protocols are added that require deferred loading, `ensure_loaded()` remains the single activation path — it should handle all failure modes.
- The eager path at lines 308–322 and `ensure_loaded()` now have parallel validation logic. If the validation rules change (e.g., new required attributes), both must be updated. Consider extracting a shared validation helper if this becomes a maintenance burden.
- Reviewer should verify: are the error messages consistent between eager and deferred paths?
