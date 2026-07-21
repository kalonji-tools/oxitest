# ADR-0007: None-by-exception

**Status:** Proposed
**Date:** 2026-07-20

Python's `None` is cheap to reach for. Any field can start out unset, any parameter can carry an optional default, any return can substitute absence for a value. Left unmanaged, this produces the shape observed in the oxitest Python source today: after the [#1482](https://github.com/kalonji-tools/oxitest/issues/1482) sweep — which removed ~17 unnecessary Optionals and ~20 guards — `python/oxitest/` still carries ~128 `| None` type annotations and ~146 `is None` / `is not None` guards across 274 total occurrences ([full catalog on #1552](https://github.com/kalonji-tools/oxitest/issues/1552)). The residual is not scattered mistakes — it clusters into six or seven recurring shapes (sum-type-in-disguise, fat-context Optional, lazy-init instance state, and so on), each of which admits a specific structural fix.

Case-by-case cleanups (the [#1482](https://github.com/kalonji-tools/oxitest/issues/1482) approach) reach a floor. The same shapes reappear in new code because there is no rule identifying them as shapes. The [#1549](https://github.com/kalonji-tools/oxitest/issues/1549) grill on `ExecutionPlan` — a fat-context bundle of four Optionals passed to a middleware chain — surfaced this directly: the fix ("Composable Middleware with Constructor Injection") wasn't specific to `ExecutionPlan`; it was the answer for a whole shape, one the codebase re-invents each time.

This ADR establishes a project-wide design principle: **`None` is a last resort.** Reach for richer types before Optional. It defines six refactor rules — one per structural shape observed in the audit — and one exception rule (Rule 7) enumerating the five Optional patterns that stay as-is. New `None`s in the codebase must fit an accepted pattern or earn a new exception via follow-up ADR. Mirrors [ADR-0005](0005-immutable-by-default-interfaces.md)'s shape: principle, rules, exception list, growth process.

## Considered Options

1. **Status quo — case-by-case cleanups.** Continue removing unnecessary Optionals opportunistically, as [#1482](https://github.com/kalonji-tools/oxitest/issues/1482) did. Rejected: [#1482](https://github.com/kalonji-tools/oxitest/issues/1482) already ran at scale and the residual is systemic — the same shapes reappear in new code because no rule identifies them as shapes. Without a design principle governing new code, the floor stays where it is.

2. **Ban `Optional` entirely.** Rule that no field, parameter, or return may be `T | None`. Rejected: Python's own language protocols use Optional-shaped signatures (`__exit__(exc_type, exc_val, exc_tb)`), and `ContextVar[T | None]` with `default=None` is the idiomatic way to signal "no active session". Both cases have no replacement without inventing new tooling or fighting the language.

3. **Principle + rules + exception list (chosen).** Establish one governing principle, six refactor rules mapping to the observed shape taxonomy, one umbrella exception rule with five sub-categories of sanctioned Optional patterns, and a growth process for new exceptions (follow-up ADR with three-alternatives-analyzed rationale). Governs new code, gives existing code a migration target per shape, and matches [ADR-0005](0005-immutable-by-default-interfaces.md)'s enforcement model — review-level plus `ty` where applicable, no new tooling.

## Decision

Option 3. The principle below governs all Python source under `python/oxitest/` — the Python bridge under `_bridge/` plus the public API module `plugin.py` alongside it. Rules 1–6 name specific shapes and the pattern that must replace each. Rule 7 is the umbrella exception list — five sub-categories of Optional patterns that stay as-is; new code may use these patterns without justification.

Occurrences may fit two rules. Each occurrence carries one primary rule tag; cross-links to overlapping rules appear under "See also" at the bottom of each rule.

### Principle

> **`None` is a last resort.** Reach for richer types before Optional — sum types over dual-None, default objects over optional hooks, always-present state over lazy-init. Every remaining `None` in the codebase is justified by an entry in the exception list, and every new `None` must earn its entry.

### Rule 1 — Sum-type-in-disguise

Two or more co-varying Optional fields inside a single class or function encoding a discriminated union must be replaced by a Union of frozen dataclasses, dispatched by `isinstance` (or `match`). One variant class per case; empty dataclasses (like `PassThrough`) cover pure-marker variants.

The precedent is `MarkAction = ShortCircuit | Wrap | PassThrough` in `_mark_registry.py` — each variant is `@dataclass(frozen=True, slots=True)`.

**Exemplars:**
- `ExecutionPlan.shared_session`, `arrange_session` (`_middleware.py:71-72`) — mutex session strategy
- `_ArrangeResult.error`, `session` (`executor.py:390-391`) → `ArrangeReady | ArrangeReadyAsync(session) | ArrangeFailed(error)` (shipped in #1574)
- `_evaluate_marks_phase -> tuple[TestResult | None, list[MarkWrapper]]` (`executor.py:285`) → `MarksHalt(result) | MarksProceed(wrappers)` (shipped in #1574)

**See also:** Rule 2 (Boundary-crossing discriminator) when the same pattern spans multiple files.

### Rule 2 — Boundary-crossing discriminator

The same conceptual state encoded via co-varying Optionals across ≥3 files spanning boundaries (Python dataclass + TypedDict + wire format + parameters) must be replaced by a Union of frozen dataclasses (per Rule 1) with paired `to_wire()` / `from_wire()` methods threading the sum type through every boundary. The wire tag lives inside `to_wire()` / `from_wire()`; expose a `.kind` StrEnum property only when consumers need set-membership dispatch (e.g., `result.status in (PASSED, WARNED)`) or reporter aggregation.

The precedent is `TestResult` (`result.py`) — a Union of frozen dataclasses with per-variant `to_wire(node_id, duration_ms)` methods that embed the tag via `_wire_base(<tag>, ...)`. `TestResult` also exposes a `.status: StatusKind` property because `_mark_registry` and `_tempdir` do set-membership dispatch (`result.status in (StatusKind.PASSED, StatusKind.WARNED)`); the property is *earned* by consumer demand, not mandatory.

**Exemplars:**
- `param_id: str | None` + `param_cases: tuple[...] | None` across `TestMeta`, `worker.py` TypedDict, `result.py`, `parametrize.py`, `_fn_metadata.py`, `_builtin_context.py`, `_violation_checkers.py` (7 files) → introduce `TestKindVariant = Parametrized(id, cases) | Solitary` (no `.kind` property — no known consumer aggregates by kind)

**See also:** Rule 1 (Sum-type-in-disguise) — Rule 2 is a specialization with wire discipline.

### Rule 3 — Fat-context Optional

An Optional field on a shared context object passed as a whole to many collaborators, when only *some* of those collaborators actually need the resource, must be replaced by constructor injection into the specific consumer(s). The consumer owns its resource (always non-None); the shared bundle carries only data every consumer needs.

When the consumers form a pipeline — as in the executor's middleware chain — this naturally becomes **Composable Middleware with Constructor Injection**: `build_pipeline()` assembles the specific middlewares needed for a given test based on test shape, and each middleware receives its resources through its constructor. See #1555 (prototype) and #1561 (refactor spec).

Rule 3 is scoped to Optional fields on shared bundles. Non-Optional fat-context (always-present resources shared with all consumers) is out of scope for this ADR.

**Exemplars:**
- `ExecutionPlan.default_timeout: int | None` (`_middleware.py:69`) → owned by `TimeoutMiddleware`
- `ExecutionPlan.backend: AsyncBackend | None` (`_middleware.py:70`) → owned by `AsyncBridgeMiddleware`
- `ExecutionPlan.shared_session`, `arrange_session` (`_middleware.py:71-72`) → owned by session middlewares

**See also:** Rule 1 (the mutex angle on the session fields — dual-classified).

### Rule 4 — Lazy-init instance state

An instance field of type `T | None` initialized to None and set later in the object's lifecycle, where consumers guard with `is None` before use, must be replaced by one of (in priority order):

1. **Eager construction** — set the field fully in `__init__` (or `__post_init__` for frozen dataclasses). Requires the constructor to have all the data needed to build the field.
2. **Factory method** — a classmethod (`Foo.for_bar(bar) -> Foo`) or module-level function that acquires the necessary data and returns a fully-constructed instance. Requires the caller to have the data before the object exists.
3. **State-machine sum type** — when the object's lifecycle has distinct phases (unopened → open → closed) and the field genuinely only exists in some phases, model the phases as variants (per Rule 1).

Reach for option 1 first; only fall back when the shape forces you to. Rule 4 applies to mutable classes on [ADR-0005](0005-immutable-by-default-interfaces.md)'s `&mut` exception list too — being mutable doesn't grant permission to use lazy-init as the default pattern.

**Exemplars:**
- `SharedAsyncManager._session: AsyncSession | None = None` (`_async_orchestrator.py:86`) → factory or state-machine
- `_WindowsTimeoutContext._timer: threading.Timer | None = None` (`_timeout.py:59`) → state-machine (`Idle | Active(timer)`)
- `StdlibLogBackend._old_level: int | None = None` (`_builtins/_logcapture.py:63`) → state-machine (`Uninstalled | Installed(old_level)`)

### Rule 5 — Callback/hook

An optional `Callable[..., ...] | None = None` parameter or field where None means "no callback wanted" must be replaced by a no-op default function of the same signature. Consumers call the callback unconditionally.

No-op defaults are module-level functions colocated with each callsite — no shared `_noops` module.

If the None case has semantic weight beyond "call vs don't call" (e.g., the presence of a callback selects a mode or gates behavior), that's Rule 1 territory (sum type), not Rule 5.

**Exemplars:**
- `safe_call(..., on_error: Callable | None = None)` (`_boundary.py:29`) → `safe_call(..., on_error=_no_on_error)`
- `safe_teardown(..., warn: Callable | None = None)` (`_boundary.py:84`) → `safe_teardown(..., warn=_no_warn)`
- `_FixtureOutcome.teardown: Callable[[], None] | None = None` (`_fixture_instantiator.py:135`) → `teardown: Callable[[], None] = _no_teardown` (reclassified to Rule 1 and shipped as `HasTeardown | NoTeardown` in #1574 — the None case gated behavior, per Rule 5's own carve-out)

### Rule 6 — Plugin-surface optionality

Fields typed `Plugin | None` or `Backend | None` on the plugin registry or on the public `Plugin` dataclass, where None means "no plugin registered" or "no backend provided", must be replaced by null-object defaults — canonical singleton instances of the protocol that no-op on every method.

Consumers query the field unconditionally and call methods — no None guards at consumer sites.

The null-object singletons are module-level constants in the plugin subsystem (e.g., `_NULL_DEBUGGER`, `_NULL_COVERAGE`, `_NULL_ASYNC_BACKEND`) and are used as the default value on the field. This applies uniformly to the public `Plugin` dataclass (authors omit the field, get the null default) and to internal `PluginRegistry` (resolved state consumers query) — one canonical default, no None-to-null conversion phase.

Discovery logic — "which plugin provides X" — uses `isinstance` checks against the null-object singleton during registry build (one place), not scattered across consumer code.

**Exclusion:** `PluginEntry.plugin: Plugin | None = None` (`plugin_loader.py:87`) — deferred until activated — is Rule 4 territory, not Rule 6. Fix via state-machine sum type: `DeferredPluginEntry | ActivatedPluginEntry`.

**Exemplars:**
- `Plugin.async_backend: AsyncBackend | None` (`plugin.py:285`) → default = `_NULL_ASYNC_BACKEND`
- `Plugin.debugger_backend: DebuggerBackend | None` (`plugin.py:288`) → default = `_NULL_DEBUGGER`
- `Plugin.coverage_provider: object | None` (`plugin.py:291`) → default = `_NULL_COVERAGE`
- `PluginRegistry.debugger_backend`, `.coverage_provider` (`plugin_loader.py:180-181`) → resolved to null-object by default

**See also:** Rule 7b (`_async_backend.py:173` — getattr-fallback chain, dual with find-lookup semantics).

### Rule 7 — Accepted Optional patterns

An `Optional[T]` that fits one of the five sub-categories below is exception-listed and stays as-is. New code may use these patterns without justification. Any Optional outside these sub-categories must be refactored per Rules 1–6.

**(a) Genuine no-value default** — user-facing parameter or dataclass field where None means "no value supplied, use default or derive".

- Exemplars: `raises(match: str | None = None)` (`_raises.py:61`), `warns(match=None)` (`_warns.py:12`), `Fixtures.__init__(name=None)` (`_fixtures.py:125`), `FunctionMetadata.fixture_name: str | None` (`_fn_metadata.py:34`, "derive from `__name__`").
- Note: when None could legitimately be a value the caller wants to pass (dict-value semantics, reflection over fields that may be None), use a private MISSING sentinel object instead of `T | None`. Precedent: `_FIELD_DIFF_SENTINEL` (`_diagnostics.py:143`).

**(b) Find-lookup return** — `def find() -> T | None` where None means "not present". Covers dict-like get, cache lookup, parser fallback, reflection lookup, exception-to-result mappers.

- Exemplars: `registry.get(name) -> FixtureDef | None` (`_fixture_registry.py:269`), `_parse_node_id(id) -> tuple | None` (`_fixture_validator.py:19`), `dispatch_exception(exc) -> TestResult | None` (`_diagnostics.py:261`).
- Raise instead of returning None ONLY when not-found is a bug at the call site (e.g., required fixture missing after `FixtureValidationPhase`).

**(c) Language-protocol sentinel** — Optional-shaped params required by a Python protocol. Cannot change.

- Exemplars: `__exit__(exc_type: type[BE] | None, exc_val: BE | None, exc_tb: types.TracebackType | None)` (`_raises.py:32-36`).

**(d) ContextVar unset marker** — `ContextVar[T | None]` with `default=None` for "no active session" is idiomatic Python. Don't refactor to a typed MISSING sentinel — lower value than the noise.

- Exemplars: `_fixtures_registry_var: ContextVar[FixtureRegistry | None]` (`_read_fixtures.py:11`), `_diagnostic_collector_var: ContextVar[list[Diagnostic] | None]` (`_diagnostic_collector.py:17`), `_fixture_context: ContextVar[FixtureContext | None]` (`_fixture_context.py:69`).

**(e) Optional dependency import** — canonical `try: import X; except ImportError: X = None` idiom with `Module | None`; guard uses with a friendly `raise ImportError`.

- Exemplars: `_coverage.py:15`, `worker.py:56`.

**See also:** Rule 1 (`_fixture_session.py:341,349,405,462-466` — ContextVar unset also serves as an outermost-session discriminator; primary tag 7d, dual with Rule 1 and Rule 4).

**Growth process.** A new Rule 7 sub-category — a new sanctioned Optional pattern beyond (a)–(e) — requires a follow-up ADR that:

1. **Names the shape** being sanctioned (the class of occurrences, not a single occurrence).
2. **Analyzes at least three alternative treatments** — typically sum type, null-object / default, and exception-listed Optional — but the specific three depend on the shape.
3. **States why Optional wins** with an explicit trade-off statement referencing at least one drawback of each alternative.

No code prototype required — a written rationale is sufficient. Adding a new *occurrence* within an existing sub-category (e.g., another dict-like `get()` method fitting 7b) needs no ceremony — the pattern is the license. This mirrors [ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4's convention for adding `&mut` classes, but strengthens the entry requirement to include the three-alternatives-analyzed rigor.

## Consequences

- **Retroactive refactor via follow-up issues.** Existing code that violates Rules 1–6 is not fixed by this ADR. [#1559](https://github.com/kalonji-tools/oxitest/issues/1559) files one bare follow-up refactor issue per non-middleware taxonomy context, each linking back to the relevant rule; execution happens per issue after this ADR merges. The middleware refactor — Rule 3's first worked example — is spec'd at [#1561](https://github.com/kalonji-tools/oxitest/issues/1561).
- **Contributor contract.** New code follows Rules 1–7. A PR that adds a `| None` outside Rule 7's sub-categories needs either a per-shape refactor to a Rules 1–6 pattern or a follow-up ADR growing Rule 7 per the acceptance test above. This is a review-level contract, not a runtime enforcement.
- **Enforcement via review + `ty`.** No new tooling. `ty` already catches many mismatches at the type layer (e.g., calling `.foo()` on a `Foo | None` without narrowing); code review catches the shape-level cases (a new `T | None` field that fits Rule 1 must become a sum type). Existing tooling suffices.
- **Prototype confirmation.** The Composable Middleware pattern for Rule 3 was validated on `ExecutionPlan` — the prototype ([#1555](https://github.com/kalonji-tools/oxitest/issues/1555)) reached zero non-semantic Optionals and zero irreducible Optionals surfaced as candidates for Rule 7 growth. Concrete evidence the refactor rules are executable, not aspirational.
- **Interaction with [ADR-0005](0005-immutable-by-default-interfaces.md).** Rule 4 (lazy-init instance state) explicitly applies to mutable classes on ADR-0005's `&mut` exception list too — being on that list grants mutability, not permission to default fields to None and populate later. The two ADRs compose: ADR-0005 governs whether a class is mutable; ADR-0007 governs whether its fields may be Optional. Both defaults are strict; both grant exceptions by explicit entry.
