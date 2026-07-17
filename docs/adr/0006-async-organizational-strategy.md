# ADR-0006: Async organizational strategy for oxitest

**Status:** Proposed
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
| async | `shared` / `session` | `SharedAsyncManager.resolve()` on the shared async session |
| async | `each` (function scope) | per-test-lifetime loop, `run_until_complete` for setup and teardown |

The per-test-lifetime loop is owned by the fixture instantiator, created lazily on first each-scope async fixture, shared with the test body if the test is `async def`, and closed after cleanup. `AsyncBackend.run` is *not* used in this path.

### The one illegal cell

Sync test + function-scope async fixture is **rejected at arrange time**, always, regardless of `strict` config. Diagnostic names three legal exits:

1. Make the test `async def`.
2. Convert the fixture to `shared` (or `session`) scope.
3. Convert the fixture to a `def` (sync) fixture.

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
- **`strict` semantics unchanged.** `strict = "abort" | "warn" | "off"` continues to toggle style rules (bare asserts, dict parametrize, missing mark reasons). B′ rejection is orthogonal and always-on.

## Alternatives Considered

- **B — Parallel-subpackage split (`oxitest.aio.*`).** Rejected. The one concrete benefit (~1ms `asyncio` import cost skipped for pure-sync users) does not offset +~120 LOC of implementation, ~90–150 lines of user migration, doubled docs surface, and the `Fixtures` naming clash between packages. The other selling points (discoverability, aio-only extension) are speculative — no aio-only features are planned in the 6–12 month horizon (verified during grilling). Extraction to `oxitest.aio.*` remains open as a future breaking-change one-shot if aio-only features materialize and earn the split.

- **A alone (silent polymorphism, no B′).** Rejected. Sync test + function-scope async fixture "works" but with unexpected loop-lifecycle latency, no diagnostic, and no navigational hint if the user's intent was actually a different combination. Pytest #12930 (gated warn/error) → #14015 (always-on hard error) is direct empirical evidence that silent polymorphism drifts into confusion. `strict = "abort"` DNA plus green-field user base makes always-on rejection cheap now.

- **B + B′** — subpackage split plus loud rejection. Rejected implicitly by rejecting B. B′ is a policy layer that composes on either A or B; the axes were evaluated independently. Subpackage split (B) failed on its own merits; layering B′ on top does not rescue it.

- **B′ gated by `strict`.** Considered. Rejected in favour of always-on because `strict` toggles style, not correctness. Pytest's own arc from gated (#12930) to always-on (#14015) is direct evidence that gating was insufficient.

## Related

- Wayfinder map: [#1530 — Async organizational strategy for oxitest, with @arrange as first proof-of-fit](https://github.com/kalonji-tools/oxitest/issues/1530)
- Prototype tickets (all closed):
  - [#1531 — Prototype: polymorphic-unified async pattern](https://github.com/kalonji-tools/oxitest/issues/1531)
  - [#1532 — Prototype: parallel-subpackage async pattern (oxitest.aio.*)](https://github.com/kalonji-tools/oxitest/issues/1532)
  - [#1533 — Research external proposals + prototype most promising (fallback: test-kind hybrid B′)](https://github.com/kalonji-tools/oxitest/issues/1533)
- Downstream spec: [#1535 — Spec: apply chosen async pattern to @arrange (including silent-failure bug fix)](https://github.com/kalonji-tools/oxitest/issues/1535)
- Root-cause reference: `python/oxitest/_bridge/_fixture_instantiator.py:138-151` — `_unpack_sync` silent-discard of coroutines and async generators.
- Follow-up (tracked outside this map): `AsyncBackend.run` seam refactor — loop-lifecycle-aware replacement for the current `run(coro) -> _T` shape.
