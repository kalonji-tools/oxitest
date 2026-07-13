# Plan 002: Split test_parametrize.py into focused test modules

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 7983e5d..HEAD -- python/tests/test_parametrize.py`
> If the file changed since this plan was written, compare the current state
> against live code before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `7983e5d`, 2026-07-10
- **Issue**: https://github.com/kalonji-tools/oxitest/issues/1328

## Why this matters

`test_parametrize.py` is 1503 lines with ~65 test functions spanning decorator mechanics, collection expansion, execution, composition via `partial()`, FixtureRef resolution, validation/rejection, and dict-mode parametrize. Same navigability problem as `test_executor.py` — when a test fails, the filename doesn't tell you which parametrize subsystem broke.

## Current state

`python/tests/test_parametrize.py` contains these test function groups:

**Group A — Decorator stamps + validation** (lines 27-130, ~6 tests):
```
test_parametrize_stamps_function
test_parametrize_multiple_cases
test_parametrize_rejects_non_dataclass
test_parametrize_rejects_non_frozen_dataclass
test_parametrize_rejects_empty_cases
test_parametrize_rejects_wrong_instance_type
```

**Group B — Collection expansion** (lines 133-206, ~3 tests):
```
test_collect_parametrize_expands_to_n_items
test_collect_parametrize_item_has_param_values
test_collect_non_parametrize_has_none_param_id
```

**Group C — Fixture resolution with parametrize** (lines 208-500, ~10 tests):
```
test_plain_typed_param_not_resolved_as_fixture
test_fixture_annotated_param_resolved_alongside_plain_param
test_plain_typed_param_matching_fixture_raises_unannotated_error
test_executor_runs_parametrize_case
test_executor_parametrize_failure
test_executor_parametrize_case_with_fixture
test_fixture_ref_in_parametrize_resolves_fixture
test_fixture_ref_compact_mode_raises
test_fixture_ref_unregistered_fixture_errors
test_fixture_ref_no_session_returns_error
test_parametrize_rejects_non_callable_for_fixture_ref_field
```

**Group D — Dict mode** (lines 504-710, ~12 tests):
```
test_parametrize_dict_mode_stamps_function
test_parametrize_dict_mode_multiple_cases
test_parametrize_dict_mode_rejects_extra_key
... through ...
test_collect_dict_parametrize_item_has_param_values
test_parametrize_inferred_type_stamps_function
test_parametrize_rejects_invalid_case_type
```

**Group E — FixtureRef namespace resolution** (lines 744-845, 2 tests):
```
test_fixture_ref_uses_namespace_qualified_lookup_when_namespace_present
test_fixture_ref_falls_back_to_flat_lookup_when_no_namespace
```

**Group F — Cases internals** (lines 848-915, ~5 tests):
```
test_dict_cases_items_yields_repr_pairs
test_dict_cases_resolve_returns_kwargs_and_empty_fixrefs
test_dataclass_cases_items_yields_field_repr_pairs
test_dataclass_cases_resolve_expanded_mode
test_dataclass_cases_resolve_compact_mode
```

**Group G — Collection-time validation** (lines 918-1030, ~5 tests):
```
test_dict_parametrize_rejects_extra_key (via collect)
test_dict_parametrize_rejects_missing_key (via collect)
test_dataclass_parametrize_rejects_non_frozen (via collect)
test_dataclass_parametrize_rejects_mixed_types (via collect)
test_fixture_ref_no_session_with_namespace_returns_error
```

**Group H — Direct call validation** (lines 1033-1050, 2 tests):
```
test_parametrize_rejects_empty_cases_direct
test_parametrize_rejects_non_dataclass_non_dict_direct
```

**Group I — Composition (partial)** (lines 1062-1503, ~18 tests):
```
test_partial_stores_target_type_and_fields
... through ...
test_executor_composed_with_fixture_ref
```

Imports at the top:
```python
from __future__ import annotations

import textwrap
from collections.abc import Generator
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import oxitest as oxi
from oxitest import Fixture, FixtureRef, TempDir, helpers, parametrize, raises
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.importer import collect_module
from oxitest._bridge.plugin_loader import PluginRegistry
```

## Commands you will need

| Purpose   | Command                              | Expected on success |
|-----------|--------------------------------------|---------------------|
| Test all  | `just test`                          | exit 0, all pass    |
| Check     | `just check`                         | exit 0              |

## Scope

**In scope**:
- `python/tests/test_parametrize.py` — DELETE after split
- `python/tests/test_parametrize_decorator.py` — CREATE (Groups A, F, H)
- `python/tests/test_parametrize_collection.py` — CREATE (Groups B, G)
- `python/tests/test_parametrize_execution.py` — CREATE (Groups C, E)
- `python/tests/test_parametrize_dict.py` — CREATE (Group D)
- `python/tests/test_parametrize_partial.py` — CREATE (Group I)

**Out of scope**:
- `python/tests/test_parametrize_composition.py` — already focused (dogfood tests)
- Any test logic changes

## Git workflow

- Conventional commits: `chore: split test_parametrize.py into focused modules (#1328)`
- Do NOT push or open a PR unless the operator instructs it.

## Steps

### Step 1: Create test_parametrize_decorator.py

Groups A (decorator stamps/validation), F (cases internals), H (direct call validation). Docstring: `"""Tests for @oxi.parametrize decorator: stamps, cases internals, validation."""`

**Verify**: `just test python/tests/test_parametrize_decorator.py` → all pass

### Step 2: Create test_parametrize_collection.py

Groups B (collection expansion) and G (collection-time validation). Docstring: `"""Tests for parametrize collection: expansion to items, collection-time validation."""`

**Verify**: `just test python/tests/test_parametrize_collection.py` → all pass

### Step 3: Create test_parametrize_execution.py

Groups C (fixture resolution with parametrize) and E (FixtureRef namespace). Docstring: `"""Tests for parametrize execution: fixture coexistence, FixtureRef resolution."""`

**Verify**: `just test python/tests/test_parametrize_execution.py` → all pass

### Step 4: Create test_parametrize_dict.py

Group D (dict-mode parametrize). Docstring: `"""Tests for dict-mode @oxi.parametrize: stamps, validation, collection, execution."""`

**Verify**: `just test python/tests/test_parametrize_dict.py` → all pass

### Step 5: Create test_parametrize_partial.py

Group I (composition via partial). Docstring: `"""Tests for parametrize composition via partial(): stacking, cartesian product, validation."""`

**Verify**: `just test python/tests/test_parametrize_partial.py` → all pass

### Step 6: Delete test_parametrize.py and verify

Delete `python/tests/test_parametrize.py`. Verify total test count matches.

**Verify**: `just test` → all pass, same test count

## Test plan

- No new tests — pure file reorganization.
- `just test` confirms zero regressions.
- `just check` confirms ruff/ty pass.

## Done criteria

- [ ] `python/tests/test_parametrize.py` no longer exists
- [ ] 5 new focused test modules exist and pass
- [ ] `just test` exits 0 with no test count regression
- [ ] `just check` exits 0
- [ ] Each new file has a module-level docstring

## STOP conditions

- `test_parametrize.py` has changed since commit `7983e5d`
- Test count after split doesn't match original (~65 tests)
- Any test references an import that doesn't resolve in its new file

## Maintenance notes

- `test_parametrize_composition.py` (dogfood tests) is separate and unaffected
- Future parametrize tests should go in the module matching their concern
- Dict-mode and partial-composition are the most complex modules — new edge case tests for those features belong in their respective files
