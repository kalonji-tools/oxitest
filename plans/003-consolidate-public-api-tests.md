# Plan 003: Consolidate test_public_api.py granular export tests

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise.
>
> **Drift check (run first)**: `git diff --stat 7983e5d..HEAD -- python/tests/test_public_api.py`

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `7983e5d`, 2026-07-10
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1329

## Why this matters

`test_public_api.py` has 11 individual `hasattr(oxitest, "X")` tests (lines 16-84) that each check a single export. This is repetitive boilerplate. The file already has good consolidated tests (`test_exception_types_in_all`, `test_plugin_config_types_in_all`) — the export checks should follow that pattern.

## Current state

The 11 individual export tests (lines 16-84):
```python
def test_tempdir_exported_from_oxitest() -> None:
    assert hasattr(oxitest, "TempDir"), "'TempDir' should be exported from oxitest"

def test_tempdir_factory_exported_from_oxitest() -> None:
    assert hasattr(oxitest, "TempDirFactory"), ...

# ... 9 more identical patterns for StdCapture, FdCapture, Patcher,
# CaptureResult, LogCapture, SharedFixtureMutationError, Yields,
# WarnCapture, FixtureTeardownWarning
```

Some tests check more than `hasattr`:
- `test_shared_fixture_mutation_error_importable_from_oxitest` also checks `issubclass(RuntimeError)`
- `test_yields_exported_from_oxitest` also checks `"Yields" in oxitest.__all__`
- `test_fixture_teardown_warning_exported_from_oxitest` checks `issubclass(UserWarning)`

Tests that are already consolidated and should NOT be changed:
- `test_internal_types_not_in_all` (line 87)
- `test_exception_types_in_all` (line 98)
- `test_plugin_config_types_in_all` (line 111)
- `test_plugin_config_types_importable` (line 120)
- `test_injectable_exported_from_oxitest` (line 130)

Repo conventions for parametrize:
- Use `@oxi.parametrize` with frozen `@dataclass` cases
- Follow existing pattern in `test_approx.py`, `test_markers.py`

## Commands you will need

| Purpose   | Command                                       | Expected on success |
|-----------|-----------------------------------------------|---------------------|
| Test file | `just test python/tests/test_public_api.py`   | exit 0              |
| Check     | `just check`                                  | exit 0              |

## Scope

**In scope**: `python/tests/test_public_api.py`

**Out of scope**: `oxitest/__init__.py`, `__all__`, adding new exports

## Git workflow

- Conventional commits: `chore: consolidate public API export tests (#1329)`

## Steps

### Step 1: Create a parametrized builtin export test

Replace the 7 simple `hasattr` tests (TempDir, TempDirFactory, StdCapture, FdCapture, Patcher, CaptureResult, LogCapture) with a single `@oxi.parametrize` test:

```python
@dataclass(frozen=True)
class ExportCase:
    """Expected public export name."""
    name: str

@oxi.parametrize(
    tempdir=ExportCase(name="TempDir"),
    tempdir_factory=ExportCase(name="TempDirFactory"),
    stdcapture=ExportCase(name="StdCapture"),
    fdcapture=ExportCase(name="FdCapture"),
    patcher=ExportCase(name="Patcher"),
    capture_result=ExportCase(name="CaptureResult"),
    logcapture=ExportCase(name="LogCapture"),
)
def test_builtin_type_exported(name: str) -> None:
    """Builtin fixture types are available as top-level names in oxitest."""
    assert hasattr(oxitest, name), f"'{name}' should be exported from oxitest"
```

### Step 2: Handle the special-case exports

Keep standalone tests for exports that need additional assertions beyond `hasattr`:

- `test_shared_fixture_mutation_error_importable_from_oxitest` — keep as-is (checks issubclass)
- `test_yields_exported_from_oxitest` — keep as-is (checks __all__)
- `test_warncapture_exported_from_oxitest` — can fold into the parametrized test (only checks `is not None`)
- `test_fixture_teardown_warning_exported_from_oxitest` — keep as-is (checks issubclass)

**Verify**: `just test python/tests/test_public_api.py` → all pass

### Step 3: Verify coverage parity

Count parametrize case names + remaining standalone tests. Confirm all 11 original export names are still tested.

**Verify**: `grep -c 'def test_' python/tests/test_public_api.py` shows fewer functions but same export coverage

## Done criteria

- [ ] All 11 originally-tested exports are still verified
- [ ] Simple `hasattr` tests are consolidated into parametrized test(s)
- [ ] Tests with extra assertions (issubclass, __all__) remain standalone
- [ ] `just test python/tests/test_public_api.py` exits 0
- [ ] `just check` exits 0

## STOP conditions

- `test_public_api.py` has changed since commit `7983e5d`
- Any export that was tested before is no longer tested after

## Maintenance notes

- When new public exports are added to oxitest, add them to the parametrized `ExportCase` list
- The standalone special-case tests remain because they test more than export presence
