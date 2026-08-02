# ADR-0009: Fixture system redesign

**Status:** Accepted (amended 2026-08-02 — see [Amendments](#amendments))
**Date:** 2026-07-28

The fixture system was designed with a single-location assumption: fixtures live in `conftest.py`, declared via a `Fixtures()` instance whose `.fixture` decorator accumulates definitions during conftest loading. Wayfinder map [#1703](https://github.com/kalonji-tools/oxitest/issues/1703) opened the debate on where fixtures may live — grilling [#1706](https://github.com/kalonji-tools/oxitest/issues/1706) named **Position 4**: promote test-file top-level to first-class, keep the existing `Fixtures()` machinery, add a `ModuleSource` variant on top of the current registry, drop `registrar-in-test-module`, add a new `registrar-in-class-body` violation.

A follow-on first-principles brainstorming session (recorded in the redirect comment on [#1707](https://github.com/kalonji-tools/oxitest/issues/1707#issuecomment-5101919212)) reasoned that Position 4 was a **retrofit** onto machinery built around a single-location assumption. Asking instead "what is the framework's actual job in the fixture system?" produced a structurally different answer, validated with a runnable prototype under `scripts/prototype_fixture_redesign/` — **an uncommitted local design exercise, not a path in this repository** (Amendment 4). This ADR codifies that answer.

The core reframe is one sentence: **visibility is Python's job; lifecycle is the framework's job.** Python's package/module hierarchy already decides who can see what — the framework doesn't need to redo that. What only the framework can do is track when a value gets instantiated and when it gets disposed. Everything else — location, discovery mechanism, declaration syntax — falls out of that split.

## Considered Options

1. **Retrofit Position 4 onto the current shape.** Add a `ModuleSource` variant next to `ConftestSource`, keep `Fixtures()` and `Helpers()` instance registries, keep walk-up-tree conftest discovery, drop `registrar-in-test-module`, introduce `registrar-in-class-body`. Rejected: it grafts a new axis onto machinery that was never meant to carry it, and preserves the `Fixtures()` / `Helpers()` `&mut` exceptions on [ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4 alongside a new co-existing declaration path. The result is defensible but organically foreign — two ways to declare a fixture, one legacy, one new, both alive indefinitely.

2. **Full runtime enforcement of file conventions via a custom import hook.** Install a Python meta-path finder that intercepts imports of any non-conforming file and rejects fixture-decorated functions found outside `__fixtures__.py` / `__helpers__.py` / `__init__.py` / `test_*.py`. Maximum enforcement, no possibility of dead-code fixtures in unscanned files. Rejected: high runtime cost, poor interoperability with editors and static analysis, breaks under the standard three-tier collection fallback when AST prescan fails. Convention-plus-loud-collection-time-error achieves the same goal with none of the cost.

3. **Principle-plus-rules with file-convention discovery (chosen).** Establish the visibility/lifecycle reframe as the governing principle. Define eight rules covering declaration files, lifetime tiers and boundaries, B1 strict boundary, lifetime cap, proxy access, plugin convergence, autouse, and retirements. Discovery via Rust AST prescan on a reserved-name file set — dunder convention matching Python's own `__init__.py` / `__main__.py`, zero collision with user modules. Enforcement via prescan-time errors (loud rejection at the shallowest catchable frame, per [ADR-0006](0006-async-organizational-strategy.md)). This originally added "plus a strict dial for shortcut access"; the dial is retracted (Amendment 3) and shortcut access is unconditional. No new tooling; existing `ty` + collection-time diagnostics suffice.

## Decision

Option 3. The principle below governs the fixture system; the eight rules below define the surface. Follow-on impl work is enumerated in **Consequences** (23 items in 7 phases); this ADR listed them but did not file them. They were filed post-merge as [#1708](https://github.com/kalonji-tools/oxitest/issues/1708)–[#1722](https://github.com/kalonji-tools/oxitest/issues/1722) and [#1727](https://github.com/kalonji-tools/oxitest/issues/1727), so that enumeration is now a record of the plan as accepted, not the live work list.

### Principle

> **Visibility is Python's job; lifecycle is the framework's job.** A fixture is a Python callable with a declared **lifetime boundary** — the code-structural unit whose exit triggers teardown. Instantiation is lazy: creation is deferred until a test inside the boundary requests the value. Where the callable lives, who can import it, and what namespace it appears under are determined by Python's package/module hierarchy — the framework's only fixture responsibility is deciding *when* the value gets built and torn down.

### Rule 1 — Declaration files

**Status:** shipped (#1708, #1710, #1711, #1712); the file convention was amended by Amendment 1, and the helpers column is retracted by Amendment 5.

Fixtures may be declared only in three file kinds. The Rust AST prescan (`rustpython-parser`) scans these files; everything else is invisible to the framework by design.

| File | Fixtures allowed | Notes |
|------|------------------|-------|
| `__fixtures__.py` (any package level) | ✓ any lifetime | General fixture home |
| `__init__.py` (any package level) | ✓ `package` only (recommended) | A *declaration home* for package-lifetime things — not what defines the package boundary (Rule 2) |
| `test_*.py` | ✓ `function` or `module` only | Inline; fixture lifetime capped at module |

A fixture accidentally placed in `helpers.py` or `utils.py` is invisible to the framework — dead code by design. Enforced by convention, not by walking every module. Declaration API is a pure decorator with zero import-time side effects: `@oxi.fixture(lifetime=...)` (required kwarg), writing marker attributes directly on the wrapped function.

> **Retracted in part — Amendment 5 (#1781).** This rule originally listed **four**
> file kinds, adding `__helpers__.py`, and gave the table a "Helpers allowed"
> column; the declaration API originally paired `@oxi.helper` (no lifetime) with
> `@oxi.fixture`. Both are withdrawn: helpers are not a framework concept, so
> they get no declaration file and no decorator. `__helpers__.py` never occurred
> anywhere in this repository outside this ADR, and `@oxi.helper` was **deleted**
> by #1788 rather than converted into the real decorator this rule specified.
> Prescan scans exactly `__fixtures__.py` and `__init__.py`
> (`src/pipeline/collection.rs:436`) — which is now the whole rule, not a subset
> of it.

### Rule 2 — Lifetime tiers and boundaries

**Status:** shipped (#1710, #1711, #1745); amended by Amendment 1, and its `session` row corrected by Amendment 4.

`Lifetime` is a `StrEnum` with four values, ordered by the **breadth of the code-structural unit** they name — not by the strength of the guarantee they offer. Under parallel execution the ladder is deliberately non-monotonic in guarantee strength; see *Lifetimes under parallel execution* below. Each tier has exactly one boundary — the code-structural unit whose exit triggers teardown.

| Lifetime | Boundary | Disposal trigger | Under parallel execution |
|----------|----------|------------------|--------------------------|
| `function` | The individual test | After the test completes | No effect |
| `module` | The Python module (test file) | After all tests in the module complete | No effect |
| `package` | The directory subtree containing the declaration | After all tests in the subtree complete | **Exactly once per run** — collapses the subtree onto one worker |
| `session` | The worker task group | At task-group teardown | **Once per task group** — no scheduling constraint of its own; the grouping is whatever `package` declarations produce |

**A package is any directory.** `__init__.py` is not required and its absence does not make a directory ineligible. What the framework needs from a package is a subtree to bound disposal, to filter the B1 catalog, and to derive a namespace segment — a directory supplies all three. PEP 420 namespace packages are the norm in modern Python, so requiring the marker file would make oxitest stricter than the language itself, and would contradict this ADR's own principle that visibility is Python's job. `__init__.py` remains a legal and recommended *declaration home* for package-lifetime fixtures (Rule 1); it is not what *defines* the boundary. The two roles were originally conflated.

Yield-based fixtures use `Yields[T]` and expose their teardown code after the `yield` statement (unchanged from the current shape).

#### Lifetimes under parallel execution

oxitest distributes work across worker subprocesses by default (`min_parallel_tests = 100`). Any tier wider than the module therefore has to answer a question a single-process framework never faces: *how many* instances exist when the boundary spans more than one process.

`function` and `module` are unaffected — the scheduler never splits a module across workers, so both tiers are exact for free. The two wide tiers each take one side of a trade the framework cannot avoid:

- **`package` guarantees exactly one instance per run**, and pays for it by co-locating the declaring directory's entire subtree onto a single worker. The guarantee is *structural*, not a caching hint: the scheduler is constrained so that the situation in which a second instance could exist never arises.
- **`session` guarantees one instance per worker *task group***, and constrains the scheduler not at all. Because it constrains nothing it also earns nothing of its own: a worker pops task groups in a loop (`src/worker_session.rs:271`–`272`) and every task builds a fresh fixture session (`python/oxitest/_bridge/worker.py:265`), while only `package` declarations merge modules into a group (`src/filter.rs:270`–`292`). Absent one, a task group is a single module and `session` is `module` under a wider name. It is the tier for a resource that is safe to rebuild and unsafe to share across processes — a connection pool, a compiled-artifact cache — never a global singleton.

The ladder is non-monotonic on purpose. `package` buys exactness and charges parallelism; `session` buys parallelism and charges exactness. Neither dominates the other, so the choice stays with the user. `session`'s side of that trade is weaker than it looks, though, and this is the one asymmetry worth stating plainly: it is the only tier whose instance count is set by *another* tier's declarations rather than by its own boundary. Playwright's `scope: 'worker'` and Vitest's `scope: 'worker'` do name the process, and this ADR originally borrowed that precedent for `session` — it should not, because oxitest's `session` is not per-process (Amendment 4). Whether it *should* be given real per-process semantics, which would require the scheduler to carry fixture state across task groups, is left open rather than settled here.

**A module belongs to its outermost declaring ancestor.** Where declarations nest, the shallowest wins: a fixture declared higher up already spans the whole subtree, so anchoring on a deeper declaration would still let the scheduler split the outer package across workers and rebuild its value.

**The collapse is announced.** A `package` declaration that merges two or more modules emits a collection-time `WARNING` naming the declaring file, the fixture, and the module count, and pointing at the two exits — narrow the fixture's package, or drop to `lifetime="module"`. A declaring package holding a single module stays silent: it costs no parallelism, and warning there would train users to ignore the message. Documenting the cost beside the tier follows xUnit, which states its widest tier "must be designed for with this parallelism requirement in mind", and Vitest, which introduces its `file`/`worker` collapse in the same paragraph as the tiers themselves.

**Builtin fixtures do not trigger the collapse.** Only user-declared `@oxi.fixture` lifetimes do. `_TempDirFactoryFixture` (`_builtins/_tempdir.py`) declares session scope and would otherwise serialise every oxitest run.

### Rule 3 — B1 strict boundary

**Status:** shipped (#1713); amended by Amendment 2.

A fixture is usable only by tests in its **anchor package** or descendant packages. The anchor package is the directory containing the declaration file (Rule 2 — `__init__.py` not required). For a test at `a.b.c.test_x`, the ancestor chain is `[a, a.b, a.b.c]`; the test may use fixtures anchored anywhere in that chain plus its own module.

Sibling and unrelated packages cannot access the fixture. Attempted use raises `BoundaryError` (diagnostic code `fixture-boundary`) naming the fixture's anchor, the test's package, and the legal exits. No allow-comment escape hatch. No `strict` softening — and note that the `"warn"` position this originally named has never existed (Amendment 3). This follows [ADR-0006](0006-async-organizational-strategy.md)'s loud-rejection precedent.

Package-scope fixtures anchored at `tests/api/` are usable from `tests/api/v1/test_x.py` (descendant) but not from `tests/other/test_y.py` (sibling).

**Enforcement is at access time, not collection time.** This ADR originally specified the opposite:

> Attempted use is a **collection-time error** […] violations fire at collection time, before any test runs, at the shallowest catchable frame.
>
> Hierarchical prescan enforces this at discovery: given a test being collected, the framework prescans only the ancestor-chain declaration files.

That is **amended**. Prescan extracts fixture *declarations*, not fixture *usages*, so nothing at collection time knows that a test intends to reach `fx.admin.conn`. The gate fires instead at the two resolution routes — the `fx` proxy and `Fixture[T]` parameter injection — and again when descending into a fixture's own dependencies.

The amendment is not a weakening of the loud-rejection principle, because a collection-time gate could never have been the *only* gate: dynamic access (`getattr(fx, name)`) defeats any static analysis, so an access-time check is required whatever else exists. Collection-time enforcement is therefore an optimisation layered on top — earlier failure, and the precondition for the editor-squiggle tooling in this ADR's Consequences — tracked by [#1758](https://github.com/kalonji-tools/oxitest/issues/1758).

**A fixture's dependencies are governed by the fixture's own anchor**, not by the location of the test that triggered resolution. Otherwise a fixture anchored at `tests/api/` could acquire a dependency anchored at `tests/api/v1/` whenever it happens to be resolved by a test living there — a dependency it could never legally declare — and, at `lifetime="package"`, cache a value that embeds one from a narrower boundary. This is the "declaration time" half of enforcement referred to in the Consequences.

### Rule 4 — Lifetime cap

**Status:** shipped (#1711); amended by Amendment 1, whose retraction of the `package`/`session` equivalence is itself re-grounded by Amendment 4.

Declared `lifetime` cannot exceed the declaration site's boundary.

| Declaration site | Max legal lifetime |
|------------------|-------------------|
| Inline in `test_*.py` | `module` |
| `__fixtures__.py` at package X | `package` (anchored at X) |
| `__init__.py` at package X | `package` (anchored at X) |
| Any of the above at the rootdir package (`tests/`) | `package` (exactly once per run) or `session` (once per task group) |

Anything else is a **declaration error at prescan time** with three legal-exit hints (move to `__fixtures__.py` at package level; drop to `module` lifetime; restructure as a rootdir fixture).

**`session` is available only at rootdir, and is not a synonym for rootdir `package`.** This ADR originally argued the opposite:

> `session` is available only at rootdir because below root, `package(root)` and `session` collapse to the same runtime behavior (the fixture's visibility subtree is smaller than the run — either it's never referenced outside the anchor package under B1 and equals `package`, or it would leak). At rootdir the two ARE the same thing; the framework accepts `session` as the idiomatic name for the run-lifetime tier.

That equivalence is **retracted** — though not for the reason Amendment 1 gave. Amendment 1 replaced it with "`session` is one instance per worker process", which is itself wrong; Amendment 4 retracts the replacement.

The two tiers differ because their guarantees have different *sources*. Rootdir `package` is exactly-once **structurally**: it collapses the whole suite onto one worker and pays for the guarantee in parallelism. `session` constrains the scheduler not at all and takes whatever grouping it happens to find, so in a suite with no `package` declaration a rootdir `session` fixture is built once per module. In a suite that does declare one at rootdir the two coincide — but only because `package` did the work. Offering both is still more expressive than declaring them synonyms; what a reader must not do is read `session` as a guarantee.

The *restriction* of `session` to rootdir survives, for the half of the original argument the retraction does not touch: declared below root, a `session` fixture would outlive the subtree that is allowed to see it, which is exactly what Rule 4 exists to prevent.

**A `session` fixture cannot be a true singleton.** Anything that must happen exactly once per run — a database migration, a schema create, a shared artifact build — belongs at rootdir `package` and pays the parallelism cost. Frameworks that do offer a cross-process once-per-run hook restrict it to serialised handles rather than live objects; Jest is explicit that "any global variables that are defined through `globalSetup` can only be read in `globalTeardown`. You cannot retrieve globals defined here in your test suites."

### Rule 5 — Access via the `fx` proxy

**Status:** shipped (#1708, #1713, #1714); amended by Amendments 2 and 3, and the `hlp` half is retracted by Amendment 5. The `namespace=` override this rule names is still unbuilt — `@oxi.fixture` accepts only `lifetime=` — and is [#1782](https://github.com/kalonji-tools/oxitest/issues/1782)'s question, not a helpers-only gap.

Tests receive fixtures via a synthesized proxy parameter — the type annotation `Fixtures` reappears here as an access proxy (the old instance-registry meaning is retired, see Rule 8):

```python
def test_flow(fx: Fixtures):
    conn = fx.api.conn                    # qualified
    tx = fx.tx                            # shortcut — unconditionally legal
```

> **Retracted in part — Amendment 5 (#1781).** This rule originally synthesized
> **two** proxy parameters, adding `hlp: Helpers`, and the example above reached
> a helper through it (`resp = hlp.api.make_request(conn, "/users")`). The `hlp`
> proxy, `HelpersProxy`, and helper namespace derivation are withdrawn. Note the
> consequence for Rule 8's retirement list: `Fixtures` is a name being *reused*,
> but `Helpers` is now a name being *removed* — nothing reuses it — and #1788
> removed it.

**Qualified access** (`fx.<segment>.<name>`) walks the package path and always works when the fixture is in the test's ancestor chain. Cross-boundary use raises `BoundaryError` with an actionable diagnostic.

**Shortcut access** (`fx.<name>` without a package prefix) resolves the nearest visible fixture and is **unconditionally legal** — no diagnostic, no configuration. Resolution is B1-filtered exactly as qualified access is, so a shortcut can never reach a fixture the test could not reach by its qualified path; the shortcut saves keystrokes, never scope. Because a bare name carries no segment to attribute blame to, a cross-boundary shortcut reports `FixtureNotFoundError` rather than `BoundaryError`, matching what `Fixture[T]` injection already does (see *Error type is a function of the segment alone*, below).

This ADR originally gated the shortcut behind a three-position strict dial. That gate is retracted; see [Amendment 3](#amendment-3--the-shortcut-strict-dial-is-retracted-2026-07-30) for why, including the industry-precedent argument that motivated it and why the argument does not survive the fact that oxitest's dial has no `warn` position.

**Two access routes, asymmetric on purpose.** `Fixture[T]` parameter injection is bare-name *only* — `resolve_param` looks up by parameter name and no `Fixture[T]` spelling carries a package path. So un-prefixed access is mandatory on that route while the proxy route offers both forms. The asymmetry is not a claim that the routes are principled opposites: functionally `conn: Fixture[Connection]` resolves exactly as `fx.conn` does. It is that the injection route has no alternative spelling, so forbidding bare names there would delete the route, whereas on the proxy route the qualified form always exists. Recorded so a later reader does not mistake it for an oversight. A qualification syntax for `Fixture[T]` is not planned.

**Two-catalogs design constraint.** `FixturesProxy` must be able to consult two views — the **B1-filtered catalog** (fixtures visible to *this* test, used for resolution) and the **full catalog** (every fixture in the run, used for diagnostic quality). The prototype surfaced this: without the full view, the proxy cannot tell "package `api`" apart from "fixture `api`" when neither is in the filtered set (both would look like unknown names), and cross-boundary access reports as `FixtureNotFoundError` — "you have a typo" — when the correct diagnostic is `BoundaryError`. Neither view is optional.

The constraint is about *reachability of both views*, not about object count. This ADR originally said the proxies "hold two references", which read as two catalog objects; the implementation satisfies it with two query modes over the single `FixtureRegistry`, since the proxy already carries the test's module path and the registry can be asked either question. Materialising a filtered catalog per test would cost an O(all fixtures) pass for every test in the run and buy nothing the predicate does not already give.

**The guarantee is scoped to directory-anchored fixtures.** Declaration homes (`__fixtures__.py`, `__init__.py`) are registered run-wide — on the serial path and into every worker — so the full view is genuinely full and the verdict is deterministic. An inline declaration is registered only when its test module is imported, so its presence in the catalog depends on worker assignment, selection, and import order. Rather than let the *diagnostic* vary with scheduling, cross-module access to an inline fixture reports `FixtureNotFoundError` with a static hint that inline declarations are capped at `module` lifetime. Semantics are unaffected either way — the access is illegal in both readings. Upgrading the message using the declarations prescan already extracts is [#1759](https://github.com/kalonji-tools/oxitest/issues/1759).

**Error type is a function of the segment alone.** An unreachable segment raises `BoundaryError`; a segment unknown anywhere raises `FixtureNotFoundError`. When the segment is unreachable *and* the leaf does not exist there either, the boundary is reported first, with the missing leaf appended — the boundary statement is true regardless of the leaf, whereas leading with the typo would imply that fixing the spelling makes the access work.

**Naming clash rule.** A fixture named the same as a sibling package segment is shadowed by the segment in shortcut form (`fx.api` returns the sub-proxy, not a fixture named `api`); the fixture remains reachable via the qualified path. Convention: avoid the collision. (Originally *"Applies identically to helpers"* — void, Amendment 5.) This rule was vacuous until shortcut access existed — `FixturesProxy.__getattr__` had no fixture branch for a segment to win against — and became live with [#1714](https://github.com/kalonji-tools/oxitest/issues/1714).

**Framework builtins are not shortcut-reachable.** `fx.oxi.tmp` is the only spelling for a builtin; the reserved `oxi` namespace exists so framework names cannot collide with user fixture names, and hoisting them into the flat namespace would put `log`, `patch`, and `cap` where a user's own fixture of that name would clash. The registry names builtins after their private implementation class (`_TempDirFixture`), which the proxy's leading-underscore guard already rejects, so the property holds without a filter — but it holds *incidentally*, resting on a naming convention rather than a predicate, and is pinned by a regression test rather than left to be rediscovered.

**Namespace derivation.** Default namespace = the anchor-package segment name; overridable via `namespace=` on the decorator. Use overrides sparingly.

> **Not yet built** — the `namespace=` override. `@oxi.fixture` accepts only
> `lifetime=` today (`python/oxitest/_bridge/_fixture_decorator.py:25`), and
> whether the override should exist at all is [#1782](https://github.com/kalonji-tools/oxitest/issues/1782).
> This marker originally attributed the gap to #1715 and listed the `hlp` proxy,
> `HelpersProxy`, and the helper half of the naming-clash rule alongside it; those
> are retracted rather than pending (Amendment 5), which leaves `namespace=` as
> the only forward-looking claim in this rule.

### Rule 6 — Plugin convergence

**Status:** not yet built (#1717, #1718); amended by Amendment 4.

Plugins register fixtures via the **same decorator path as user code**. A plugin package with a `__fixtures__.py` file declares fixtures with `@oxi.fixture` exactly as users do; the framework treats each activated plugin as an ambient ancestor, making plugin fixtures visible session-wide under the plugin's declared namespace (e.g., `fx.postgres.pg_session` for a `postgres` plugin).

Plugins that need to generate fixtures at runtime (5% case — e.g., one fixture per detected DB schema) export an optional `register_fixtures(registry: FixtureRegistry) -> None` hook called at session initialization after AST prescan. Dynamically-added `FixtureDef`s land in the same registry the AST-scanned ones do; identical semantics after registration.

`FixtureProvider` and `HelperProvider` protocols retire. Migration from `FixtureProvider.register_fixtures(reg)` to the module-level `register_fixtures(registry)` hook is mechanical — the shape is close, only the entry point changes.

> **Half of this is done — Amendment 5 (#1781).** `HelperProvider` was deleted by
> #1788, so its retirement is history rather than plan. Only the `FixtureProvider`
> half remains forward-looking, and it is the half that has a downstream
> implementer (see Rule 8). The "mechanical migration" claim was already
> corrected by Amendment 4, point (d) below.

> **Not yet built** — #1717 (static path), #1718 (runtime hook). Four statements
> above are also **drifted**, corrected in Amendment 4. (a) "session-wide" mixes
> two things that Amendment 1 separated. Plugin fixture *visibility* genuinely
> **is** run-wide: plugin fixtures are ambient (`_fixture_registry.py:152`, whose
> docstring cites this rule by name) and are registered into every worker session
> (`_fixture_session.py:403`, called per session at `:343`). But *lifetime*
> `session` is one instance **per task group** — narrower still than the
> per-worker reading Amendment 1 gave it, see Rule 2 and Amendment 4
> (`python/oxitest/_bridge/worker.py:265`) — so the phrase cannot mean run-wide
> instantiation under either reading. **Read the sentence above as "ambient in
> every fixture session"** — the rule's own wording is left as accepted, per this
> sweep's convention of marking unshipped rules rather than rewriting text
> #1717 will replace. The restatement is **not** a narrowing of the visibility claim, which
> would replace this drift with a new one. (b) `register_fixtures` is typed on `FixtureRegistry`, which no
> public module exports. (c) A runtime-registered `FixtureDef` does not get
> "identical semantics" for free: semantics are keyed on its source variant, and
> only the module-anchored variant is B1-anchored, so the hook's signature has to
> say which variant a dynamically-added fixture carries. (d) The migration is not
> "mechanical" — `FixtureProvider` has no `register_fixtures` method at all, and
> its actual shape is a type-matched per-fixture provider object rather than a
> registry-mutating callback. Note that the *ambient* half of "ambient ancestor"
> already holds, per (a); it is the *ancestor* half that is unbuilt. The API shape
> is a decision rather than an implementation — see #1773.

### Rule 7 — Autouse

**Status:** not yet built (#1716); the autouse table was amended by Amendment 1 and again by Amendment 4.

`@oxi.fixture(autouse=True, lifetime="...")` fires for every test in the fixture's B1 boundary without being explicitly requested. Same shape as today; the lifetime cap from Rule 4 applies unchanged.

| `autouse=True, lifetime=X` | Fires… |
|----------------------------|--------|
| `function` | Once per test in the fixture's B1 scope |
| `module` | Once per module boundary in scope |
| `package` | Once per package boundary in scope — exactly once per run (Rule 2) |
| `session` | Once per task group — one module unless a `package` declaration merges the subtree (Rule 2). For autouse work that must happen exactly once per run, declare at rootdir with `lifetime="package"` |

Autouse fixtures remain accessible by explicit request (`Fixture[T]` or `fx.<name>`); autouse is additive, not exclusive. The invisibility concern historically raised against autouse is solved by tooling, not by removing the feature: `oxitest inspect` shows autouse-firing per test as a first-class view (a follow-on impl item — see Consequences).

> **Not yet built** — #1716. `@oxi.fixture` has no `autouse` kwarg
> (`python/oxitest/_bridge/_fixture_decorator.py:25` is
> `def fixture(*, lifetime: str)`), and `src/prescan.rs:1500` carries a test
> asserting `@oxi.fixture(lifetime="function", autouse=True)` is *rejected*, so
> the table above describes surface slice 9 will build;
> `docs/user/how-to/use-fixtures.md:436` already tells users so. Four further
> statements need Amendment 4, three of them **drifted**.
>
> **(a) "Same shape as today" is retracted**, on three independent grounds.
> Today's autouse is the `Fixtures()` registrar kwarg
> (`_fixtures.py:256`–`262`) that Rule 8 retires. Its shape is **run-wide
> ambient**, not B1-scoped: the only two source variants that can carry `autouse`
> today have no anchor field — `ConftestSource` (`_fixture_registry.py:79`) and
> `PluginSource` (`:85`, reached at `_fixture_session.py:407`, `:417`) — so
> `is_visible_from` falls through to `case _: return True`
> (`_fixture_registry.py:193`, "everything unanchored is ambient"), and
> `docs/user/how-to/use-fixtures.md:429` promises users it "runs for every test".
> And the candidate enumeration is not B1-filtered at all — see (b). Rule 7's
> shape is strictly *narrower* than today's, not the same. What does carry over,
> so this is not over-corrected: the fire-without-request **mechanic** is
> unchanged (`_fixture_session.py:843`–`845`). Vehicle and scope are what fail.
>
> **(b) Slice 9 must B1-filter the enumeration, not merely anchor autouse** — see
> [#1774](https://github.com/kalonji-tools/oxitest/issues/1774).
> `get_autouse()` (`_fixture_registry.py:405`) iterates `_by_name` and takes no
> `module_path`, while resolution applies B1 and **raises** on a filtered-out
> candidate (`_fixture_instantiator.py:374`–`376`). This is inert today only
> because every autouse-capable source is anchorless. The moment `autouse=True`
> reaches `ModuleSource`, every test outside the fixture's boundary errors with
> `FixtureNotFoundError` naming a fixture it never requested and cannot see.
> Read the direction carefully: this is the **inverse** of a boundary leak, a
> spurious hard error rather than over-permissive access. B1 itself holds on both
> resolution routes and needs no change.
>
> **(c) The `session` row is drifted — `session` is once per _task group_, not
> once per worker process.** A worker pops task groups in a loop until the shared
> queue drains (`src/worker_session.rs:271`–`272`) and every task builds a fresh
> session (`python/oxitest/_bridge/worker.py:265`, inside `run(task)` at `:238`,
> called per stdin line at `:366`) with a fresh per-instance session scope
> (`_fixture_session.py:298`); nothing caches across tasks. Measured on `main`:
> eight single-test modules over two workers built a `lifetime="session"` fixture
> **eight** times across two PIDs, and adding one `lifetime="package"`
> declaration to co-locate the subtree into a single task group brought it to
> **one**. Absent a `package` declaration a task group is one module
> (`src/filter.rs:275`) and a `session` declaration does not trigger co-location
> (`src/pipeline/collection.rs:274` filters on `package` only), so `session`
> degrades to per-module — the failure Amendment 1's point 1 described as
> pre-#1745. Amendment 4 should restate the count as "once per task group, which
> is one module unless a `package` declaration merges the subtree". The row's
> **advice holds and is load-bearing**: rootdir `lifetime="package"` is the only
> construct that actually delivers exactly-once. The same "per worker process"
> phrasing also appeared in Rule 2, Rule 4, Amendment 1, and in the shipped
> `lifetime=` docstring — all corrected here, which is what opened this sweep's
> scope from docs-only. The **legacy** `shared=True` surface repeats the claim in
> its own vocabulary (`python/oxitest/plugin.py:135`,
> `docs/user/how-to/run-in-parallel.md:92`,
> `docs/user/how-to/use-fixtures.md:422`–`424`, and the Rust warning text);
> that surface is on Rule 8's retirement list and is corrected under
> [#1778](https://github.com/kalonji-tools/oxitest/issues/1778), not here — it
> has an auto-arrange mechanism `lifetime=` does not
> (`src/pipeline/arrange.rs:232`–`255`), so its numbers cannot be carried across
> from this measurement.
>
> **(d) "Autouse is additive, not exclusive" holds only for `Fixture[T]`.** The
> autouse loop dedupes against annotation-derived names alone
> (`_fixture_session.py:844`), and function lifetime has no scope cache to dedupe
> through (`_scope_for` returns `None` for function scope,
> `_fixture_session.py:468`–`469`; caching is gated on that at
> `_fixture_instantiator.py:408` and stated at `:484`–`485`). So reaching an
> autouse fixture through **either** proxy route builds it twice in one test —
> measured on `main`, and once only when the fixture is `shared`. Additive, but
> duplicative, and for autouse-with-side-effects the side effect happens twice.
> Slice 9 inherits this; (b)'s enumeration filter does not fix it, because a
> fixture inside its own boundary still double-builds.
>
> **Confirmed as holding — do not re-open.** The Rule 4 lifetime cap genuinely is
> autouse-independent: enforcement keys on `decl.lifetime` alone
> (`src/pipeline/collection.rs:200`–`218`, `:288`–`312`) and
> `PrescanDeclaration` (`src/prescan.rs:85`–`90`) has no autouse field. The
> `package` row's "exactly once per run" holds structurally (`src/filter.rs:260`
> merges the subtree into one task group). The tooling justification is
> forward-looking **with a home** —
> [#1722](https://github.com/kalonji-tools/oxitest/issues/1722), slice 15 — and
> its per-*fixture* half already ships across the detail pane
> (`src/inspect/detail/fixture.rs:22`), the `UnusedFixtures` signal
> (`src/inspect/signals.rs:94`), the `autouse()` query predicate, and `--tree -v`
> tags; only the per-*test* view is unbuilt, so fix the tense, not the argument.
> Separately, Rule 7 does not mention the other autouse × lifetime constraint
> `main` already enforces — `AutouseRegistrationError` refusing async non-shared
> autouse (`_fixtures.py:255`, `docs/user/reference/errors.md:316`) — which slice
> 9 owns.

### Rule 8 — Retirements

**Status:** the helper entries are retired (#1787, #1788); the rest is not — retirement is slice 13 (#1720), preceded by the own-suite migration (#1719). Amended by Amendments 4 and 5.

The redesign retires the following surface. Each entry names what goes and why.

- **`Fixtures()` and `Helpers()` as instance-based registries** — the `db = Fixtures(name="db")` + `@db.fixture` pattern is replaced by module-level `@oxi.fixture` / `@oxi.helper`. The **names** `Fixtures` and `Helpers` are reused as the access-proxy type annotations in test signatures (Rule 5); the old instance usage no longer exists.
- **`conftest.py` as a special filename** — replaced by `__fixtures__.py` / `__helpers__.py` / `__init__.py`. Walk-up-tree conftest discovery (`find_conftest_paths` in `conftest_loader.py`) is replaced by hierarchical AST prescan on the ancestor chain of the tests being collected.
- **`registrar-in-test-module` strict violation and its `# oxitest: allow[registrar-in-test-module]` escape hatch** — the whole class of violation becomes nonsensical (there is no registrar to be in a test module). The allow-comment escape hatch was itself an [ADR-0008](0008-config-fail-closed-narrow-scope.md) violation and its removal restores the no-escape-hatch discipline.
- **`ConftestSource` variant** in `_fixture_registry.py` — replaced by a location-agnostic source variant carrying `defining_module_path` + `anchor_package_path`.
- **`FixtureProvider` and `HelperProvider` plugin protocols** — plugins converge with the user path via `@oxi.fixture` + `register_fixtures` hook (Rule 6).
- **[ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4's `Fixtures` / `Helpers` `&mut` exception** — no mutable registrar exists; the decorator writes marker attributes directly at import time, no accumulation phase.

> **The helper entries are retired; the rest still stands — Amendment 5 (#1781).**
> Three bullets above name helper surface, and all of it is gone as of #1788: the
> `Helpers()` registrar, the `HelperProvider` protocol, and ADR-0005 Rule 4's
> `Helpers` `&mut` entry. Read those three as **record of a completed retirement**,
> not as plan. Two corrections follow for the fixture halves, which are still
> forward-looking and still #1720's work:
>
> - The first bullet says the names `Fixtures` **and** `Helpers` are reused as
>   access-proxy annotations. Only `Fixtures` is — Rule 5's `hlp` proxy is
>   retracted — so `Helpers` was removed outright, with no successor meaning. The
>   sweep's point 2 below anticipated half of this ("only `Fixtures` is real
>   today"); Amendment 5 settles it permanently rather than as a state of play.
> - `@oxi.helper` appears in the first bullet as the replacement for
>   `@db.helper`. There is no replacement: the sentinel was deleted, not
>   converted, and point 3 below's requirement that it be converted is void.
>
> ADR-0005 Rule 4 itself still carries both `&mut` entries and needs its own edit
> — that is [#1721](https://github.com/kalonji-tools/oxitest/issues/1721)'s scope, in a different document.

> **Conformance sweep ([#1769](https://github.com/kalonji-tools/oxitest/issues/1769), 2026-07-30).** Nothing above is
> retired yet — the own-suite migration is [#1719](https://github.com/kalonji-tools/oxitest/issues/1719) and the
> retirement itself is [#1720](https://github.com/kalonji-tools/oxitest/issues/1720). All six targets are live on
> `main`. That is the expected state for a retirement plan, so the list is correctly forward-looking; four claims
> *about* the list are not, and are corrected here.
>
> **1. The list conflates two operations of very different cost.** Only **`ConftestSource`** is internal — its
> retirement really is a delete. Everything else is documented user-facing surface: **`conftest.py`** (14
> `docs/user/` pages, including one whose title is the retired concept), **`Fixtures()`** (8), **`Helpers()`** (6),
> and **`FixtureProvider`/`HelperProvider`**, which are *stable public API since 1.0.0*
> (`docs/user/explanation/provisional-apis.md:22`, `:26`) and pinned by conformance tests
> (`python/tests/docs/how-to/test_write_plugins.py:353`, `:382`). **`registrar-in-test-module` belongs on the
> documented side too** — the kebab-case id is absent from `docs/user/`, but the rule is documented under its prose
> name at `docs/user/explanation/strict-mode.md:182`, inside the section "The nine checks" (`:21`), which retiring it
> makes stale. Sharpest of all: **`docs/user/reference/stability.md:11` lists `Fixtures` as semver-protected**, and
> `:16` extends that to "`Plugin` dataclass and protocol interfaces" — so two entries here are surfaces the project
> has promised not to remove outside a major version. `Helpers` is *not* on that list, so the pair does not carry the
> same promise. **And `Fixtures` is not removed but *repurposed*** — Rule 5 reuses the name as a proxy type
> annotation, so a user's `fixtures = oxitest.Fixtures()` does not fail with a clean `AttributeError`; it becomes a
> call on a name that now means something else. That is a nastier migration than deletion, and a deprecation shim
> cannot simply alias it. **Amendment 4 should split this list in two** and give the public half a major-version
> story.
>
> **2. "The old instance usage no longer exists" is false.** Both registries are live and exported: `Fixtures`
> (`python/oxitest/_bridge/_fixtures.py:107`, exported `python/oxitest/__init__.py:194`) and `Helpers`
> (`_helpers.py:15`, exported `__init__.py:195`). The clause contradicts this rule's own Status line. **The last
> bullet repeats the defect** — "no mutable registrar exists; … no accumulation phase" is also present tense for a
> future state, while [ADR-0005](0005-immutable-by-default-interfaces.md) still carries both `&mut` entries (`:44`,
> `:45`). **Correction: sweep Rule 8's tense to "will be removed in slice 13 ([#1720](https://github.com/kalonji-tools/oxitest/issues/1720))"**;
> the same edit fixes Rule 5's parallel claim that the name `Helpers` is free to reuse. Of the two reused
> annotations, only `Fixtures` is real today (`proxy_ns.py:158`); `Helpers`/`hlp` has no injection path.
>
> **3. The helper *read* surface is missing from the list.** These bullets retire the *registration* pattern, but
> Rule 5 replaces the *read* surface, and none of it is named: `oxitest.helpers` (`python/oxitest/__init__.py:223`,
> `_read_helpers.py:34`) — the accessor `CLAUDE.md:246` mandates for oxitest's own suite; the `oxitest.helper`
> sentinel (`__init__.py:227`), whose `AttributeError` text steers users into `Helpers()` *and* `conftest.py`, both
> retired here — so it must be **converted** into the real decorator, not deleted; and the documented-but-nonexistent
> `fx.helpers` route (`_helpers.py:24`). A second steering site sits at `_read_helpers.py:51`.
>
> **4. The ADR-0008 citation does not hold.** [ADR-0008](0008-config-fail-closed-narrow-scope.md) scopes itself
> explicitly and narrowly to deserialization errors inside the `[tool.oxitest]` table; a source-line
> `# oxitest: allow[…]` comment is not config and cannot violate it. **Correction: cite instead ADR-0008's own
> rejection of `--allow-config-drift`** (option 3, `:16`: "escape hatches mean the guarantee isn't real") **and the
> loud-rejection DNA it invokes at option 4** (`:18` — [ADR-0006](0006-async-organizational-strategy.md) async
> hard-break, [ADR-0007](0007-none-by-exception.md) None-by-exception). Those are two different options and the
> argument needs both. The removal case survives; only its authority changes. Two details for
> [#1720](https://github.com/kalonji-tools/oxitest/issues/1720): the escape hatch has exactly one consumer
> (`importer.py:499`) but the parser is generic (`_allow_comment.py:10`), so the delete must reach the parser or the
> mechanism outlives its only rule; and while the *syntax* is undocumented — learned only from the error text at
> `importer.py:521`, never from `docs/user/` — the **term is documented twice**, at
> `docs/user/how-to/use-fixtures.md:242` and `docs/user/reference/errors.md:409`, both promising "There is no
> allow-comment escape hatch" for B1. Deleting the mechanism leaves those two sentences promising the absence of
> something that no longer exists, so they join the blast radius in point 1.
>
> **Two facts [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) needs and this rule does not carry.**
> (a) `ModuleSource` **already ships** with exactly the `defining_module_path` + `anchor_package_path` fields named
> as `ConftestSource`'s replacement (`python/oxitest/_bridge/_fixture_registry.py:96`, already in the `FixtureSource`
> union at `:105`) — that successor is built, not pending. The counterweight, so this is not undersized in the other
> direction: `ConftestSource` is referenced across a dozen further `_bridge/` modules and a dozen test modules, so it
> is not a one-file delete either. (b) `FixtureProvider` has a **real, named downstream implementer**: `HostProvider`
> in `oxi-nixinfra`, which tracks oxitest `main` through an unpinned flake input, so
> retirement breaks it the day it lands rather than at a version bump. `HelperProvider` has zero implementers
> anywhere. The two protocols must therefore be sequenced separately, and only one needs a migration path.

### Reconciliation with prior ADRs

- **[ADR-0002](0002-unified-fixture-backend.md) (Unified fixture backend)** — Type-based resolution (`Fixture[T]` primary key, parameter name as qualifier) and the unified registry survive intact. Source variants collapse: `ConftestSource` retires; the new source variant carries `defining_module_path` + `anchor_package_path`. Override precedence extends naturally with the new lifetime tiers.
- **[ADR-0005](0005-immutable-by-default-interfaces.md) (Immutable-by-default) Rule 4** — Retires the `Fixtures` / `Helpers` `&mut` exceptions. Decorators write marker attributes at import time; no accumulation-during-conftest-loading phase remains. The reused type annotation name (`Fixtures` on test parameters) is a proxy accessor, not a mutable registrar — it does not re-inherit the exception. **Amendment 5 splits this entry.** The `Helpers` half is *done*: #1788 deleted the registrar, so that exception has no subject left, and nothing reuses the name — this clause originally read "the reused type annotation names (`Fixtures`, `Helpers` on test parameters) are proxy accessors". The `Fixtures` half remains forward-looking and is [#1721](https://github.com/kalonji-tools/oxitest/issues/1721)'s work; ADR-0005 Rule 4 still carries **both** entries today, so the reconciliation this bullet describes is half-performed, not performed.
- **[ADR-0006](0006-async-organizational-strategy.md) (Async organizational strategy)** — Async fixture behavior is orthogonal to declaration mechanism. `@fixture(lifetime="function")` on an `async def` behaves per ADR-0006's per-test-loop rules. Implemented in [#1733](https://github.com/kalonji-tools/oxitest/issues/1733) for the `function` and `module` tiers, with three refinements ADR-0006 did not anticipate, because it assumed fixtures are resolved *before* the test body: (a) `fx.<ns>.<name>` returns an awaitable — `await fx.pkg.conn` — since attribute access offers no earlier hook; (b) an async fixture wider than `function` lifetime promotes async test bodies onto the shared session loop, because a value cannot move between loops and a per-test loop dies before the fixture's boundary is reached; (c) teardown fires at the declared boundary, clamped so it can never be scheduled after its loop closes. Illegal cell combinations (sync test + function-scope async fixture) are rejected loud on both access paths — at arrange time for `@arrange`, at access time for the proxy. Loud-rejection DNA is *reinforced* by this ADR: B1 boundary violations and lifetime-cap violations fire at the shallowest frame that can catch them — declaration-site violations at prescan, B1 violations at access (Rule 3, as amended). The same loud rejection covers the shortcut route: a sync test reaching an async fixture via `fx.<name>` raises `AsyncFixtureAccessError` at access, before the factory runs, exactly as the qualified route does, so async-ness is a property of the fixture rather than of the access form chosen. (This list previously included strict-abort shortcut violations; the strict dial is retracted — see Amendment 3.)
- **[ADR-0008](0008-config-fail-closed-narrow-scope.md) (Config fail-closed)** — B1 boundary violation and lifetime-cap violation both fail closed. No per-callsite bypass anywhere in the new surface. This originally read "…and strict-dial-forbidden shortcut all fail closed. No per-callsite bypass anywhere in the new surface; all configurability lives on the strict dial" — with the dial retracted (Amendment 3) the new surface has **no** configurability at all, which is a stronger fail-closed position than the one claimed, not a weaker one. The *exit code* differs by when the violation is catchable: declaration-site violations abort collection with a `UsageError` exit code, while a B1 violation surfaces as a test `ERROR` and the ordinary failing-run exit code, because it is detected inside a running test and must not blank the results of every other test in the run. Giving the run a `UsageError` exit code without aborting it needs a per-test bridge-to-coordinator exit vote that does not exist yet — [#1761](https://github.com/kalonji-tools/oxitest/issues/1761).

## Amendments

### Amendment 1 — the parallelism model (2026-07-29)

Tracked by [#1746](https://github.com/kalonji-tools/oxitest/issues/1746). Amends Rules 1, 2, 4, and 7. The principle, the declaration-file convention, the B1 boundary, proxy access, plugin convergence, and the retirements all stand as accepted.

This ADR was written against a single-process mental model. Neither the document as accepted on 2026-07-28 nor the [design spec](https://github.com/kalonji-tools/oxitest/issues/1707#issuecomment-5101919212) it came from contains a single occurrence of `parallel`, `worker`, or `subprocess` — while oxitest distributes tests across worker subprocesses by default at `min_parallel_tests = 100`. Three statements did not survive contact with the scheduler:

1. **The lifetime ladder said nothing about parallelism.** Under the real scheduler a fixture session is created and torn down per task, so the widest *effective* tier was the module — narrower than every framework surveyed. Rule 2 now states, per tier, what happens when work spans processes, and `package` earns its exactly-once guarantee structurally by collapsing its subtree onto one worker.
2. **`package` was defined as "the Python package (directory with `__init__.py`)".** That made the tier unreachable in oxitest's own test suite, in which no directory holding real test modules carries an `__init__.py`. Rule 2 now defines a package as any directory; `__init__.py` keeps its role as a declaration home.
3. **Rootdir `package` and `session` were declared the same thing.** They are not. Rule 4 carries the retraction with the original argument quoted in full.

> **Point 3's replacement is itself retracted by Amendment 4.** This amendment
> said "once instances can exist per process", and Rules 2, 4, and 7 were then
> written against "`session` is one instance per worker process". Measured on
> `main`, it is one instance per **task group** — narrower again, and equal to
> `module` in any suite with no `package` declaration. The retraction of the
> equivalence survives; the reason given for it does not.

Rule 7's autouse table follows from (1) and (3): a `session` autouse fixture fires once per task group, not "once for the whole run" — as amended.

**Evidence.** A [primary-source survey of ten frameworks](https://github.com/kalonji-tools/oxitest/issues/1710#issuecomment-5119280622) found that **no surveyed framework ships a code-structural tier wider than the file that is also process-shared.** pytest is alone in offering a `package` scope, and `pytest-xdist` has no package-level `--dist` mode, so that scope degrades silently under distribution. oxitest can do better only because it owns both the static prescan and the scheduler. The seven decisions behind this amendment are recorded in the [grilling outcome](https://github.com/kalonji-tools/oxitest/issues/1710#issuecomment-5119666710).

**Implemented by** [#1745](https://github.com/kalonji-tools/oxitest/issues/1745) (worker tasks carry N modules; wire protocol v5) and [#1710](https://github.com/kalonji-tools/oxitest/issues/1710) (`lifetime="package"`), both merged 2026-07-29 — ahead of this record, which is why the amendment describes shipped behaviour rather than proposing it.

### Amendment 2 — B1 enforcement timing and mechanism (2026-07-30)

Tracked by [#1713](https://github.com/kalonji-tools/oxitest/issues/1713). Amends Rules 3 and 5 and the ADR-0008 reconciliation. The B1 boundary itself — anchor package plus descendants, no escape hatch, no `strict` softening — stands exactly as accepted; what changes is *when* it is checked and *how* the two catalogs are realised.

Amendment 1 found that this ADR was written against a single-process model. This one finds the parallel gap: it was also written against the prototype, a 300-line Python-only simulation with no AST prescan, no worker subprocesses, and one resolution route. Three statements did not survive contact with the real system:

1. **B1 violations were to be collection-time errors, enforced by "hierarchical prescan at discovery".** Prescan extracts *declarations*, not *usages* — nothing at collection time knows a test intends to reach `fx.admin.conn`. Rule 3 now places the gate at access time, and notes that a static gate could never have been the only gate, because dynamic access defeats it. Collection-time enforcement becomes an optimisation, [#1758](https://github.com/kalonji-tools/oxitest/issues/1758).
2. **The proxies were to "hold two references" to two catalogs.** The constraint is that both the filtered and the full view be reachable; object count was an artefact of how the prototype was built. Rule 5 now states the constraint and leaves the mechanism to the implementation, which asks one registry two questions.
3. **B1 violations were to "fail closed with `UsageError` exit codes".** A violation detected inside a running test can either abort the run — blanking every other test's result over one bad attribute access — or report as a test `ERROR`. The reconciliation now distinguishes declaration-site violations (abort, `UsageError`) from access-time ones (test `ERROR`), with the run-level usage-error vote left to [#1761](https://github.com/kalonji-tools/oxitest/issues/1761).

Two further points Rule 3 and Rule 5 did not state at all, both surfaced by grilling rather than by implementation:

- **Whose boundary governs a fixture's own dependencies.** The Consequences enumeration (item 7) called for enforcement "at declaration time (for cross-boundary dependency chains)" without saying what that meant. Rule 3 now says it: a fixture's dependencies are checked against the fixture's anchor, not against the location of whichever test triggered resolution. Without that, a package-lifetime fixture can launder a narrower-anchored value through a descendant test's position.
- **The guarantee is scoped to directory-anchored fixtures.** Inline declarations are registered on module import, so their presence in the "full" catalog varies with worker assignment and import order. Rule 5 now scopes the `BoundaryError` guarantee to declaration homes rather than let a diagnostic depend on scheduling.

The decisions behind this amendment are recorded in the [grilling outcome](https://github.com/kalonji-tools/oxitest/issues/1713#issuecomment-5124157268).

### Amendment 3 — the shortcut strict dial is retracted (2026-07-30)

Tracked by [#1714](https://github.com/kalonji-tools/oxitest/issues/1714). Amends Rule 5, the ADR-0006 and ADR-0008 reconciliations, and Consequences item 9. Shortcut access itself stands as accepted, along with the naming-clash rule, which this slice makes live. What is retracted is the dial that was to police it.

Amendment 1 found this ADR written against a single-process model; Amendment 2 found it also written against the prototype. This one finds it written against a **configuration surface that does not exist**. Rule 5's table specified three positions:

| Rule 5 claimed | `main` |
|---|---|
| `strict = "off"` | `StrictMode::Off` exists |
| `strict = "warn"`, **the default** | there is no `warn` position. The dial is `off \| enforce \| abort`, and the default is *absent*, which is silent |
| `strict = "abort"` → collection error `fixture-shortcut-in-strict` | the variant exists; the violation is invisible to collection |
| `NOTICE` severity under warn | `enforce` maps to `Warning` and never to `Error` — an invariant [#1613](https://github.com/kalonji-tools/oxitest/issues/1613) established deliberately |

A fourth problem sat underneath all three: the strict *value* never crosses into Python. Only a `collect_violations: bool` does, so no proxy can consult the dial at access time even in principle.

Three ways out were weighed — map `"warn"` onto the existing `enforce`, add a real `Warn` variant, or drop the dial. Adding a variant was rejected as a global config change rippling through every existing strict rule, colliding with #1613's invariant that already makes `enforce` *be* warn. Mapping onto `enforce` was accepted, then superseded once the costs were measured against each other:

- **Shortcut resolution was already built.** `FixtureRegistry.get_visible` (bare-name, B1-filtered), `FixtureSession.get_fixture_by_name`, nearest-visible-wins with its tie-break, cycle detection, and scope caching all shipped with slices 1 and 6 and are shared with `Fixture[T]` injection. The proxy branch is a handful of lines at an insertion point Amendment 2's slice deliberately left open.
- **The dial was the entire remaining cost**, none of it shared: plumbing a value across both the PyO3 and worker-LDJSON paths, an access-time diagnostic, and an `abort` position that cannot abort — a violation found inside a running test can only report as a test `ERROR`, so `abort` would have needed the run-level usage-error vote that [#1761](https://github.com/kalonji-tools/oxitest/issues/1761) still does not provide.

So the dial cost more than the feature it governed, and it governed a habit no user has yet complained about. The industry precedent Rule 5 cited — Clippy's `wildcard_imports` in `pedantic`, Checkstyle's `AvoidStarImport`, the C++ Core Guidelines on unqualified names — argues that scope-narrowing shortcuts should be *legal by default with opt-in suppression*. oxitest's dial has no position that expresses "legal, but noticed", and manufacturing one is a config change, not a slice. The precedent's first half is honoured: the shortcut is legal. The second half is deferred until someone asks for it, at which point a `NOTICE` can be added without breaking any existing suite.

The enforcement-point question the milestone flagged as this slice's tail risk dissolves with the dial. It was: a collection-time strict error cannot be implemented, because prescan extracts declarations rather than usages and `is_fixture_annotation` does not even recognise a bare `fx: Fixtures` parameter. With no strict error to place, there is nothing to enforce early. Worth recording for whoever revisits this: at access time the distinction is *structurally exact* — `fx.tx` reaches `FixturesProxy.__getattr__` and `fx.api.tx` reaches `NamespaceProxy.__getattr__`, two different classes, with no inference required and dynamic `getattr(fx, "tx")` caught identically. Any future static gate would be the approximation, not the fallback.

**Drift is now a pattern, not an incident.** Three amendments in three days, each from the same root cause: normative claims about oxitest that were never checked against oxitest, each discovered at slice-pickup time after the cost of a grilling. The remaining slices rest on rules that have had no such check. [#1769](https://github.com/kalonji-tools/oxitest/issues/1769) sweeps the unshipped rules once, before slice 8, rather than paying that cost eight more times. The ADR's *principles* are not what drifts — B1, the declaration-file convention, the lifetime ladder, and plugin convergence have survived all three amendments untouched — which is why this is a third amendment and not a rewrite.

The decisions behind this amendment are recorded in the [grilling outcome](https://github.com/kalonji-tools/oxitest/issues/1714#issuecomment-5131149592).

### Amendment 4 — the unshipped rules, checked (2026-07-30)

Tracked by [#1769](https://github.com/kalonji-tools/oxitest/issues/1769). Amends Rules 2, 4, 6, 7, 8, the Consequences section, and Amendment 1. The principle, the declaration-file convention, the lifetime ladder's *shape*, B1, proxy access, and plugin convergence all stand as accepted — as they did through the first three amendments.

**This is the first amendment that is not reactive.** Amendments 1, 2, and 3 were each triggered at slice-pickup time, after the cost of a grilling: this ADR was written against a single-process model, then against a throwaway prototype, then against a configuration surface that does not exist. Three amendments in three days from one root cause made drift a pattern rather than an incident. This one sweeps every unshipped normative claim at once, before slice 8, instead of paying that cost eight more times.

Each rule now carries a `**Status:**` line, and statements specifying future surface rather than describing `main` carry an inline *Not yet built* marker recording the evidence. **That is the durable half of this amendment**: the previous three all drifted because a reader could not tell a description of oxitest from a specification of it.

#### 1. `session` is once per *task group* — and this retracts Amendment 1

The sweep was scoped to exclude Rules 1–4, on the premise that Amendments 1–3 had already checked them. **That premise failed, because Amendment 1 is the source of the error.** Its point 3 retracted "rootdir `package` and `session` are the same thing" and replaced it with "`session` is one instance per worker process" — trading one wrong description for another. Rules 2, 4, and 7 were then written against the replacement, and slices 3 and 4 were built against Rules 2 and 4.

A worker pops task groups in a loop until the shared queue drains (`src/worker_session.rs:271`–`272`) and every task builds a fresh fixture session (`python/oxitest/_bridge/worker.py:265`, inside `run(task)` at `:238`, called per stdin line at `:366`). Only `package` declarations merge modules into a group (`src/filter.rs:270`–`292`); a `session` declaration triggers no co-location (`src/pipeline/collection.rs:274` filters on `package` alone). Measured: eight single-test modules over two workers built a `lifetime="session"` fixture **eight** times across two PIDs; adding one `lifetime="package"` declaration brought it to **one**.

So `session` is one instance per task group, which is **one module unless a `package` declaration merges the subtree** — the exact failure Amendment 1's point 1 believed [#1745](https://github.com/kalonji-tools/oxitest/issues/1745) had fixed. #1745 widened a *task*; it did not make sessions per-process. It follows that `session` is the only tier whose instance count is set by another tier's declarations rather than by its own boundary, and that this ADR should not have borrowed Playwright's and Vitest's `scope: 'worker'` as precedent for it.

The phrase was wrong in six places, two of them shipped surface: Rule 2's tier table and parallel-execution bullets, Rule 4's reasoning, Rule 7's table row, Amendment 1's point 3, `_fixture_decorator.py`'s `lifetime` docstring, and `docs/user/how-to/use-fixtures.md`. All are corrected. **This makes the conformance sweep a code-and-docs change rather than the docs-only one it was specified as.**

**This corrects the description, not the semantics.** Whether `session` *should* be per-process — and what the scheduler would have to change to make it so — is a design question deserving its own grilling. It is not settled by a conformance sweep, and this amendment does not imply today's behaviour is intended. The legacy `shared=True` surface repeats the same claim in its own vocabulary and is handled separately at [#1778](https://github.com/kalonji-tools/oxitest/issues/1778); its numbers cannot be carried across, because it has an auto-arrange mechanism `lifetime=` does not.

#### 2. Rule 6 — "session-wide" and a public hook typed on a private class

Plugin fixture *visibility* genuinely is run-wide: plugin fixtures are ambient and registered into every fixture session. But *lifetime* `session` is per task group, so "session-wide" cannot mean run-wide instantiation under any reading. **The phrase must be read as "ambient in every fixture session"** — deliberately *not* a narrowing of the visibility claim, which would have replaced one drift with another. Rule 6's own wording is left as accepted and carries the correction in its inline marker: this sweep marks unshipped rules rather than rewriting prose that [#1717](https://github.com/kalonji-tools/oxitest/issues/1717) will replace outright.

Separately, `register_fixtures(registry: FixtureRegistry) -> None` is advertised as plugin-facing while `FixtureRegistry` is exported from neither `oxitest` nor `oxitest.plugin`, and exposing it is a standing deferral — the Result-variant factories shipped on `oxitest.plugin` in v2.4.0 while `PluginRegistry` and `FixtureSession` were held back pending evidence from a real plugin. The rule's claim that migration is "mechanical — only the entry point changes" does not survive either: `FixtureProvider` has no `register_fixtures` method at all, and its actual shape is a type-matched per-fixture provider object rather than a registry-mutating callback. **The API shape is recorded and deferred to [#1773](https://github.com/kalonji-tools/oxitest/issues/1773)**, for slice 10's grilling with plugin context this sweep does not have.

#### 3. Rule 7 — "same shape as today" is retracted, and autouse has a latent trap

Today's autouse is the `Fixtures()` registrar kwarg that Rule 8 retires, and its shape is **run-wide ambient**, not B1-scoped — the only two source variants that can carry `autouse` have no anchor field, so visibility falls through to "everything unanchored is ambient". Rule 7's shape is strictly *narrower* than today's, not the same. What does carry over, so this is not over-corrected: the fire-without-request mechanic is unchanged.

Underneath that: `get_autouse()` iterates an unfiltered by-name index and takes no `module_path`, while resolution applies B1 and **raises** on a filtered-out candidate. This is inert today only because every autouse-capable source is anchorless. The moment `autouse=True` reaches an anchored source, every test outside the fixture's boundary errors with `FixtureNotFoundError` naming a fixture it never requested and cannot see — the **inverse** of a boundary leak, a spurious hard error rather than over-permissive access. Filed as [#1774](https://github.com/kalonji-tools/oxitest/issues/1774), blocking [#1716](https://github.com/kalonji-tools/oxitest/issues/1716). This is the third unfiltered index after the `_by_type` hazard in [#1768](https://github.com/kalonji-tools/oxitest/issues/1768). **The recurring shape — a convenience index that predates B1 and does not know about it — is the finding worth carrying forward, more than any single instance of it.**

#### 4. Rule 8 — the retirement list conflates internal deletes with semver-protected surface

Only `ConftestSource` is internal; its retirement really is a delete. Everything else is documented user-facing surface, and two entries are listed on `stability.md` as semver-protected. "The old instance usage no longer exists" is false — both registries are live and exported — and the same present-tense-for-future-state defect runs through the rule. The helper *read* surface is missing from the list entirely, though Rule 5 replaces it. And the ADR-0008 citation does not hold: that ADR scopes itself narrowly to deserialization errors inside `[tool.oxitest]`, so a source-line allow-comment cannot violate it; the removal case survives on ADR-0008's rejection of `--allow-config-drift` and the loud-rejection DNA it invokes, which are two different options and the argument needs both.

The full scope for [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), including the two facts the rule did not carry — `ModuleSource` already ships the named replacement fields, and `FixtureProvider` has a real downstream implementer while `HelperProvider` has none — is in Rule 8's inline marker rather than repeated here.

#### 5. Rule 5 / the helpers surface — three live spellings where the ADR reasoned about one

> **Superseded — Amendment 5 (#1795, #1781).** Every spelling in the table below
> is gone. #1787 migrated oxitest's own suite off `helpers.common.<fn>()` to plain
> `from tests import helpers` imports and rewrote the CLAUDE.md rule to forbid the
> proxy; #1788 then deleted the surface itself — `_read_helpers.py`, the `Helpers`
> registrar, and the `oxitest.helper` sentinel. The `oxitest.helpers` row is
> therefore false in **both** clauses, not the one #1795 filed against: it is not
> Live, and CLAUDE.md mandates the opposite of what the row says.
>
> **Kept as a record, not corrected in place.** This point existed to tell slice 8
> which incumbents it had to dispose of before introducing `hlp`. Slice 8 is
> `wontfix` and there is no audience left for the verdicts — but the table is
> *why* slice 8 was expensive, and so part of the evidence for retiring the
> concept. Deleting it would leave the conclusion without its reasoning.

Rule 5 introduces `hlp` as *the* helper access proxy, and Rule 8 says the name `Helpers` is free to reuse. Neither statement accounts for what is already there:

| Spelling | State |
|---|---|
| `oxitest.helpers`, as `helpers.common.<fn>()` | **Live**, backed by `_HelpersProxy` (`_read_helpers.py:34`) — and `CLAUDE.md` mandates it as the way oxitest's own suite reaches shared utilities |
| `helpers.<name>()` on a `Helpers()` instance | **Live** — the registrar Rule 8 retires |
| `fx.helpers` | **Phantom** — documented at `_helpers.py:24`, and that docstring is its only occurrence in the repository; `proxy_ns.py` has zero helper references |
| `hlp` | **Proposed** by Rule 5 |

A fifth adjacent name, `oxitest.helper`, is occupied by a sentinel that raises and steers users into the very `Helpers()`-in-`conftest.py` surface Rule 8 retires — so it must be *converted* into the real decorator rather than deleted. Slice 8 must decide the fate of the incumbents before introducing `hlp` as one more.

**One pre-seeded finding is retracted here.** The sweep's own worklist claimed Rule 5's `HelpersProxy` name was already taken by `_HelpersProxy`. It is not: `_FixturesProxy` and `FixturesProxy` already coexist on `main` as private-read-accessor beside public-injection-proxy, and the ADR names `HelpersProxy` without the underscore — so slice 8 reproduces a working pattern rather than colliding. What survives is a readability hazard, not drift: two classes one underscore apart with unrelated responsibilities.

#### 6. Consequences — drifted from its own amendments, and from itself

Two bullets still stated claims Amendment 2 retracted: that proxies "carry" two catalog objects, and that B1 violations fire at prescan. The editor-squiggle promise rests on collection-time usage extraction, which [#1758](https://github.com/kalonji-tools/oxitest/issues/1758) records as unbuilt. The Python-import fallback bullet turned out to be **split** — it holds for tests and is false for declaration files, whose path chose loud rejection instead — and that is a real disagreement with the bullet rather than an oversight, so it is routed to [#1727](https://github.com/kalonji-tools/oxitest/issues/1727) rather than resolved here. Three of the five "deferred design questions" were no longer open, and the one genuinely open item with no filed home anywhere is now [#1779](https://github.com/kalonji-tools/oxitest/issues/1779). The section carried no breaking-change consequence at all despite retiring semver-protected surface; one is added. The "throwaway prototype" bullet ordered the deletion of a directory that **was never committed** — `git log --all -- 'scripts/prototype*'` is empty — so the design's stated validation basis cannot be consulted by anyone; that bullet is struck rather than actioned.

And the pointer to the follow-on enumeration miscounted it twice: the Decision said "24 tickets in 5 phases", Consequences said "23 tickets in 5 phases", and the document contains **23 tickets in 7 phases**. That is the cheapest conformance failure available — the only claim in this ADR checkable without reading a line of oxitest — and it went unchecked through three amendments.

#### What this amendment does not do

It adds no principle and reverses no decision except Amendment 1's point 3, which it replaces with a measured one. It settles neither what `session` should mean, nor the plugin registration API shape ([#1773](https://github.com/kalonji-tools/oxitest/issues/1773)), nor the fallback-versus-loud-rejection tension ([#1727](https://github.com/kalonji-tools/oxitest/issues/1727)). It fixes no behavioural bug it found: [#1774](https://github.com/kalonji-tools/oxitest/issues/1774) and [#1778](https://github.com/kalonji-tools/oxitest/issues/1778) are filed, not fixed.

**The sweep also miscounted itself.** Six `Holds` citations were caught stale and corrected during execution, three of them in its own pre-seeded findings; one pre-seeded finding was retracted outright (point 5); and one was corrected on its central claim before landing. A conformance sweep shipping a stale citation would reproduce the exact failure it exists to fix, so the tally belongs in the record rather than in a private log.

The full statement-by-statement verdict record — every in-scope claim with `Holds` plus a `file:line` citation, `Drifted`, or `Forward-looking` plus its slice issue — is posted on [PR #1772](https://github.com/kalonji-tools/oxitest/pull/1772) rather than tracked in-repo, per the project's convention that surveys live on the issue tracker.

### Amendment 5 — the helper column is retracted (2026-08-02)

Tracked by [#1781](https://github.com/kalonji-tools/oxitest/issues/1781) and [#1795](https://github.com/kalonji-tools/oxitest/issues/1795). Amends Rules 1, 4, 5, 6 and 8, Amendment 4's point 5, and the Consequences section. The principle, the declaration-file convention **for fixtures**, the lifetime ladder, the B1 boundary, shortcut access, and plugin convergence all stand as accepted — as they have through four amendments.

**Helpers are not a peer concept to fixtures, and this ADR should never have given them a column.** [#1700](https://github.com/kalonji-tools/oxitest/issues/1700) resolved that the helper system is retired rather than rebuilt; slice 8 ([#1715](https://github.com/kalonji-tools/oxitest/issues/1715)) is closed `wontfix`. `@oxi.helper`, `__helpers__.py`, the `hlp` proxy, `HelpersProxy`, and the helper half of every rule below will never be built.

The argument for the retraction is this ADR's own opening line: *"visibility is Python's job; lifecycle is the framework's job"* (see the Decision). **A helper has no lifecycle.** It is a function you call, and Python already has a complete answer for where such a function lives and how a test reaches it — `import`. The framework's fixture responsibility is deciding when a value is built and torn down; there is no such decision to make for a helper, so there was never a framework job here to design a surface for. Rule 1 gave helpers a declaration file, Rule 5 gave them a proxy, and Rules 6 and 8 gave them a plugin protocol and a retirement, all for a concept the thesis excludes. The retraction is not a reversal of the ADR's argument — it is the first place that argument was not applied to its own contents.

**What this amendment does not do.** It does not re-litigate #1700; the decision is recorded here, not made here. It reverses nothing else, and adds no principle.

#### The incumbent surface is gone, not pending

Both tracking issues predate the deletion. [#1788](https://github.com/kalonji-tools/oxitest/issues/1788) merged 2026-08-01 and removed `Helpers`, `oxitest.helpers`, `HelperRegistry`, `HelperProvider`, the `oxitest.helper` sentinel and `_read_helpers.py`, after [#1787](https://github.com/kalonji-tools/oxitest/issues/1787) migrated oxitest's own ~790 call sites to plain `from tests import helpers` imports. On `main`, `grep -rn 'HelperProvider\|_HelpersProxy\|oxitest\.helper' python/ src/` returns nothing.

That makes every inline *Not yet built — #1715* marker in this document wrong in **both** directions at once: wrong about the future, because nothing is coming, and wrong about the present, because the incumbent it implicitly contrasts against no longer exists. All are replaced below.

It also settles the two live questions the ADR left for slice 8. `oxitest.helper` was to be *converted* into the real decorator rather than deleted, because its `AttributeError` steered users into retired surface (Rule 8's marker, point 3); it was deleted, and the steering problem went with it. And Rule 8's note that the name `Helpers` is free for Rule 5 to reuse is now moot from the other end: nothing reuses it, because `hlp` is withdrawn.

#### This is a different failure from Amendments 1–4

Amendments 1, 2 and 3 each corrected claims about oxitest that had never been checked against oxitest, and Amendment 4 swept the rest at once. Every one of them was **drift** — the document and the system disagreeing about a fact.

This one is not. No claim about helpers was falsified by measurement; the concept was reasoned out of existence. A reader counting five amendments should be able to tell the two kinds apart, because the remedies differ: drift is cured by checking claims against `main`, which Amendment 4 has now institutionalised in the `**Status:**` lines and inline markers. A peer concept that never earned its place is not caught by any amount of checking — it needed [#1700](https://github.com/kalonji-tools/oxitest/issues/1700)'s question, *"what is this for?"*, asked of the design rather than of the code.

The one structural lesson worth carrying: the helper column entered this ADR by symmetry with fixtures — same file convention, same proxy shape, same plugin protocol — and symmetry is not a justification. Nothing in the Decision ever argued that helpers *needed* framework support; the rules simply gave them the treatment fixtures got.

## Consequences

- **New declaration surface for users.** All fixture declarations move to module-level `@oxi.fixture(lifetime=...)` in one of three reserved file kinds — originally "fixture and helper declarations … `@oxi.helper` … four reserved file kinds", per Amendment 5. Existing users need a migration path (see follow-on impl, Documentation phase). Green-field users will see only the new surface **once [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) retires the legacy one** — not before. Amendment 4 corrects the original present tense: both surfaces are live today, and the legacy registrar is still what a newcomer meets first.
- **Both catalog views reachable from every proxy.** `FixturesProxy` (originally "and `HelpersProxy`", retracted by Amendment 5) must be able to consult the **B1-filtered** view for resolution decisions and the **full** view for diagnostic attribution. Losing the second view produces misleading diagnostics (`FixtureNotFoundError` in place of `BoundaryError`). This bullet originally read "**Two catalogs on every proxy** … carry both the B1-filtered catalog and the full catalog"; Amendment 2 retracted that object-count reading, and Rule 5 now states the constraint as reachability. The shipped implementation asks the single `FixtureRegistry` two questions rather than materialising a per-test catalog — `has_visible_anchor` (`python/oxitest/_bridge/_fixture_registry.py:466`, filtered) beside `has_namespace` (`:462`, full), which is exactly the pair the `BoundaryError`-vs-`FixtureNotFoundError` decision consults (`python/oxitest/_bridge/_fixture_session.py:809`).
- **Prescan-time errors replace collection-time errors for lifetime-cap violations.** Lifetime-cap violations fire at prescan, before any Python import and before any fixture instantiation — the inline cap at `src/pipeline/collection.rs:188` and the rootdir-`session` rule at `:288`. **B1 boundary violations do not.** This bullet originally claimed both fired at prescan; Amendment 2 moved B1 enforcement to access time, because prescan extracts *declarations* and never *usages*, so nothing at collection time knows a test intends to reach `fx.admin.conn`. The "better tooling (e.g., editor squiggles on illegal declarations)" this bullet promises therefore depends on collection-time usage extraction, which is unbuilt and tracked by [#1758](https://github.com/kalonji-tools/oxitest/issues/1758) — it is not a consequence already purchased by the lifetime-cap gate.
- **Fallback to Python-import discovery survives — for *tests*, not for *fixtures*.** If AST prescan cannot parse a test file it emits `PrescanResult::Unavailable` and the file falls through to Python-import-based discovery (`src/pipeline/transitions/files_collected.rs:114`–`:125`), the same three-tier collection model already used for tests. **Declaration files have no such fallback**: an unparsable one raises `CollectError::PyError` and a parseable one carrying unrecognised decorators is rejected outright (`src/pipeline/collection.rs:329`–`:352`), and the bullet's original illustration — `if flag: dec = fixture; @dec def x(): ...` — parses fine and so takes exactly that rejecting path. Amendment 4 records this as a **live disagreement rather than an oversight**: the fixture path chose the loud rejection [ADR-0006](0006-async-organizational-strategy.md) mandates and Rules 1 and 3 invoke by name, while this bullet promises a silent fallback, and Considered Option 2 rejected an import hook partly *because* it "breaks under the standard three-tier collection fallback". Which principle wins is a triage question, routed to [#1727](https://github.com/kalonji-tools/oxitest/issues/1727) and deliberately not settled here.
- **Deferred design questions.** Five were listed; Amendment 4 finds three of them no longer open and gives all five a home.
    - IDE / type-checker stub generation for the `fx` proxy (auto-generated `.pyi` vs. dynamic-only vs. user-declared Protocol overlay) — **still open**, and until Amendment 4 the only item in this ADR with no filed home at all. Now [#1779](https://github.com/kalonji-tools/oxitest/issues/1779). Originally "the `fx` / `hlp` proxies"; the `hlp` half is retracted (Amendment 5), which narrows #1779's scope by half.
    - `FixtureRegistry.add()` runtime API details (ordering guarantees, duplicate-name handling) — **has a home**: [#1718](https://github.com/kalonji-tools/oxitest/issues/1718), with the prior API-shape decision at [#1773](https://github.com/kalonji-tools/oxitest/issues/1773).
    - The migration story, incremental coexistence vs. hard cutover — **answered by what shipped.** Slices 1–7 ran the new declaration path alongside the legacy `Fixtures()` path; that *is* incremental coexistence, with [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) as the cutover.
    - `FixtureRef[T]` internals under the new source variant — **answered by construction, pending coverage.** The `FixtureRef` path resolves by name and namespace through the same B1-filtered route as everything else (`executor.py:225`–`:238` → `_fixture_session.py:881`–`:887` → `_fixture_instantiator.py:374`), so it is source-agnostic and needs no `ModuleSource`-specific work. No test exercises `FixtureRef[T]` against an `@oxi.fixture` declaration, so it is recorded as answered-pending-coverage rather than closed.
    - `oxitest inspect` updates for the new source variant and the autouse-firing view — **has a home**: [#1722](https://github.com/kalonji-tools/oxitest/issues/1722).
- **Completing this ADR requires a major version.** Added by Amendment 4; the original Consequences carried no breaking-change consequence at all. `docs/user/reference/stability.md:11` lists `Fixtures` under "Stable (semver-protected)" — surface that "will not change in backward-incompatible ways without a major version bump" (`:7`) — and `:16` extends the same promise to "`Plugin` dataclass and protocol interfaces". Rule 8 retires both. `Fixtures` is not even removed cleanly: Rule 5 reuses the name as a proxy type annotation, so a user's `fixtures = oxitest.Fixtures()` does not fail with a clean `AttributeError` but becomes a call on a name that now means something else. And `FixtureProvider` has a named downstream implementer — `HostProvider` in `oxi-nixinfra`, tracking oxitest `main` through an unpinned flake input — so retirement breaks it the day it lands rather than at a version bump. A second, separate gap for [#1721](https://github.com/kalonji-tools/oxitest/issues/1721): **the replacement surface carries no stability promise either.** `oxitest.fixture` appears on none of `stability.md`'s three tiers, while the legacy registrar it replaces is semver-protected. The docs rewrite must tier the new surface, not merely untier the old one.
- ~~**Prototype is throwaway.** `scripts/prototype_fixture_redesign/` is a 300-line Python-only simulation with six interactive scenarios. Delete it once the follow-on impl issues are filed, or fold pieces into test fixtures for the real implementation.~~ **Struck by Amendment 4.** That directory was never committed — `git log --all -- 'scripts/prototype*'` is empty — so there is nothing to delete and no reader can consult the design's stated validation basis. The prototype was an uncommitted local exercise; this ADR, and Amendments 2 and 3 which both reason against it, should be read accordingly.
- **Follow-on impl work (23 items in 7 phases) — a record of the plan as accepted, not a live work list.** This ADR **listed** the follow-on work; it did **not** file the tickets. Filing happened post-merge per the standard project pipeline (grill → spec → PR), as slices 1–15 ([#1708](https://github.com/kalonji-tools/oxitest/issues/1708)–[#1722](https://github.com/kalonji-tools/oxitest/issues/1722)) plus [#1727](https://github.com/kalonji-tools/oxitest/issues/1727). **Per-item status is deliberately not maintained below** — those tickets are authoritative for what is shipped and what is left, and this enumeration is left as accepted, annotated only where a later amendment names an item. **Amendment 5 voids every helper half below.** Eight items name helper surface outright — **1, 3, 4, 8, 14, 17, 18, 23** (`@oxi.helper`, `__helpers__.py`, `HelpersProxy`, `Helpers()`, `HelperProvider`, the `Helpers` `&mut` entry, the `hlp` proxy) — and two more, **2 and 10**, carried a helper half under Amendment 4's half-shipped reading without naming it. One strike, no per-item re-status: restating status here is what rotted the first time. Enumeration:

  **Foundation (v0 — non-shipping):**
  1. `PrescanDeclaration` in `src/prescan.rs` — extend `PrescanItem` with fixture/helper declaration extraction.
  2. New source variant in `_fixture_registry.py` carrying `defining_module_path` + `anchor_package_path`; retire `ConftestSource`.
  3. Decorator implementations: `@oxi.fixture(lifetime=...)`, `@oxi.helper` — pure markers, no import-time side effects.
  4. File-convention scanning: `__fixtures__.py`, `__helpers__.py`, `__init__.py` at package level; inline `test_*.py`.

  **Discovery (v0):**
  5. Hierarchical prescan walk driven by the tests being collected.
  6. Lifetime-cap enforcement at prescan.
  7. B1 boundary enforcement at proxy resolution + at declaration time (for cross-boundary dependency chains).

  **Access (v0):**
  8. `FixturesProxy` + `HelpersProxy` synthesis, with both filtered and full catalogs.
  9. Shortcut behavior. (Enumerated here as "+ strict-dial gating"; the dial is retracted — see Amendment 3.)
  10. Namespace derivation + `namespace=` override.

  **Plugins (v0):**
  11. Converged decorator path for static plugin fixtures.
  12. `register_fixtures` runtime hook + `FixtureRegistry.add()` API.
  13. `pyproject.toml` plugin namespace declaration.

  **Retirement (v1):**
  14. Remove `Fixtures()` and `Helpers()` classes (instance-registry meaning only; access-proxy type annotation reuses the name).
  15. Remove `conftest.py` special-case discovery.
  16. Remove `registrar-in-test-module` violation kind and its allow-comment path.
  17. Update [ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4 (drop the `Fixtures` / `Helpers` `&mut` entries).
  18. Retire `FixtureProvider` and `HelperProvider` plugin protocols.

  **Documentation (v1):**
  19. Rewrite `docs/user/how-to/use-fixtures.md` around the new model.
  20. Rewrite `docs/user/how-to/migrate-from-pytest.md` for the new discovery convention.
  21. Migration guide for existing oxitest users (bridging from `Fixtures()` instances to `@oxi.fixture`).

  **Tooling (v1):**
  22. `oxitest inspect` — autouse-firing view per test.
  23. IDE / type-checker stub generation for `fx` / `hlp` proxies (may spawn its own spec).

- **Wayfinder map [#1703](https://github.com/kalonji-tools/oxitest/issues/1703) reaches its destination on merge of this ADR.** The map's remaining work was tracked by [#1707](https://github.com/kalonji-tools/oxitest/issues/1707), whose task was drafting this document. Once merged, the map closes; the follow-on impl tickets above are filed as fresh project work, not resumed map tickets. *(Held: both issues are closed, and #1708–#1722 were filed as fresh project work.)*

> **Consequences audit — the evidence behind the corrections above ([#1769](https://github.com/kalonji-tools/oxitest/issues/1769)).**
>
> **All findings below are now applied or routed** — Amendment 4 summarises them; this block is kept as the audit record, since the evidence is what makes each correction checkable rather than merely asserted.
>
> **Applied inline above:** the self-count (23 items in 7 phases — the Decision said "24 tickets in 5 phases" and this section said "23 tickets in 5 phases", and both were wrong); the two-catalogs bullet, which still asserted the object-count reading Amendment 2 retracted; the prescan-time-errors bullet, which still placed B1 enforcement at prescan after Amendment 2 moved it to access time; the green-field bullet, now conditional on #1720; the Python-import fallback bullet, now split with the tension routed to #1727; the "deferred design questions" bullet, now with a home per entry; the struck prototype bullet; and the new major-version bullet.
>
> **The six findings, with the evidence for each.**
>
> 1. **The enumeration is a historical record, and the lead-in now says so.** It read as a live work list — "This ADR **lists** the follow-on work; it does **not** file the tickets" — while functioning as a record: all 23 items were filed post-merge and seven slices have merged. Items **1, 5, 6, 7, 9** are shipped outright; items **2, 3, 4, 8, 10** are half-shipped, in every case with the fixtures half shipped and the helpers half open on [#1715](https://github.com/kalonji-tools/oxitest/issues/1715); items **11–18, 20–22** are unstarted; item **19** (`use-fixtures.md`) is substantially done already, ahead of its own "Documentation (v1)" phase, with the legacy registrar demoted to a marked section at `docs/user/how-to/use-fixtures.md:361`. Two structural notes for Amendment 4: item **17** (ADR-0005 Rule 4) sits in the "Retirement" phase here but was filed into the docs slice [#1721](https://github.com/kalonji-tools/oxitest/issues/1721), so the phase grouping does not match the filed shape; and **two items have no filed home at all** — the `namespace=` decorator override in item **10** (unbuilt for fixtures *and* helpers, as Rule 5's Status line records, and not covered by #1715) and item **23** (IDE / type-checker stub generation, which the "Deferred design questions" bullet also lists). Amendment 4 should file or explicitly defer those two, and should **not** restate per-item status in this document — that is what rotted the first time.
>
> 2. **The "Deferred design questions" bullet is stale in three of its five entries.** `FixtureRegistry.add()` runtime API details now belong to [#1718](https://github.com/kalonji-tools/oxitest/issues/1718), with the prior API-shape decision on [#1773](https://github.com/kalonji-tools/oxitest/issues/1773). The `oxitest inspect` updates belong to [#1722](https://github.com/kalonji-tools/oxitest/issues/1722). The migration story is **answered by what shipped**: slices 1–7 ran the new declaration path alongside the legacy `Fixtures()` path, which is incremental coexistence, with [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) as the cutover — so the question should be closed out, not carried. Of the remaining two, `FixtureRef[T]` internals appear **answered by construction**: the FixtureRef path resolves by name and namespace through the same B1-filtered route as everything else (`python/oxitest/_bridge/executor.py:225`–`:238` → `_fixture_session.py:881`–`:887` → `_fixture_instantiator.py:374`), so it is source-agnostic and needs no `ModuleSource`-specific work — but no test exercises `FixtureRef[T]` against an `@oxi.fixture` declaration, so Amendment 4 should record it as answered-pending-coverage rather than resolved. Stub generation stays genuinely open and unfiled (point 1).
>
> 3. **The Python-import fallback bullet is Split, and the fixtures half is wrong — as is the example it uses for both.** The *tests* half **holds**: `PrescanResult::Unavailable` pushes an empty `PrescanModule` and the file falls through to import-based discovery (`src/pipeline/transitions/files_collected.rs:114`–`:125`). The *fixtures* half has **no fallback at all**. An unparsable declaration file raises `CollectError::PyError` — "fixtures in this file will not be registered" (`src/pipeline/collection.rs:329`–`:337`) — and a parseable file carrying unrecognised decorators raises "has @-decorated functions but no recognized `@oxi.fixture` declarations" (`:339`–`:352`). The bullet's own illustration, `if flag: dec = fixture; @dec def x(): ...`, **parses fine** and therefore takes that second path and errors: `prescan_fixture_module` returns `Unavailable` only on a read or parse failure (`src/prescan.rs:788`–`:792`, `:800`–`:803`), never for dynamic decoration. Two precision points while correcting it: the fixture-side enum is `PrescanFixtureResult`, not the `PrescanResult` the bullet names, and [#1727](https://github.com/kalonji-tools/oxitest/issues/1727) cites this as "Consequence #4" meaning the fourth *bullet*, not enumeration item 4 (which is file-convention scanning).
>
>    **Amendment 4 must not simply restate this to match `main`.** The two paths encode a real disagreement rather than an oversight: the fixture path chose **loud rejection**, which [ADR-0006](0006-async-organizational-strategy.md) mandates and Rules 1 and 3 invoke by name, while this bullet promises a **silent fallback**, and Considered Option 2 rejected an import hook partly *because* it "breaks under the standard three-tier collection fallback". #1727 exists to build the silent fallback and is still `needs-triage`. Which principle wins is a triage question, so record the tension and route it to #1727 — do not resolve it here.
>
> 4. **The prototype this ADR rests on was never committed.** The opening paragraph cites the design as "validated with a runnable prototype under `scripts/prototype_fixture_redesign/`", the "Prototype is throwaway" bullet orders that directory deleted, and Amendments 2 and 3 both reason against "the prototype". The path has no history: `git log --all -- 'scripts/prototype*'` is empty, `git ls-files` matches nothing under it, and `scripts/` holds only `check_bridge_sync.py` and `check-tag-version.sh`. There is nothing to clean up, so the throwaway bullet should be **struck rather than actioned**, and the opening paragraph and both amendments should name it an uncommitted design exercise so no reader goes hunting for the validation basis.
>
> 5. **Consequences carries no breaking-change consequence, and should.** `docs/user/reference/stability.md:11` lists `Fixtures` under "Stable (semver-protected)", defined at `:7` as surface that "will not change in backward-incompatible ways without a major version bump", and `:16` extends the same promise to "`Plugin` dataclass and protocol interfaces". Rule 8 therefore retires surface the project has undertaken not to remove outside a major version, and `oxi-nixinfra` implements `FixtureProvider` against an unpinned `oxitest` flake input. Rule 8's own marker records the scope for #1720; what is missing *here* is the release consequence of the redesign as a whole. Amendment 4 should add a Consequences bullet stating that completing this ADR requires a major version. A second and separate gap on the same page, for [#1721](https://github.com/kalonji-tools/oxitest/issues/1721)'s sizing: **the replacement surface carries no stability promise at all.** `oxitest.fixture` — the new decorator — appears on none of `stability.md`'s three tiers (Stable `:5`, Experimental `:31`, Internal `:39`, the last an enumerated list of `_bridge` internals rather than a catch-all), while the legacy `Fixtures` registrar it replaces is semver-protected at `:11`. The docs rewrite should tier the new surface, not only untier the old one.
>
> 6. **"Green-field users see only the new surface" is Drifted — both surfaces are live, and the legacy one is still what a newcomer meets first.** `docs/user/how-to/use-fixtures.md:364` tells users the legacy route "still works and is not deprecated" (under the heading at `:361`, "## Legacy: `Fixtures()` in `conftest.py`"); the `oxitest.helper` sentinel actively instructs a new user into the retired API — *"Helpers in oxitest are declared via a Helpers() registry… Define your Helpers() instance in conftest.py"* (`python/oxitest/__init__.py:227`–`:238`); and the package's own module docstring leads its "Public API" section with the legacy registrar — `:3`–`:5`, "Public API / ---------- / `Fixtures` — Instance-based fixture registry. Create one per conftest.py" — while `@oxi.fixture`, `__fixtures__.py` and `lifetime` appear **nowhere in that file at all**, docstring (`:1`–`:101`) or code. Coexistence is deliberate and correct for an incrementally-shipped redesign; what Drifts is the present-tense claim that it is already over, and it ends at [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), not before. Amendment 4 should restate the bullet as conditional on #1720; the sentinel text and the module docstring belong in [#1721](https://github.com/kalonji-tools/oxitest/issues/1721)'s scope. **Note for the Rule 8 tense sweep:** this is the same present-tense-for-future-state defect Rule 8 carries, pointing the *other* way — Rule 8 says the old surface is already gone, this bullet says the new one already stands alone, and both are false for one reason. A sweep scoped to Rule 8 will not reach this one.
