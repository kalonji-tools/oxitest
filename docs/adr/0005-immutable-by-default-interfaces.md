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

## Amendments

### Amendment 1 — Rule 5 gains an exception mechanism, and its ten sites are named (2026-08-13)

**Issue:** [#2109](https://github.com/kalonji-tools/oxitest/issues/2109), from the audit map [#2100](https://github.com/kalonji-tools/oxitest/issues/2100). Evidence: [#2102](https://github.com/kalonji-tools/oxitest/issues/2102), re-measured on `main` `b39991fa` with no tracked modifications. Amends Rule 5.

Rule 5 says *"Functions and methods do not mutate their parameters."* Ten sites in `python/oxitest/` do. The code is right and the rule is incomplete.

**Rule 5 names no way to be an exception.** Rule 1 sends a non-frozen dataclass to Rule 4's list. Rule 2 permits a mutable `_`-prefixed field outright. Rule 6 sends a setter to Rule 4's list. Rule 4 is that list, and names itself the single source of truth. Rule 5 sends nothing anywhere and permits nothing — and every one of the ten sites sits in a `_`-prefixed function, which is the shape Rule 2 already sanctions three rules above. (Rule 3 names no exception either. It is a naming convention, and this amendment does not reach it.)

**Nothing can report a violation.** The Consequences name `ty` as the enforcement. `ty` cannot see this rule: `list.append` on a parameter typed `list[T]` is correct against that type. The rule is about what a function **does**, and an annotation records only what a function **accepts**. So the ten sites were reached without any of them being refused, argued for, or written down.

#### Considered options

1. **Rewrite the ten sites to copy-and-return.** Rejected, and the reason is specific rather than a cost argument. `_resolve_arranged_entry` in `executor.py` documents it: a function-lifetime fixture registers its teardown into `scope_refs.teardowns`, which is the session's own per-test list, so a private list passed down is **ignored** for exactly the fixtures the function has to reorder — and it is ignored silently. `@oxi.arrange('sync_a', 'async_b', 'sync_c')` tears down `sync_c`, then `async_b`, then `sync_a`, and only one ordered list can express that. Copy-and-return does not produce a visibly wrong result here. It produces a correct-looking one with the wrong teardown order.

2. **Widen Rule 5 to permit parameter mutation.** Rejected: it discards the rule instead of bounding it. Rule 5's second half — *"Plugin protocol parameters are immutable from the callee's perspective"* — is a contract with people outside this repository, and none of the ten sites argues against it. All ten are private and none is on a protocol.

3. **A named exception, in the form Rule 4 already uses (chosen).** One exception with one shape, an enumerated list a new entry must join, and conditions that fix what the shape looks like at the call site. It matches the growth process this ADR and [ADR-0007](0007-none-by-exception.md) already share. Rule 4 gives the route: *"A new class claiming mutability must be added to this list (or a follow-up ADR)."* ADR-0007 gives the burden: *"a growth process for new exceptions (follow-up ADR with three-alternatives-analyzed rationale)."* Option 3 is also the only one of the three that leaves a checkable artifact behind.

#### The exception: the accumulator parameter

> **A function may mutate a parameter when that parameter is an accumulator.**
>
> An **accumulator** is a collection the **caller** owns, passed in so the callee can add to it, and read by the caller after the call returns. The exception covers three forms and no others: a call to a mutating method on the parameter name, `del` on a parameter subscript, and assignment to a parameter subscript.
>
> Four conditions, all required:
>
> 1. **The function is private** — a `_`-prefixed name, or nested inside one. A public function never mutates a parameter.
> 2. **The parameter is typed with its concrete mutable type** — `list[T]`, `dict[K, V]`, `set[T]` — never `Sequence`, `Mapping` or `AbstractSet`. This inverts Rule 5's corollary deliberately: the read-only abstract is how a parameter says *"I will not write this"*, so an accumulator must not wear it.
> 3. **The reason the collection must be shared is written down** — in the function's docstring, or in the table below.
> 4. **The site is in the table below.** A new one joins the table by the route Rule 4 gives — an amendment to this ADR, or a follow-up ADR — and carries the three-alternatives rationale [ADR-0007](0007-none-by-exception.md) asks of a new exception.
>
> `*args` and `**kwargs` are outside Rule 5 entirely and need no entry. Python builds a fresh tuple or dict for each call, so the callee owns it and no caller object is ever reached.

#### The ten sites

Listed by file, function and form. Never by line number: this list must survive the merges of the code it describes, and a line anchor rots on position without failing.

| File under `python/oxitest/_bridge/` | Function | Form | Why the collection is shared |
|---|---|---|---|
| `_fixture_instantiator.py` | `_instantiate` | `scope_teardowns.append()` | The scope's own teardown list. `start()` performs the registration, so the timed wrapper must reach the caller's list rather than a copy ([#1962](https://github.com/kalonji-tools/oxitest/issues/1962)) |
| `_fixture_registry.py` | `_compute_arranged_ancestors` | `computed[...] = ...` | A memo table shared across one traversal. A per-call copy makes the memo do nothing |
| `_middleware.py` | `_unpack_async_fixtures` | `async_teardowns.append()` | Caller-owned, and the docstring says so: an arranged fixture advanced before the call is already queued in it. The append precedes the advance, so an interrupt at the `await` cannot strand a set-up fixture ([#1962](https://github.com/kalonji-tools/oxitest/issues/1962)) |
| `_module_source_registrar.py` | `_method_fixture_violations` | `seen.add()` | The recursion guard for a class graph that can hold a reference to itself. A per-call copy restores the `RecursionError` the parameter exists to stop |
| `executor.py` | `_resolve_arranged_entry` | `del fn_teardowns[...]` | Moves a sync entry's teardowns out of the session's per-test list, by slice, so LIFO holds across sync and async entries ([#1740](https://github.com/kalonji-tools/oxitest/issues/1740)) |
| `executor.py` | `_resolve_arranged_entries` | `fn_teardowns.extend()` | Puts them back when no async entry appeared, leaving the pre-#1740 path untouched |
| `executor.py` | `_acquire_each_session` | `fn_teardowns.append()` | Appends exactly one callable, which closes the acquired session's stack. The docstring states this in its `Args` |
| `fixture_lister.py` | `_dfs` | `path.append()` | The DFS path of a white/grey/black cycle detector, nested in `_detect_cycle` |
| `fixture_lister.py` | `_dfs` | `path.pop()` | The other half of that push/pop pair |
| `result.py` | `_wire_optional` | `output.update()` | The function's whole purpose: add the non-falsy optional fields to the wire dict the caller is building |

Ten sites, seven files. Conditions 1 and 2 were measured against all ten, not assumed: every function name begins `_`, `_dfs` is nested, and the accumulator annotations are `list[Callable[[], None]]` three times, `list[tuple[str, Any]]`, `list[str]`, `dict[str, frozenset[str]]`, `dict[str, Any]` and `set[int] | None`. None is a read-only abstract.

#### What a checker would have to read

A check for this rule is not built here — [#2109](https://github.com/kalonji-tools/oxitest/issues/2109) puts it out of scope — so the definition it needs is recorded rather than encoded, because a check written without it refuses correct code.

An `ast` walk over `python/oxitest/` flags a `Call` on an `Attribute` whose value is a parameter name and whose attribute mutates, a `Delete` of a parameter subscript, and an assignment to a parameter subscript. It must **exclude the `vararg` and `kwarg` names**. With them included the scan returns 12 rather than 10, and the two extra are `kwargs.pop()` calls in `_mark_api.py` that mutate a dict Python built for that call alone.

#### Consequences

- **The corollary gains a second job.** Rule 5's corollary asked for `Sequence` over `list` on read-only inputs to signal intent. Condition 2 makes the converse load-bearing: a concrete mutable annotation on a private function's collection parameter is now a signal, so widening one of these ten to `Sequence` would be a documentation change, not a tightening.
- **The table shrinks without an amendment and grows only with one.** A site that stops mutating leaves the list silently; a new one cannot join it silently.
