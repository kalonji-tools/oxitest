# ADR-0012: Block-scoped forms belong on the object

**Status:** Accepted
**Date:** 2026-08-06

A fixture has one lifetime, and the framework picks it. `Patcher` is built when the test starts and undone when the test ends; that is the whole point of letting the framework mediate it. But a test does not always want the whole test. It wants a patch installed for three lines, removed, and the un-patched behaviour asserted afterwards — inside the same test function.

That narrower window is a **block-scoped form**, and the question this ADR answers is what shape it takes.

The question is not hypothetical. In the loguru pathfinder migration (60 files, ~1580 test items, [#1668](https://github.com/kalonji-tools/oxitest/issues/1668)) the `Patcher` fixture went **entirely unused** — 0 injection sites against 41 uses of a block-scoped `patch_context()` the migration invented in its own conftest. A shipped fixture lost 41-to-0 to a helper an adopter wrote in an afternoon, because the shipped one could not express the window they needed.

oxitest has answered this question correctly twice already, without ever writing the answer down:

| Object | Block-scoped form | Where |
|---|---|---|
| `StdCapture`, `FdCapture` | `disabled()` | `python/oxitest/_bridge/_builtins/_capture.py:73`, on the shared `_CaptureBase` |
| `LogCapture` | `at_level()` | `python/oxitest/_bridge/_builtins/_logcapture.py:187` |

Both are methods on the state-carrying object. Neither is a second concept. That convergence was accidental — nothing recorded it as a rule, so nothing applied it to `Patcher`, and nothing would have stopped the next built-in from going a third way.

## Considered Options

1. **A separate concept for block-scoped utilities.** A registry, a decorator, a namespace — the shape oxitest actually shipped once, as helpers. Rejected, and already retired: see [ADR-0009](0009-fixture-system-redesign.md) Amendment 5, which removed the helper column from that ADR entirely. This option is listed because it is the one the project has empirically been tempted by, not because it is live.
2. **A sibling top-level function.** `oxi.patch_context()` beside `Patcher`, in the manner of `oxi.warns()` beside `WarnCapture`. Workable, and correct in the one case where the two surfaces answer *different questions* — `oxi.warns()` asserts that a specific warning was emitted, while `WarnCapture` collects everything for inspection. Rejected as the general rule, because when the two surfaces answer the *same* question at different widths, splitting them across two names doubles the vocabulary and leaves each half looking like the wrong choice from the other's documentation.
3. **A method or classmethod on the same object (chosen).** One name, one concept, two widths.

The adjacent-framework survey ([#1699](https://github.com/kalonji-tools/oxitest/issues/1699), five frameworks against primary sources) found **nobody ships block-scoping as a separate concept**: pytest's `monkeypatch.context()`, unittest's `self.enterContext()` and Go's `t.Cleanup()` are all methods on the state-carrying object. Option 3 is the industry's answer as well as this codebase's.

## Decision

> **An object with a lifetime boundary exposes any narrower, block-scoped form as a method or classmethod on itself — never as a second concept or a separate registration.**

### Rule 1 — The form is a member of the object it narrows

If an object carries state whose disposal the framework schedules, and some tests need that state installed for less than the full lifetime, the narrower window is spelled as a member of that same object. Not a parallel type, not a registry entry, not a free function that constructs a second instance behind the user's back.

The reason is that both widths are the *same capability*. `StdCapture` captures output; `StdCapture.disabled()` stops capturing output for a block. A reader who has found one has found the other, `dir()` shows them together, and the type checker follows the call. Split across two names, each is discoverable only by someone who already knows it exists.

### Rule 2 — `classmethod` when the form must be reachable without injection

An instance method is only reachable from a test that already injected the fixture. Where the evidence says users reach for the block-scoped form *instead of* injecting — not in addition to it — an instance method puts the feature behind the very step those users are declining to take.

`Patcher` is the measured case: **41 block-scoped uses, 0 injections.** An instance method would have been unreachable from all 41. pytest hit the identical wall and resolved it the same way — `MonkeyPatch` is both the injected fixture and a directly constructible class whose `context()` is the documented route when the fixture is not available. That reading comes from the adjacent-framework survey on [#1699](https://github.com/kalonji-tools/oxitest/issues/1699), which worked from primary sources; it is cited here as corroboration, not re-derived.

So the test is empirical, not stylistic: **if the block-scoped form is the dominant shape rather than a refinement of the injected one, it is a `classmethod`.** Where the block-scoped form genuinely narrows an already-injected object — `disabled()`, `at_level()` — an instance method is correct, because the instance is by definition already in hand.

### Rule 3 — Not every object needs one

This ADR does not say every lifetime-bearing object must grow a block-scoped form. Four of oxitest's eight built-ins correctly have none:

- `TempDir` and `TempDirFactory` are value carriers. There is no narrower window over "a directory exists".
- `TestContext` is metadata plus finalizer registration; narrowing it would mean narrowing the test itself.
- `WarnCapture` has no block-scoped *form*, and should not — `oxi.warns()` covers the assertion case as a genuinely different question, per option 2 above.

The rule governs the shape a block-scoped form takes **if** one is needed. Deciding that one is needed is a design judgement, made per object, on evidence of how the object is actually used.

### Rule 4 — A lifetime boundary is not, by itself, a reason to be a fixture

The rules above assume the object *is* a fixture and ask what shape its narrower form takes. This rule asks the prior question, because the obvious answer to it is wrong.

[ADR-0009](0009-fixture-system-redesign.md) Amendment 5 says mediation is justified by lifecycle rather than by visibility, and that is right as far as it goes — but "has a lifetime boundary" does not separate a fixture from a `with` statement. **`with` has a lifetime boundary too.** Setup, teardown, teardown-on-exception: Python ships all of it, needs no framework, and costs one line. If having teardown were sufficient justification, every context manager in the standard library would be a missing fixture.

So the sufficient condition is narrower. Framework mediation is justified when **at least one** of these holds:

1. **The boundary must open before the test body.** A block can only cover what happens after it opens.
2. **Teardown needs knowledge only the framework has** — the test's outcome, its name, or a CLI setting.
3. **The boundary is wider than one test.** No in-body construct spans tests.

A fourth condition — *"the value is framework state"* — was retracted by [#1949](https://github.com/kalonji-tools/oxitest/issues/1949); see the amendment below.

Each is load-bearing for at least one built-in, and none is decorative:

| Condition | Built-ins | Evidence |
|---|---|---|
| 1 — opens before the body | `StdCapture`, `FdCapture`, `LogCapture`, `WarnCapture` | A probe test whose *other* fixture printed during setup read that output back from `cap.readouterr()` inside the body. An in-body `with` cannot see it — it had not opened yet |
| 2 — framework-only knowledge | `TempDir` | Its teardown consults the test's result and `--keep-tmp` (`_builtins/_tempdir.py:170`–`:185`, *"mode == `failed`: preserve only on failure"*) and prefixes the directory with the test's name. `tempfile.TemporaryDirectory()` knows none of that |
| 3 — wider than one test | `TempDirFactory` | `scope = "session"` (`_builtins/_tempdir.py:191`) |

**Amendment (#1949) — condition 4 is retracted.** It read *"The value **is** framework state. There is nothing to construct; the object *is* the running test's context,"* and `TestContext` was its only member.

The condition was derived from the built-in inventory rather than from the principle, and that inventory contains `TestContext` as a fixture only because injection was the sole delivery mechanism available when it was written. Read forwards, the condition argues the opposite of what it claimed: **"there is nothing to construct" is a reason *not* to mediate.** Mediation schedules a lifetime, and ambient state has no lifetime to schedule. This is Amendment 5's *"symmetry is not a justification"* reaching a condition of this ADR's own.

`TestContext` therefore leaves this rule's inventory. It is reachable without injection via `TestContext.current()`, which is Rule 2's shape and Rule 2's justification — the form must be reachable by code that cannot be injected into. Rule 3 is unaffected: `TestContext` still needs no block-scoped form, for the reason already given there.

The runtime built-in registry is deliberately **not** changed. `TestContext` stays in `BuiltinFixture._registry` because that registry is what makes `ctx: TestContext` resolve, and that spelling is semver-protected. Deregistering it was prototyped and measured: it breaks injection outright (`fixture 'ctx' not found`, exit code 3), costs five production special-cases, and silently drops `TestContext` from `oxitest --fixtures`. This rule governs *justification*, not registration.

**Retirement.** Two spellings predate `current()` and both keep working under semver: `ctx: TestContext` injection, and `fx.oxi.ctx`. Both are legacy as of #1949 and are retired at v4. Neither is documented as a peer of `current()`.

**`Patcher` satisfies none of them.** Patching is inherently something a test does during its body; the undo needs no outcome, no test name, and no width beyond the block. That is a better account of the 41-to-0 result than "it lacks a block-scoped form": adopters did not overlook the fixture, they correctly declined a mediation that buys nothing. Its block-scoped form is therefore the **primary** form, not a companion to the injected one — which is exactly what Rule 2 concluded from the usage evidence, reached here from the design side instead.

This rule refines ADR-0009's line rather than contradicting it. Amendment 5 was separating lifecycle from *visibility*, and settled that correctly. It did not ask which lifecycles need mediating, because in that argument no lifecycle was in question. That is the gap this rule closes.

**It is deliberately not a gate.** Conditions 1–3 are judgements about intent, and a test cannot ask why an object exists. What is gated is the narrower, checkable question in Rule 5.

### Rule 5 — The decision is recorded, not remembered

`python/tests/test_builtin_shape_rule.py` partitions every type in `BuiltinFixture.registered_types()` into exactly one of three buckets — has a block-scoped form, needs none, or is a known gap with an issue number. The partition is asserted in both directions, so a newly registered built-in that nobody classified fails the suite, and a bucket entry naming a type that no longer registers fails it too.

This is the operative half of the ADR. A design rule that lives only in prose is applied by whoever remembers it; the gate makes the shape decision a thing you cannot skip silently when adding a built-in.

The gate reaches oxitest's own built-ins, because that is the set the framework can enumerate. The **rule** is broader — see Scope.

## Scope

The rule binds any object with a lifetime boundary: oxitest's built-ins, fixtures shipped by plugins, and fixtures users declare in their own `__fixtures__.py`. It is a design principle, not a built-in checklist.

Enforcement necessarily has a narrower reach than the rule. Rule 5's gate can only enumerate what oxitest owns, and no gate can inspect a user's test tree for whether a second fixture should have been a method. **The gate is the rule's worked example, not its boundary** — for plugin and user code the rule is advice, applied by the author, and this ADR is what they are pointed at.

## Relationship to ADR-0009 Amendment 5

The clause *"never a second concept, never a separate registration"* appears in this ADR's decision statement as a **constraint on the chosen shape**. It is not this ADR's to justify.

[ADR-0009](0009-fixture-system-redesign.md) Amendment 5 owns that argument. It retracted the helper column on the grounds that a helper has no lifecycle, and that where code lives is Python's job while when a value is built and disposed is the framework's. This ADR cites that reasoning and does not reproduce it, per `CLAUDE.md`'s arity rule — exactly one file defines each fact.

The division is worth stating plainly, because the two halves catch different failures and neither would have caught the other's:

| Failure | Caught by |
|---|---|
| The helper system — a second concept invented to work around a **loader** gap | Amendment 5 (lifecycle vs visibility) |
| `Patcher` — one concept, **missing** its block-scoped form | this ADR |

A version of this rule that claimed both would be overstating its reach. The helper system was not a badly-shaped block-scoped form; it was a reachability workaround, and it died to a different argument.

## Why a new ADR rather than an ADR-0009 amendment

[ADR-0009](0009-fixture-system-redesign.md) governs the fixture **system**: declaration files, lifetime tiers, the B1 boundary, namespace derivation. This ADR governs how a lifetime-bearing **object** exposes a second entry point, which is interface design — [ADR-0005](0005-immutable-by-default-interfaces.md)'s family, not ADR-0009's.

There is also a textual bar. Amendment 5 set its own boundary explicitly — *"It reverses nothing else, and adds no principle"* — and it is the amendment that carried the other half of this decision. Adding a principle by a tenth amendment to the same ADR would contradict the amendment that declined to add one, in a chain that is already long enough to be read selectively.

## Consequences

- **`CONTEXT.md` gains a second design principle.** It states the rule in short form and names this ADR as the single source of truth, the same arrangement *Immutable by Default* has with [ADR-0005](0005-immutable-by-default-interfaces.md). That placement is load-bearing rather than decorative: `docs/agents/domain.md` instructs every agent skill to read `CONTEXT.md` before exploring and to use its vocabulary, which is the only reach this rule has over agent-authored work.
- **Adding a built-in now has a required step.** Classify it in `test_builtin_shape_rule.py`. Choosing "needs none" is a legitimate outcome and costs one line; what is no longer available is not choosing.
- **`Patcher` is recorded as a known gap, not as a violation.** It is registered in the gate's `KNOWN_GAP` bucket citing [#1696](https://github.com/kalonji-tools/oxitest/issues/1696), which will add `Patcher.context()`. That bucket entry deletes itself when the gap closes — if `Patcher` gains the form while still listed as a gap, the partition still passes, but the member assertion in [#1696](https://github.com/kalonji-tools/oxitest/issues/1696)'s own change will move it to the first bucket.
- **`Patcher` is semver-protected** (`docs/user/reference/stability.md:14`), so the fix for that gap is additive — a new member beside the existing fixture — and does **not** wait on a major release.
- **`Patcher`'s fixture form is left standing without an independent justification.** Rule 4 finds it satisfies none of its conditions, so once `Patcher.context()` exists the injected form is a vestige rather than a peer. This ADR does **not** retire it: `stability.md:14` protects it, so removal is a major-version conversation on the same v4.0.0 gate as [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), and nothing is gained by breaking it early. Recorded here so that a later reader does not mistake its survival for an endorsement, and so the question is already framed if that release comes.
- **Six built-ins are re-confirmed as fixtures, on stated grounds rather than by inheritance.** Before Rule 4 the only recorded justification was "has a lifetime boundary", which is satisfied by any `with` statement and so justified nothing. Each now names the condition it meets. Two of the eight do not: `Patcher`, as above, and — since #1949 retracted condition 4 — `TestContext`, whose ambient accessor is now the justified route. Both stay registered under semver until v4.
- **This ADR has no structural gate.** `docs/adr/` sits outside `mkdocs.yml`'s `docs_dir` (`docs/user`), so `mkdocs --strict` never validates its links, and no other check reads it. Its cross-references are maintained by reading, and a reader who finds one stale should treat that as expected maintenance rather than as evidence the decision moved.
- **The user-facing statement lives in `docs/user/how-to/use-fixtures.md`**, where someone writing a fixture will meet it, and cites this ADR by absolute GitHub URL because `docs/adr/` is not published on the documentation site.
