# ADR-0006: Async organizational strategy for oxitest

**Status:** Accepted
**Date:** 2026-07-17

oxitest's `@arrange` decorator silently no-ops when arranging a function-scope async fixture on either sync or async tests. The root cause is `_fixture_instantiator.py:138-151` — `_unpack_sync` only checks `inspect.isgenerator`, so a coroutine or async generator returned by the factory is stored as the outcome value and discarded without ever being awaited. Sync fixtures and shared-scope async fixtures compose with `@arrange` correctly; only function-scope async fixtures fall through the crack.

Fixing the crack forced an upstream question: how should oxitest organize its async surface as it grows? Three prototypes explored the space (wayfinder map [#1530](https://github.com/kalonji-tools/oxitest/issues/1530)):

- [#1531 — Polymorphic-unified](https://github.com/kalonji-tools/oxitest/issues/1531) — one `@arrange`, one `Fixtures`, internal dispatch on `FixtureDef.is_async × scope`.
- [#1532 — Parallel-subpackage split](https://github.com/kalonji-tools/oxitest/issues/1532) — async surface (arrange, Fixtures, AsyncBackend, SharedAsyncSession) relocates to `oxitest.aio.*`; sync stays top-level.
- [#1533 — Test-kind hybrid (B′)](https://github.com/kalonji-tools/oxitest/issues/1533) — loud rejection at arrange time when a sync test arranges a function-scope async fixture. Composable on either #1531 or #1532.

Prototyping surfaced a cross-cutting design constraint: `AsyncioBackend.run(coro)` today wraps `asyncio.run()`, which calls `loop.shutdown_asyncgens()` on close. Any pattern that dispatches per-fixture-call through this seam finalizes setup async generators via `GeneratorExit` before the test body runs — teardown post-yield code never executes. The implementation must use a per-test-lifetime loop shared across setup → body → teardown, sitting *around* `AsyncBackend`, not *through* `AsyncBackend.run`. This constraint applies regardless of chosen pattern.

## Considered Options

1. **A — Polymorphic-unified.** One `@arrange` decorator, one `Fixtures` class. Registration infers `is_async` from the factory. The arrange path dispatches on `(is_async, scope)`. Zero migration; zero public API surface change. Silent no-op eliminated by construction. Illegal cell (sync test + function-scope async fixture) *silently works* — spawns a fresh event loop, blocks the sync test on `loop.run_until_complete`, teardown fires. Correct but surprising.

2. **B — Parallel-subpackage split (`oxitest.aio.*`).** Async surface relocates to a subpackage. Marquee benefit: import-time isolation — pure-sync users skip the `asyncio` import cost (~1ms, verified via subprocess check on the prototype). Costs: +~120 LOC of implementation, ~90–150 lines of user migration across ~30 files for a moderate suite, doubled docs surface, `Fixtures` type-name clash between packages (users mixing both must alias). Two decorators and two Fixtures classes are behaviorally identical today; the split is speculative unless aio-only features (cancellation scopes, loop policies, custom backends) materialize to earn it.

3. **B′ — Test-kind hybrid.** Sync test + function-scope async fixture rejected at arrange time with a loud diagnostic naming three legal exits. Pytest precedent: PR #12930 (merged 8.4, gated warn/error) → PR #14015 (Nov 2025, escalated to always-on hard error). Rejection at arrange time locates the diagnostic at the `@arrange` line, not deep inside a factory. B′ is a policy layer, orthogonal to organizational choice — composable on either #1531 or #1532.

## Decision

**Adopt A + B′ with always-on rejection.**

### The pattern

One `@arrange` decorator, one `Fixtures` class. `FixtureDef.is_async` is inferred at registration from the factory (`iscoroutinefunction` or `isasyncgenfunction`). The arrange path dispatches on `(defn.is_async, defn.scope)`:

| Fixture kind | Scope | Dispatch |
|---|---|---|
| sync | any | current sync path (generator unpack or plain value) |
| async | `session` | `SharedAsyncManager.resolve()` on the shared async session |
| async | `each` (function scope) | per-test-lifetime loop, `run_until_complete` for setup and teardown |

The per-test-lifetime loop is owned by the fixture instantiator, created lazily on first each-scope async fixture, shared with the test body if the test is `async def`, and closed after cleanup. `AsyncBackend.run` is *not* used in this path.

### The one illegal cell

Sync test + function-scope async fixture is **rejected at arrange time**, always, regardless of `strict` config. Diagnostic names three legal exits:

1. Make the test `async def`.
2. Raise the fixture's lifetime above `function` — `lifetime="module"`, `"package"` or `"process"`.
3. Convert the fixture to a `def` (sync) fixture.

> **Amended — Amendment 2 ([#1876](https://github.com/kalonji-tools/oxitest/issues/1876)).** Exit 2 read *"Convert the fixture to `shared` (or `session`) scope."* Those are **Scope** spellings, and this list is read by a user, who writes a **Lifetime**. `shared` has not existed since #1720. The dispatch table above keeps Scope, correctly — it dispatches on `defn.scope`.
>
> Amendment 2 also states which **routes** this cell covers, which this rule never did.

Rejection is not gated by `strict` because `strict` toggles style (bare asserts, dict parametrize); the async cell rejection is correctness. Pytest #12930 (gated) → #14015 (always-on) is direct empirical evidence that gating was insufficient.

### Public surface

`AsyncBackend`, `SharedAsyncSession` stay at `oxitest/` — no `oxitest.aio.*` split, no relocation, no deprecation shims. `@arrange` and `Fixtures` are single top-level exports.

### Application to future async surface

Any future async APIs (`Patcher` async variant, async helpers, async marks, etc.) follow the same rules: polymorphic dispatch on kind + scope where a kind axis exists, loud rejection of illegal cells at the earliest catchable frame. This locks the pattern for consistency; it does not commit to when or whether those APIs land.

## Consequences

### Positive

- **Silent no-op eliminated by construction.** The `_fixture_instantiator.py:138-151` bug is impossible under A + B′: sync tests loud-reject; async tests dispatch to the async path which awaits.
- **Zero migration.** No public API relocation. `AsyncBackend`, `SharedAsyncSession`, `@arrange`, `Fixtures` all stay put.
- **On-brand with strict-mode DNA.** Loud rejection at the shallowest catchable frame (arrange time, `@arrange` line) matches oxitest's existing `strict = "abort"` philosophy.
- **Per-test-loop discipline is visible at one seam.** Because the per-test loop is only created for async tests (guaranteed by B′'s rejection of the mixed case), the loop lifecycle is tied to test kind at a single seam. Future maintainers cannot reintroduce the `asyncio.run()`-per-fixture-call footgun by accident without breaking a whole test kind loudly.
- **Diagnostic locality.** B′ rejects *before* the factory is invoked, so the diagnostic points at the `@arrange` line. Errors surfaced by the factory itself (a real fixture bug) still wrap through `ArrangeError` but with the underlying cause preserved.

### Negative

- **`AsyncBackend.run(coro)` is a design wart.** Surfaced during prototyping (#1531 and #1533 NOTES.md). `run(coro)` naturally maps to `asyncio.run(coro)`, which finalizes pending async generators on loop close — incompatible with a per-test-lifetime loop discipline. The implementation must work *around* the seam via `_Instantiator.each_loop`. A loop-lifecycle-aware seam refactor (e.g., `AsyncBackend.acquire_loop() -> ContextManager[AbstractEventLoop]`) is tracked as a **follow-up issue outside this map**, because it changes a public protocol and warrants its own grilling.
- **Illegal cell can no longer be worked around silently.** Users who *want* to arrange async fixtures from sync tests (a niche pattern) must convert one side. The trade-off is intentional; pytest's own evolution is direct evidence that "silently works" confuses more than it enables.
- **Rejection is not user-configurable at ship.** A future release can add a knob if a legitimate use case emerges. Green-field user base (no legacy consumers of the silent behavior) makes always-on cheap now.

### Neutral

- **Future async APIs inherit the pattern.** Any async helper, `Patcher` variant, or new async infrastructure added later dispatches on kind + scope and rejects the sync-test / function-scope-async cell at the same seam. Consistent by construction; no per-feature debate.
- **Docs impact scoped to `@arrange`.** One user doc page needs a section on async fixture interaction; the error reference needs the B′ rejection message documented with its 3 legal exits. No new doc pages, no subpackage navigation, no import-diff table for users.
- **`strict` semantics unchanged.** `strict = "off" | "enforce" | "abort"`, defaulting to *absent* (which is silent), continues to toggle style rules (bare asserts, dict parametrize, missing mark reasons). B′ rejection is orthogonal and always-on. *This bullet as accepted spelled the dial `"abort" | "warn" | "off"`; there has never been a `warn` position — see [Amendment 1](#amendment-1--the-strict-dial-has-no-warn-position-2026-08-02).*

## Alternatives Considered

- **B — Parallel-subpackage split (`oxitest.aio.*`).** Rejected. The one concrete benefit (~1ms `asyncio` import cost skipped for pure-sync users) does not offset +~120 LOC of implementation, ~90–150 lines of user migration, doubled docs surface, and the `Fixtures` naming clash between packages. The other selling points (discoverability, aio-only extension) are speculative — no aio-only features are planned in the 6–12 month horizon (verified during grilling). Extraction to `oxitest.aio.*` remains open as a future breaking-change one-shot if aio-only features materialize and earn the split.

- **A alone (silent polymorphism, no B′).** Rejected. Sync test + function-scope async fixture "works" but with unexpected loop-lifecycle latency, no diagnostic, and no navigational hint if the user's intent was actually a different combination. Pytest #12930 (gated warn/error) → #14015 (always-on hard error) is direct empirical evidence that silent polymorphism drifts into confusion. `strict = "abort"` DNA plus green-field user base makes always-on rejection cheap now.

- **B + B′** — subpackage split plus loud rejection. Rejected implicitly by rejecting B. B′ is a policy layer that composes on either A or B; the axes were evaluated independently. Subpackage split (B) failed on its own merits; layering B′ on top does not rescue it.

- **B′ gated by `strict`.** Considered. Rejected in favour of always-on because `strict` toggles style, not correctness. Pytest's own arc from gated (#12930) to always-on (#14015) is direct evidence that gating was insufficient.

## Amendments

### Amendment 1 — the `strict` dial has no `warn` position (2026-08-02)

Tracked by [#1771](https://github.com/kalonji-tools/oxitest/issues/1771). Amends one Consequences bullet — "`strict` semantics unchanged". Everything that bullet exists to say stands as accepted: `strict` toggles style rules, B′ rejection is orthogonal to it, and B′ is always-on. What is corrected is the *enumeration of the dial's values*, which named a position oxitest has never had.

As accepted, the bullet read:

> - **`strict` semantics unchanged.** `strict = "abort" | "warn" | "off"` continues to toggle style rules (bare asserts, dict parametrize, missing mark reasons). B′ rejection is orthogonal and always-on.

| The bullet claimed | `main` |
|---|---|
| `strict = "abort"` | `StrictMode::Abort` exists |
| `strict = "warn"` | **never existed.** Not renamed, not deprecated, not removed — no `Warn` variant has ever been in `StrictMode`, so no oxitest release has ever accepted the value |
| `strict = "off"` | `StrictMode::Off` exists, and normalises straight back to *absent* — it takes the same code path as the key not being there, rather than being a fourth behaviour |
| *(unstated)* | `strict = "enforce"` — the position the bullet omitted entirely, and the only soft one |

`StrictMode { Abort, Enforce, Off }` lives at `src/config/mod.rs:117`–`121` under `#[serde(rename_all = "lowercase")]`, read from a `strict` key sitting directly under `[tool.oxitest]` (`src/config/pyproject.rs:170`) — not under a `markers` sub-table, as the flat spelling in the bullet correctly implied. The `"off"` → absent normalisation is `src/config/merge.rs:253`–`255`.

The key is **fail-closed**, in the sense [ADR-0008](0008-config-fail-closed-narrow-scope.md) established: a value outside the enum is printed and the process exits with `UsageError` (4) at `Config::load`, before collection begins (`src/config/mod.rs:601`–`602`). So this was not a cosmetic documentation defect. A reader who copied the bullet into their `pyproject.toml` got a hard error and no test run, reported in #1771 as:

```
error: pyproject.toml: unknown variant `warn`, expected one of `abort`, `enforce`, `off`
in `strict`
```

**The corrected statement.** The dial is `off | enforce | abort`. Its default is **absent**, which is silent — not `off`-as-a-behaviour, and not any implied soft default:

| `strict` | Style violations (bare asserts, dict parametrize, missing mark reasons) | Diagnostic severity |
|---|---|---|
| absent, or `"off"` | not checked | none emitted |
| `"enforce"` | reported; the rest of the run proceeds (`src/pipeline/helpers.rs:223`–`246`) | `Warning` |
| `"abort"` | printed, then an early `Err` before any test executes — `ExitCode::CollectError`, or `UsageError` when a requested `--json` or `--junit-xml` artifact cannot be written (`src/pipeline/helpers.rs:214`–`221`, [#1682](https://github.com/kalonji-tools/oxitest/issues/1682), [#1858](https://github.com/kalonji-tools/oxitest/issues/1858)) | `Error` |

The severity column is `src/pipeline/collection.rs:673`–`677`.

**The two-position reading survives, with `enforce` in `"warn"`'s place — but it has to be stated, not inferred.** If this ADR's author read the dial as "one soft position and one hard position", that reading still holds; the soft position is simply not called `warn`. `enforce` maps to `DiagnosticSeverity::Warning` and never to `Error` — a deliberate invariant from [#1613](https://github.com/kalonji-tools/oxitest/issues/1613), guarded by a test at `src/pipeline/collection.rs:1331`–`1345`, which on `main` is scoped to doctest-coverage diagnostics. [ADR-0009's Amendment 3](0009-fixture-system-redesign.md#amendment-3--the-shortcut-strict-dial-is-retracted-2026-07-30) turned down a proposal to add a real `Warn` variant partly on that ground: `enforce` already *is* warn.

One thing "soft" must not be read to mean. Under `enforce` the run continues, but violating items are not merely annotated — they are withheld from worker dispatch and reported as per-test `Error` outcomes (`src/pipeline/execution.rs:514`). `enforce` is warn-*severity*, not nothing-fails. The distinction between `enforce` and `abort` is whether the *rest of the suite* still gets to run.

**Nothing about B′ changes.** B′ rejection of the sync-test / function-scope-async-fixture cell was never gated by `strict` — see the Decision's "The one illegal cell" and the rejected "B′ gated by `strict`" alternative, both of which reason about `strict` as a *style* dial without enumerating its values, and are therefore untouched by this amendment. The `strict = "abort"` references elsewhere in this ADR name a real value and stand.

**Provenance.** The fictional value was found while grilling [#1714](https://github.com/kalonji-tools/oxitest/issues/1714), which hit the same spelling in [ADR-0009](0009-fixture-system-redesign.md) Rule 5. ADR-0009's occurrences and the user-facing copies in `CONTEXT.md`, `docs/user/how-to/use-fixtures.md` and `docs/user/reference/errors.md` were corrected in PR #1770; ADR-0006's was deliberately left alone there, because a fixture-slice PR is the wrong place to amend an unrelated accepted ADR. This closes that deferral, and with it the last normative document presenting `strict = "warn"` as usable config.

This belongs to the same class of defect as ADR-0009's five amendments and the [#1769](https://github.com/kalonji-tools/oxitest/issues/1769) conformance sweep: a normative claim about oxitest that was never checked against oxitest. ADR-0006's *decision* is unaffected — A + B′ with always-on rejection stands exactly as accepted.

## Related

- Wayfinder map: [#1530 — Async organizational strategy for oxitest, with @arrange as first proof-of-fit](https://github.com/kalonji-tools/oxitest/issues/1530)
- Prototype tickets (all closed):
  - [#1531 — Prototype: polymorphic-unified async pattern](https://github.com/kalonji-tools/oxitest/issues/1531)
  - [#1532 — Prototype: parallel-subpackage async pattern (oxitest.aio.*)](https://github.com/kalonji-tools/oxitest/issues/1532)
  - [#1533 — Research external proposals + prototype most promising (fallback: test-kind hybrid B′)](https://github.com/kalonji-tools/oxitest/issues/1533)
- Downstream spec: [#1535 — Spec: apply chosen async pattern to @arrange (including silent-failure bug fix)](https://github.com/kalonji-tools/oxitest/issues/1535)
- Root-cause reference: `python/oxitest/_bridge/_fixture_instantiator.py:138-151` — `_unpack_sync` silent-discard of coroutines and async generators.
- Follow-up (tracked outside this map): `AsyncBackend.run` seam refactor — loop-lifecycle-aware replacement for the current `run(coro) -> _T` shape.

### Amendment 2 — the illegal cell is proxy-only, and the routes are now named (2026-08-11)

Tracked by [#1876](https://github.com/kalonji-tools/oxitest/issues/1876). Amends "The one illegal cell" and the dispatch table. The decision to keep async surface at `oxitest/` is untouched.

**This ADR never said which access route its cell governs, because when it was written there was one.** The `fx.` proxy arrived later, with ADR-0009 slices 6 and 7. The proxy enforces something **stricter** than the cell described here, and no normative document recorded that — which is why the difference read as a hole in the parameter route rather than as a boundary.

**The rule, by route.** Measured on `main` `48e87a92`, Linux 6.18.41 x86_64, CPython 3.12.13, one project per cell:

| Consumer | Route | `function` | wider than `function` |
|---|---|---|---|
| sync test | `Fixture[T]` parameter | refused | **legal** — `FrozenProxy('module-value')`, `FrozenProxy('package-value')` |
| sync test | `FixtureRef` in `@oxi.parametrize` | refused | **legal** |
| sync test | `await fx.<name>` proxy | refused | **refused, at every tier** |
| sync fixture | a `Fixture[T]` dependency | refused | **legal** — `FrozenProxy('consumed:async-wide')` |

**The asymmetry is the delivery mechanism, not an oversight.** The proxy returns a handle only `await` can unwrap, so a sync test cannot consume it at any lifetime. The parameter routes return a value already awaited on the shared session loop, so above `function` there is nothing left to await. Widening the refusal to the parameter routes would move a cell this ADR routes deliberately from legal to illegal, and break suites that run today.

**The consumer set is stated because it was measured.** A *fixture* consuming a fixture is the same cell one level down, and the question there is whether the consumer can await rather than whether the test is async. It behaves identically: a sync fixture depending on a `module`-lifetime async fixture receives the value, and one depending on a `function`-lifetime async fixture is refused with its own message — `sync fixture 'X' cannot depend on async fixture 'Y'`. So `function`-only holds for both consumer kinds.

**`FixtureRef` was on the wrong side and is corrected.** It resolved through the proxy machinery, so a sync test naming a wider-tier async fixture in a `@oxi.parametrize` case was refused where the identical fixture reached by a `Fixture[T]` parameter resolved. One cell had two answers decided by how the fixture was named, and the error cited an `fx.<namespace>.<name>` spelling the user never wrote. It now resolves as the parameter route does. `function` lifetime is unchanged — the value is an un-advanced coroutine and `AsyncDepGuardMiddleware` refuses it.

**`AsyncFixtureAccessError` no longer offers an exit that cannot work.** It listed *"Raise the fixture's lifetime so it is built outside the test"* at every tier, on the one route where raising the lifetime never helps — measured refusing at `function`, `module` and `package`. Above `function` it now offers the parameter route instead, which is the cell this amendment declares legal. It also printed `Lifetime: each`; `each` is a Scope, and no `@oxi.fixture` call accepts it.

**What pins this.** `python/tests/data/async_guard_matrix/legal` holds nine cells, run serially and under `-n 2`. `illegal/` holds the four that stay illegal — the `function`-tier `FixtureRef` pair, and the sync-proxy cells at `module` and `package`. The proxy pair is the load-bearing half: it is the only assertion that the proxy stays stricter than this ADR's cell, and without it a change relaxing the proxy would leave every gate green.

### Amendment 3 — one loop is necessary and not sufficient; the task is the second invariant (2026-08-12)

**Status:** accepted. Implements [#1740](https://github.com/kalonji-tools/oxitest/issues/1740).

This ADR reasons in loops. Its cross-cutting constraint says *"The implementation must use a per-test-lifetime loop shared across setup → body → teardown"*, and that is implemented and it holds. **It is not sufficient.**

A fixture that holds an `anyio.CancelScope`, an `asyncio.TaskGroup`, or a `contextvars.ContextVar` across its `yield` depends on setup and teardown running in **one task**, not only on one loop. anyio made *"run all tests and fixtures in the same task"* a documented breaking change in 4.0, and named contextvar propagation as the driver. pytest-asyncio has the same defect open at [#1191](https://github.com/pytest-dev/pytest-asyncio/issues/1191).

**The invariant.** At `lifetime="function"`, a fixture's setup, the test body and the fixture's teardown run in one task, on every declaration route: the `Fixture[T]` parameter, the `fx.` proxy, and `@oxi.arrange`.

**What was measured, and what was wrong.** On `main` at `b98d2114`, CPython 3.12.13, Linux x86_64, with `--serial` and `-n 2` giving the same result:

| Route at `lifetime="function"` | setup / body / teardown | Before |
|---|---|---|
| `fx.` proxy | Task-1 / Task-1 / Task-1 | already held |
| `Fixture[T]` parameter | Task-2 / Task-2 / Task-2 | already held |
| `@oxi.arrange` | Task-4 / Task-5 / Task-6 | **broken** |

The two main routes already held it, because `_async_test_core` resolves the fixtures inside the body coroutine and drains their teardowns in its own `finally` — one `AsyncSession.run` covers the three phases. The arrange path advanced its fixtures before the body coroutine existed, so each `run_until_complete` made a task of its own.

**The change.** The executor queues an arranged async fixture instead of advancing it. `_async_test_core` advances the queue, and drains it after the body.

**`module` lifetime and wider are exempt, and the exemption is deliberate.** One setup serves many test bodies, so no task can span them. A fixture that needs the task invariant belongs at `function` lifetime. This is documented and **not** diagnosed: a registration-time warning would fire on every wide-lifetime async generator, and the overwhelming majority hold a connection or a directory across the yield and are correct. When the invariant is needed and absent, anyio raises `Attempted to exit cancel scope in a different task than it was entered in`, which names the problem better than anything this framework would synthesise at registration.

**A persistent runner task was rejected.** It is the shape anyio's plugin and pytest-asyncio [#1193](https://github.com/pytest-dev/pytest-asyncio/pull/1193) use: hold one long-lived task on the session and submit work to it. An `AsyncSession` outlives one test whenever a fixture is wider than `function` lifetime, so a task held by the session would make a `module`-lifetime setup share a task with **every** body in that module — an invariant this ADR does not promise and the exemption above explicitly denies.

**The public protocol does not change.** `AsyncSession.run(coro)` keeps its shape. The invariant is a property of where the framework calls it, not of the protocol, so a third-party backend inherits it by construction rather than by contract.

**Teardown order is unchanged, and that cost the design its first shape.** LIFO holds *across* the two kinds of arranged fixture: `@arrange('sync_a', 'async_b', 'sync_c')` tears down `sync_c`, then `async_b`, then `sync_a`. Draining the async ones inside the body coroutine while the sync ones stayed in `fn_teardowns` put every async teardown first, which an existing test caught. Both kinds now travel in one ordered list and are drained in one reversed walk. A test that arranges no async fixture keeps the pre-#1740 path exactly.

**One limitation is discharged.** `test_shared_session_precedence_over_arrange_session` recorded that *"an @arrange fixture that yields a loop-bound resource on the arrange session's loop cannot be used from the body"*. The body coroutine advances the fixture now, so it runs on whatever loop the body runs on.

**One behaviour changes for sync arranged fixtures, and it is a cost of the ordering rule.** Because both kinds share one teardown pass, a **sync** arranged fixture's teardown now runs **inside the test's event loop** rather than after it closes. A teardown that drives its own loop therefore fails:

```
error in teardown of fixture 'sync_with_own_loop': asyncio.run() cannot be called from a running event loop
```

`safe_teardown` converts that to a warning diagnostic, so the test still reports `passed`. It applies only to a sync arranged fixture in a test that also arranges an async one — a sync test, or an async test arranging nothing async, keeps the pre-#1740 path and is unaffected.

A sync teardown must not call `asyncio.run` or `loop.run_until_complete`. Narrowing the diversion to sync fixtures declared *after* the first async entry would shrink the exposure without removing it, and was rejected: it makes the behaviour depend on declaration position, which is harder to state than the rule it avoids.

**What pins this.** `python/tests/test_async_task_identity.py`, against `python/tests/data/arranged_task_identity/`. It asserts task identity with `asyncio.current_task()` names rather than `id()` — CPython can give a freed task's address to the next task, and a first probe on this issue read "one task" from that reuse. `anyio` is deliberately **not** a test dependency: task identity is the invariant, and the standard library states it directly.
