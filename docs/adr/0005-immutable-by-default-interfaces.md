# ADR-0005: Immutable-by-default Python interfaces

**Status:** Accepted
**Date:** 2026-07-10

Python is mutable by default — any attribute can be reassigned, any list appended to, any dict mutated. oxitest's Rust core is the opposite: everything is immutable unless explicitly declared `mut`. The Python bridge layer sits between these two worlds, and its interfaces have grown organically — some frozen, some not, with no consistent rule governing which.

This ADR establishes a project-wide design principle: **all Python-side interfaces are immutable by default.** Mutability is the exception, not the norm, and every exception is explicitly justified.

## Considered Options

1. **Status quo.** Continue the organic approach — freeze dataclasses when it feels right, leave regular classes mutable. No enforcement, no rule. Simple, but produces inconsistency: `FixtureDef` is frozen while `WarnCapture.list` is a bare mutable field. Contributors (human and agent) have no guideline for new code.

2. **Immutable by default with explicit `&mut` exceptions (chosen).** Define six concrete rules that default everything to immutable. Classes that need mutability are listed in this ADR as the single source of truth. Enforcement via `ty` static analysis and runtime patterns (`frozen=True`, `@property`, immutable return types) — no new tooling.

3. **Full runtime enforcement via custom metaclass.** Build a metaclass or decorator that intercepts all attribute writes and raises unless opted in. Maximum enforcement, but high complexity, runtime cost, and poor interoperability with dataclasses, protocols, and PyO3.

## Decision

Option 2. The six rules below govern all Python source under `python/oxitest/`:

### Rule 1: Frozen dataclasses by default

All dataclasses use `@dataclass(frozen=True, slots=True)`. A dataclass without `frozen=True` requires justification — either it appears in the `&mut` exception list below, or it earns a new entry.

### Rule 2: Immutable public read accessors

Public fields that are collections must be exposed as immutable views: `tuple`, `frozenset`, `MappingProxyType`, or typed as `Sequence`/`Mapping`. Never expose a live `list`, `dict`, or `set`. Internal `_`-prefixed fields may be mutable.

This applies to `&mut` classes too — their read accessors return frozen snapshots even though their mutating methods modify internal state.

### Rule 3: Mutation is named explicitly

Methods that mutate `self` are named to signal mutation: `.clear()`, `.append()`, `.set_*()`, `.add_*()`. A method that looks like a read (`.records`, `.warnings`, `.dirs`) must not mutate state. This mirrors Rust's `&self` vs `&mut self` convention at the naming level.

### Rule 4: `&mut` exceptions

Classes whose purpose is stateful accumulation earn mutability. They are listed here — the single source of truth. A new class claiming mutability must be added to this list (or a follow-up ADR).

**Setup-phase mutable** — mutable during conftest loading, effectively frozen after collection:

| Class | Justification |
|-------|---------------|
| `Fixtures` | Accumulates fixture definitions via `@fixtures.fixture` decorator during conftest loading |
| `_MiddlewarePipeline` | Accumulates middleware into three zones (`_pre_guard`, `_post_guard`, `_pre_session`) during executor pipeline configuration; private, single-caller (executor only) |

**Test-lifetime mutable** — mutable during a test, torn down after:

| Class | Justification |
|-------|---------------|
| `Patcher` | Accumulates monkeypatches, restores originals on teardown |
| `StdCapture` | Captures stdout/stderr into buffers, resets on `readouterr()` |
| `FdCapture` | Captures file descriptors into buffers, resets on `readouterr()` |
| `LogCapture` | Captures log records, allows `set_level()` |
| `WarnCapture` | Captures warnings into a list |
| `TempDirFactory` | Creates temporary directories, tracks them for cleanup |
| `TestContext` | Accumulates teardown finalizers, holds parametrize `param` |
| `_Scope` | Internal fixture scope cache — accumulates cached values and teardown callbacks during test execution |

**Infrastructure mutable** — mutable across the entire session:

| Class | Justification |
|-------|---------------|
| `CoveragePyProvider` | Wraps `coverage.py`, holds mutable coverage collector |
| `SharedAsyncManager` | Holds a mutable event loop / async session reference |

### Rule 5: Parameters are never mutated

Functions and methods do not mutate their parameters. If a function needs to modify a collection, it copies first and returns the new version. Plugin protocol parameters are immutable from the callee's perspective — plugins must not mutate what they receive.

**Corollary:** Input parameters that are only read should be typed with their read-only abstract — `Sequence` not `list`, `Mapping` not `dict`, `AbstractSet` not `set`. This signals the immutability contract at the type level and gives callers flexibility in what they pass. Return values use concrete immutable types (`tuple`, `MappingProxyType`, `frozenset`). Protocol return types use the abstract (`Sequence`, `Mapping`) when implementers need flexibility in concrete type.

### Rule 6: Read-only public attributes

Public attributes on regular (non-dataclass) classes are read-only by default. Expose via `@property` with no setter. A setter requires the class to be in the `&mut` exception list *and* the specific attribute to need external mutation.

## Consequences

- **Retroactive refactor.** Existing code must be audited against the six rules and brought into compliance. This includes converting bare public fields to `@property`, wrapping mutable return values in `tuple`/`MappingProxyType`, and adding `frozen=True` to any non-frozen dataclass not in the `&mut` list.
- **Plugin author contract.** Protocol parameters are immutable. This is a convention, not a runtime enforcement — `ty` catches violations at the type level for typed code. The contract should be documented in plugin authoring guides.
- **FrozenProxy limitation.** `FrozenProxy` intercepts `__setattr__`, `__delattr__`, `__setitem__`, and `__delitem__`, but cannot intercept arbitrary mutating method calls (e.g., `.append()` on a wrapped list) at runtime. Reads that hand back the original object fall in the same category: `p[k]` and `iter(p)` yield unwrapped elements, and the `_wrapped` slot itself returns the wrapped object directly (`p._wrapped is original`). These are accepted escape hatches, not oversights — blocking `_wrapped` would need a `__getattribute__` hook on every attribute read of every shared fixture, which is disproportionate to closing one door in a room whose walls are already open by design. `ty` static analysis is the primary enforcement layer for all of these.
- **No new tooling.** Enforcement relies on existing infrastructure: `ty` for static checking, `frozen=True` for dataclass immutability, `@property` for attribute access control, and immutable return types (`tuple`, `MappingProxyType`) for collection safety.
- **Future classes default to immutable.** Any new class or dataclass is frozen/read-only unless it is added to the `&mut` exception list in this ADR with justification.
