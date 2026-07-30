# ADR-0009: Fixture system redesign

**Status:** Accepted (amended 2026-07-29 — see [Amendments](#amendments))
**Date:** 2026-07-28

The fixture system was designed with a single-location assumption: fixtures live in `conftest.py`, declared via a `Fixtures()` instance whose `.fixture` decorator accumulates definitions during conftest loading. Wayfinder map [#1703](https://github.com/kalonji-tools/oxitest/issues/1703) opened the debate on where fixtures may live — grilling [#1706](https://github.com/kalonji-tools/oxitest/issues/1706) named **Position 4**: promote test-file top-level to first-class, keep the existing `Fixtures()` machinery, add a `ModuleSource` variant on top of the current registry, drop `registrar-in-test-module`, add a new `registrar-in-class-body` violation.

A follow-on first-principles brainstorming session (recorded in the redirect comment on [#1707](https://github.com/kalonji-tools/oxitest/issues/1707#issuecomment-5101919212)) reasoned that Position 4 was a **retrofit** onto machinery built around a single-location assumption. Asking instead "what is the framework's actual job in the fixture system?" produced a structurally different answer, validated with a runnable prototype under `scripts/prototype_fixture_redesign/`. This ADR codifies that answer.

The core reframe is one sentence: **visibility is Python's job; lifecycle is the framework's job.** Python's package/module hierarchy already decides who can see what — the framework doesn't need to redo that. What only the framework can do is track when a value gets instantiated and when it gets disposed. Everything else — location, discovery mechanism, declaration syntax — falls out of that split.

## Considered Options

1. **Retrofit Position 4 onto the current shape.** Add a `ModuleSource` variant next to `ConftestSource`, keep `Fixtures()` and `Helpers()` instance registries, keep walk-up-tree conftest discovery, drop `registrar-in-test-module`, introduce `registrar-in-class-body`. Rejected: it grafts a new axis onto machinery that was never meant to carry it, and preserves the `Fixtures()` / `Helpers()` `&mut` exceptions on [ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4 alongside a new co-existing declaration path. The result is defensible but organically foreign — two ways to declare a fixture, one legacy, one new, both alive indefinitely.

2. **Full runtime enforcement of file conventions via a custom import hook.** Install a Python meta-path finder that intercepts imports of any non-conforming file and rejects fixture-decorated functions found outside `__fixtures__.py` / `__helpers__.py` / `__init__.py` / `test_*.py`. Maximum enforcement, no possibility of dead-code fixtures in unscanned files. Rejected: high runtime cost, poor interoperability with editors and static analysis, breaks under the standard three-tier collection fallback when AST prescan fails. Convention-plus-loud-collection-time-error achieves the same goal with none of the cost.

3. **Principle-plus-rules with file-convention discovery (chosen).** Establish the visibility/lifecycle reframe as the governing principle. Define eight rules covering declaration files, lifetime tiers and boundaries, B1 strict boundary, lifetime cap, proxy access, plugin convergence, autouse, and retirements. Discovery via Rust AST prescan on a reserved-name file set — dunder convention matching Python's own `__init__.py` / `__main__.py`, zero collision with user modules. Enforcement via prescan-time errors (loud rejection at the shallowest catchable frame, per [ADR-0006](0006-async-organizational-strategy.md)) plus a strict dial for shortcut access. No new tooling; existing `ty` + collection-time diagnostics + strict dial suffice.

## Decision

Option 3. The principle below governs the fixture system; the eight rules below define the surface. Follow-on impl work is enumerated in **Consequences** (24 tickets in 5 phases); this ADR lists them but does not file them.

### Principle

> **Visibility is Python's job; lifecycle is the framework's job.** A fixture is a Python callable with a declared **lifetime boundary** — the code-structural unit whose exit triggers teardown. Instantiation is lazy: creation is deferred until a test inside the boundary requests the value. Where the callable lives, who can import it, and what namespace it appears under are determined by Python's package/module hierarchy — the framework's only fixture responsibility is deciding *when* the value gets built and torn down.

### Rule 1 — Declaration files

Fixtures and helpers may be declared only in four file kinds. The Rust AST prescan (`rustpython-parser`) scans these files; everything else is invisible to the framework by design.

| File | Fixtures allowed | Helpers allowed | Notes |
|------|------------------|-----------------|-------|
| `__fixtures__.py` (any package level) | ✓ any lifetime | ✗ | General fixture home |
| `__helpers__.py` (any package level) | ✗ | ✓ | Helper home; single-purpose |
| `__init__.py` (any package level) | ✓ `package` only (recommended) | ✓ | A *declaration home* for package-lifetime things — not what defines the package boundary (Rule 2) |
| `test_*.py` | ✓ `function` or `module` only | ✓ | Inline; fixture lifetime capped at module |

A fixture accidentally placed in `helpers.py` or `utils.py` is invisible to the framework — dead code by design. Enforced by convention, not by walking every module. Declaration API is a pure decorator with zero import-time side effects: `@oxi.fixture(lifetime=...)` (required kwarg) and `@oxi.helper` (no lifetime), both writing marker attributes directly on the wrapped function.

### Rule 2 — Lifetime tiers and boundaries

`Lifetime` is a `StrEnum` with four values, ordered by the **breadth of the code-structural unit** they name — not by the strength of the guarantee they offer. Under parallel execution the ladder is deliberately non-monotonic in guarantee strength; see *Lifetimes under parallel execution* below. Each tier has exactly one boundary — the code-structural unit whose exit triggers teardown.

| Lifetime | Boundary | Disposal trigger | Under parallel execution |
|----------|----------|------------------|--------------------------|
| `function` | The individual test | After the test completes | No effect |
| `module` | The Python module (test file) | After all tests in the module complete | No effect |
| `package` | The directory subtree containing the declaration | After all tests in the subtree complete | **Exactly once per run** — collapses the subtree onto one worker |
| `session` | One worker process | At worker teardown | **Once per worker process** — no scheduling constraint |

**A package is any directory.** `__init__.py` is not required and its absence does not make a directory ineligible. What the framework needs from a package is a subtree to bound disposal, to filter the B1 catalog, and to derive a namespace segment — a directory supplies all three. PEP 420 namespace packages are the norm in modern Python, so requiring the marker file would make oxitest stricter than the language itself, and would contradict this ADR's own principle that visibility is Python's job. `__init__.py` remains a legal and recommended *declaration home* for package-lifetime fixtures (Rule 1); it is not what *defines* the boundary. The two roles were originally conflated.

Yield-based fixtures use `Yields[T]` and expose their teardown code after the `yield` statement (unchanged from the current shape).

#### Lifetimes under parallel execution

oxitest distributes work across worker subprocesses by default (`min_parallel_tests = 100`). Any tier wider than the module therefore has to answer a question a single-process framework never faces: *how many* instances exist when the boundary spans more than one process.

`function` and `module` are unaffected — the scheduler never splits a module across workers, so both tiers are exact for free. The two wide tiers each take one side of a trade the framework cannot avoid:

- **`package` guarantees exactly one instance per run**, and pays for it by co-locating the declaring directory's entire subtree onto a single worker. The guarantee is *structural*, not a caching hint: the scheduler is constrained so that the situation in which a second instance could exist never arises.
- **`session` guarantees one instance per worker process**, and constrains the scheduler not at all. It is the tier for a per-process resource — a connection pool, a compiled-artifact cache — rather than a global singleton.

The ladder is non-monotonic on purpose. `package` buys exactness and charges parallelism; `session` buys parallelism and charges exactness. Neither dominates the other, so the choice stays with the user. Naming a wide tier after the process rather than the code structure has precedent: Playwright's `scope: 'worker'` and Vitest's `scope: 'worker'` both do it. oxitest keeps the structural name `session` for continuity and pins the process semantics here.

**A module belongs to its outermost declaring ancestor.** Where declarations nest, the shallowest wins: a fixture declared higher up already spans the whole subtree, so anchoring on a deeper declaration would still let the scheduler split the outer package across workers and rebuild its value.

**The collapse is announced.** A `package` declaration that merges two or more modules emits a collection-time `WARNING` naming the declaring file, the fixture, and the module count, and pointing at the two exits — narrow the fixture's package, or drop to `lifetime="module"`. A declaring package holding a single module stays silent: it costs no parallelism, and warning there would train users to ignore the message. Documenting the cost beside the tier follows xUnit, which states its widest tier "must be designed for with this parallelism requirement in mind", and Vitest, which introduces its `file`/`worker` collapse in the same paragraph as the tiers themselves.

**Builtin fixtures do not trigger the collapse.** Only user-declared `@oxi.fixture` lifetimes do. `_TempDirFactoryFixture` (`_builtins/_tempdir.py`) declares session scope and would otherwise serialise every oxitest run.

### Rule 3 — B1 strict boundary

A fixture is usable only by tests in its **anchor package** or descendant packages. The anchor package is the directory containing the declaration file (Rule 2 — `__init__.py` not required). For a test at `a.b.c.test_x`, the ancestor chain is `[a, a.b, a.b.c]`; the test may use fixtures anchored anywhere in that chain plus its own module.

Sibling and unrelated packages cannot access the fixture. Attempted use raises `BoundaryError` (diagnostic code `fixture-boundary`) naming the fixture's anchor, the test's package, and the legal exits. No allow-comment escape hatch. No `strict = "warn"` softening. This follows [ADR-0006](0006-async-organizational-strategy.md)'s loud-rejection precedent.

Package-scope fixtures anchored at `tests/api/` are usable from `tests/api/v1/test_x.py` (descendant) but not from `tests/other/test_y.py` (sibling).

**Enforcement is at access time, not collection time.** This ADR originally specified the opposite:

> Attempted use is a **collection-time error** […] violations fire at collection time, before any test runs, at the shallowest catchable frame.
>
> Hierarchical prescan enforces this at discovery: given a test being collected, the framework prescans only the ancestor-chain declaration files.

That is **amended**. Prescan extracts fixture *declarations*, not fixture *usages*, so nothing at collection time knows that a test intends to reach `fx.admin.conn`. The gate fires instead at the two resolution routes — the `fx` proxy and `Fixture[T]` parameter injection — and again when descending into a fixture's own dependencies.

The amendment is not a weakening of the loud-rejection principle, because a collection-time gate could never have been the *only* gate: dynamic access (`getattr(fx, name)`) defeats any static analysis, so an access-time check is required whatever else exists. Collection-time enforcement is therefore an optimisation layered on top — earlier failure, and the precondition for the editor-squiggle tooling in this ADR's Consequences — tracked by [#1758](https://github.com/kalonji-tools/oxitest/issues/1758).

**A fixture's dependencies are governed by the fixture's own anchor**, not by the location of the test that triggered resolution. Otherwise a fixture anchored at `tests/api/` could acquire a dependency anchored at `tests/api/v1/` whenever it happens to be resolved by a test living there — a dependency it could never legally declare — and, at `lifetime="package"`, cache a value that embeds one from a narrower boundary. This is the "declaration time" half of enforcement referred to in the Consequences.

Implemented in [#1713](https://github.com/kalonji-tools/oxitest/issues/1713).

### Rule 4 — Lifetime cap

Declared `lifetime` cannot exceed the declaration site's boundary.

| Declaration site | Max legal lifetime |
|------------------|-------------------|
| Inline in `test_*.py` | `module` |
| `__fixtures__.py` or `__helpers__.py` at package X | `package` (anchored at X) |
| `__init__.py` at package X | `package` (anchored at X) |
| Any of the above at the rootdir package (`tests/`) | `package` (exactly once per run) or `session` (once per worker) |

Anything else is a **declaration error at prescan time** with three legal-exit hints (move to `__fixtures__.py` at package level; drop to `module` lifetime; restructure as a rootdir fixture).

**`session` is available only at rootdir, and is not a synonym for rootdir `package`.** This ADR originally argued the opposite:

> `session` is available only at rootdir because below root, `package(root)` and `session` collapse to the same runtime behavior (the fixture's visibility subtree is smaller than the run — either it's never referenced outside the anchor package under B1 and equals `package`, or it would leak). At rootdir the two ARE the same thing; the framework accepts `session` as the idiomatic name for the run-lifetime tier.

That equivalence is **retracted**. It was sound under the single-process model this ADR was written against, and it does not hold under the real scheduler: rootdir `package` is exactly-once and serialises the run, while `session` is once-per-worker and does not. They are genuinely different tiers, and offering both is strictly more expressive than declaring them synonyms.

The *restriction* of `session` to rootdir survives, for the half of the original argument the retraction does not touch: declared below root, a `session` fixture would outlive the subtree that is allowed to see it, which is exactly what Rule 4 exists to prevent.

**A per-worker `session` fixture cannot be a true singleton.** Anything that must happen exactly once per run — a database migration, a schema create, a shared artifact build — belongs at rootdir `package` and pays the parallelism cost. Frameworks that do offer a cross-process once-per-run hook restrict it to serialised handles rather than live objects; Jest is explicit that "any global variables that are defined through `globalSetup` can only be read in `globalTeardown`. You cannot retrieve globals defined here in your test suites."

### Rule 5 — Access via `fx` / `hlp` proxies

Tests receive fixtures and helpers via two synthesized proxy parameters — the type annotations `Fixtures` and `Helpers` reappear here as access proxies (the old instance-registry meaning is retired, see Rule 8):

```python
def test_flow(fx: Fixtures, hlp: Helpers):
    conn = fx.api.conn                    # qualified
    resp = hlp.api.make_request(conn, "/users")
    tx = fx.tx                            # shortcut — unconditionally legal
```

**Qualified access** (`fx.<segment>.<name>`) walks the package path and always works when the fixture is in the test's ancestor chain. Cross-boundary use raises `BoundaryError` with an actionable diagnostic.

**Shortcut access** (`fx.<name>` without a package prefix) resolves the nearest visible fixture and is **unconditionally legal** — no diagnostic, no configuration. Resolution is B1-filtered exactly as qualified access is, so a shortcut can never reach a fixture the test could not reach by its qualified path; the shortcut saves keystrokes, never scope. Because a bare name carries no segment to attribute blame to, a cross-boundary shortcut reports `FixtureNotFoundError` rather than `BoundaryError`, matching what `Fixture[T]` injection already does (see *Error type is a function of the segment alone*, below).

This ADR originally gated the shortcut behind a three-position strict dial. That gate is retracted; see [Amendment 3](#amendment-3--the-shortcut-strict-dial-is-retracted-2026-07-30) for why, including the industry-precedent argument that motivated it and why the argument does not survive the fact that oxitest's dial has no `warn` position.

**Two access routes, asymmetric on purpose.** `Fixture[T]` parameter injection is bare-name *only* — `resolve_param` looks up by parameter name and no `Fixture[T]` spelling carries a package path. So un-prefixed access is mandatory on that route while the proxy route offers both forms. The asymmetry is not a claim that the routes are principled opposites: functionally `conn: Fixture[Connection]` resolves exactly as `fx.conn` does. It is that the injection route has no alternative spelling, so forbidding bare names there would delete the route, whereas on the proxy route the qualified form always exists. Recorded so a later reader does not mistake it for an oversight. A qualification syntax for `Fixture[T]` is not planned.

**Two-catalogs design constraint.** Both `FixturesProxy` and `HelpersProxy` must be able to consult two views — the **B1-filtered catalog** (fixtures visible to *this* test, used for resolution) and the **full catalog** (every fixture in the run, used for diagnostic quality). The prototype surfaced this: without the full view, the proxy cannot tell "package `api`" apart from "fixture `api`" when neither is in the filtered set (both would look like unknown names), and cross-boundary access reports as `FixtureNotFoundError` — "you have a typo" — when the correct diagnostic is `BoundaryError`. Neither view is optional.

The constraint is about *reachability of both views*, not about object count. This ADR originally said the proxies "hold two references", which read as two catalog objects; the implementation satisfies it with two query modes over the single `FixtureRegistry`, since the proxy already carries the test's module path and the registry can be asked either question. Materialising a filtered catalog per test would cost an O(all fixtures) pass for every test in the run and buy nothing the predicate does not already give.

**The guarantee is scoped to directory-anchored fixtures.** Declaration homes (`__fixtures__.py`, `__init__.py`) are registered run-wide — on the serial path and into every worker — so the full view is genuinely full and the verdict is deterministic. An inline declaration is registered only when its test module is imported, so its presence in the catalog depends on worker assignment, selection, and import order. Rather than let the *diagnostic* vary with scheduling, cross-module access to an inline fixture reports `FixtureNotFoundError` with a static hint that inline declarations are capped at `module` lifetime. Semantics are unaffected either way — the access is illegal in both readings. Upgrading the message using the declarations prescan already extracts is [#1759](https://github.com/kalonji-tools/oxitest/issues/1759).

**Error type is a function of the segment alone.** An unreachable segment raises `BoundaryError`; a segment unknown anywhere raises `FixtureNotFoundError`. When the segment is unreachable *and* the leaf does not exist there either, the boundary is reported first, with the missing leaf appended — the boundary statement is true regardless of the leaf, whereas leading with the typo would imply that fixing the spelling makes the access work.

**Naming clash rule.** A fixture named the same as a sibling package segment is shadowed by the segment in shortcut form (`fx.api` returns the sub-proxy, not a fixture named `api`); the fixture remains reachable via the qualified path. Convention: avoid the collision. Applies identically to helpers. This rule was vacuous until shortcut access existed — `FixturesProxy.__getattr__` had no fixture branch for a segment to win against — and became live with [#1714](https://github.com/kalonji-tools/oxitest/issues/1714).

**Framework builtins are not shortcut-reachable.** `fx.oxi.tmp` is the only spelling for a builtin; the reserved `oxi` namespace exists so framework names cannot collide with user fixture names, and hoisting them into the flat namespace would put `log`, `patch`, and `cap` where a user's own fixture of that name would clash. The registry names builtins after their private implementation class (`_TempDirFixture`), which the proxy's leading-underscore guard already rejects, so the property holds without a filter — but it holds *incidentally*, resting on a naming convention rather than a predicate, and is pinned by a regression test rather than left to be rediscovered.

**Namespace derivation.** Default namespace = the anchor-package segment name; overridable via `namespace=` on the decorator. Use overrides sparingly.

### Rule 6 — Plugin convergence

Plugins register fixtures via the **same decorator path as user code**. A plugin package with a `__fixtures__.py` file declares fixtures with `@oxi.fixture` exactly as users do; the framework treats each activated plugin as an ambient ancestor, making plugin fixtures visible session-wide under the plugin's declared namespace (e.g., `fx.postgres.pg_session` for a `postgres` plugin).

Plugins that need to generate fixtures at runtime (5% case — e.g., one fixture per detected DB schema) export an optional `register_fixtures(registry: FixtureRegistry) -> None` hook called at session initialization after AST prescan. Dynamically-added `FixtureDef`s land in the same registry the AST-scanned ones do; identical semantics after registration.

`FixtureProvider` and `HelperProvider` protocols retire. Migration from `FixtureProvider.register_fixtures(reg)` to the module-level `register_fixtures(registry)` hook is mechanical — the shape is close, only the entry point changes.

### Rule 7 — Autouse

`@oxi.fixture(autouse=True, lifetime="...")` fires for every test in the fixture's B1 boundary without being explicitly requested. Same shape as today; the lifetime cap from Rule 4 applies unchanged.

| `autouse=True, lifetime=X` | Fires… |
|----------------------------|--------|
| `function` | Once per test in the fixture's B1 scope |
| `module` | Once per module boundary in scope |
| `package` | Once per package boundary in scope — exactly once per run (Rule 2) |
| `session` | Once per worker process. For autouse work that must happen exactly once per run, declare at rootdir with `lifetime="package"` |

Autouse fixtures remain accessible by explicit request (`Fixture[T]` or `fx.<name>`); autouse is additive, not exclusive. The invisibility concern historically raised against autouse is solved by tooling, not by removing the feature: `oxitest inspect` shows autouse-firing per test as a first-class view (a follow-on impl item — see Consequences).

### Rule 8 — Retirements

The redesign retires the following surface. Each entry names what goes and why.

- **`Fixtures()` and `Helpers()` as instance-based registries** — the `db = Fixtures(name="db")` + `@db.fixture` pattern is replaced by module-level `@oxi.fixture` / `@oxi.helper`. The **names** `Fixtures` and `Helpers` are reused as the access-proxy type annotations in test signatures (Rule 5); the old instance usage no longer exists.
- **`conftest.py` as a special filename** — replaced by `__fixtures__.py` / `__helpers__.py` / `__init__.py`. Walk-up-tree conftest discovery (`find_conftest_paths` in `conftest_loader.py`) is replaced by hierarchical AST prescan on the ancestor chain of the tests being collected.
- **`registrar-in-test-module` strict violation and its `# oxitest: allow[registrar-in-test-module]` escape hatch** — the whole class of violation becomes nonsensical (there is no registrar to be in a test module). The allow-comment escape hatch was itself an [ADR-0008](0008-config-fail-closed-narrow-scope.md) violation and its removal restores the no-escape-hatch discipline.
- **`ConftestSource` variant** in `_fixture_registry.py` — replaced by a location-agnostic source variant carrying `defining_module_path` + `anchor_package_path`.
- **`FixtureProvider` and `HelperProvider` plugin protocols** — plugins converge with the user path via `@oxi.fixture` + `register_fixtures` hook (Rule 6).
- **[ADR-0005](0005-immutable-by-default-interfaces.md) Rule 4's `Fixtures` / `Helpers` `&mut` exception** — no mutable registrar exists; the decorator writes marker attributes directly at import time, no accumulation phase.

### Reconciliation with prior ADRs

- **[ADR-0002](0002-unified-fixture-backend.md) (Unified fixture backend)** — Type-based resolution (`Fixture[T]` primary key, parameter name as qualifier) and the unified registry survive intact. Source variants collapse: `ConftestSource` retires; the new source variant carries `defining_module_path` + `anchor_package_path`. Override precedence extends naturally with the new lifetime tiers.
- **[ADR-0005](0005-immutable-by-default-interfaces.md) (Immutable-by-default) Rule 4** — Retires the `Fixtures` / `Helpers` `&mut` exceptions. Decorators write marker attributes at import time; no accumulation-during-conftest-loading phase remains. The reused type annotation names (`Fixtures`, `Helpers` on test parameters) are proxy accessors, not mutable registrars — they do not re-inherit the exception.
- **[ADR-0006](0006-async-organizational-strategy.md) (Async organizational strategy)** — Async fixture behavior is orthogonal to declaration mechanism. `@fixture(lifetime="function")` on an `async def` behaves per ADR-0006's per-test-loop rules. Implemented in [#1733](https://github.com/kalonji-tools/oxitest/issues/1733) for the `function` and `module` tiers, with three refinements ADR-0006 did not anticipate, because it assumed fixtures are resolved *before* the test body: (a) `fx.<ns>.<name>` returns an awaitable — `await fx.pkg.conn` — since attribute access offers no earlier hook; (b) an async fixture wider than `function` lifetime promotes async test bodies onto the shared session loop, because a value cannot move between loops and a per-test loop dies before the fixture's boundary is reached; (c) teardown fires at the declared boundary, clamped so it can never be scheduled after its loop closes. Illegal cell combinations (sync test + function-scope async fixture) are rejected loud on both access paths — at arrange time for `@arrange`, at access time for the proxy. Loud-rejection DNA is *reinforced* by this ADR: B1 boundary violations and lifetime-cap violations fire at the shallowest frame that can catch them — declaration-site violations at prescan, B1 violations at access (Rule 3, as amended). The same loud rejection covers the shortcut route: a sync test reaching an async fixture via `fx.<name>` raises `AsyncFixtureAccessError` at access, before the factory runs, exactly as the qualified route does, so async-ness is a property of the fixture rather than of the access form chosen. (This list previously included strict-abort shortcut violations; the strict dial is retracted — see Amendment 3.)
- **[ADR-0008](0008-config-fail-closed-narrow-scope.md) (Config fail-closed)** — B1 boundary violation and lifetime-cap violation both fail closed. No per-callsite bypass anywhere in the new surface. This originally read "…and strict-dial-forbidden shortcut all fail closed. No per-callsite bypass anywhere in the new surface; all configurability lives on the strict dial" — with the dial retracted (Amendment 3) the new surface has **no** configurability at all, which is a stronger fail-closed position than the one claimed, not a weaker one. The *exit code* differs by when the violation is catchable: declaration-site violations abort collection with a `UsageError` exit code, while a B1 violation surfaces as a test `ERROR` and the ordinary failing-run exit code, because it is detected inside a running test and must not blank the results of every other test in the run. Giving the run a `UsageError` exit code without aborting it needs a per-test bridge-to-coordinator exit vote that does not exist yet — [#1761](https://github.com/kalonji-tools/oxitest/issues/1761).

## Amendments

### Amendment 1 — the parallelism model (2026-07-29)

Tracked by [#1746](https://github.com/kalonji-tools/oxitest/issues/1746). Amends Rules 1, 2, 4, and 7. The principle, the declaration-file convention, the B1 boundary, proxy access, plugin convergence, and the retirements all stand as accepted.

This ADR was written against a single-process mental model. Neither the document as accepted on 2026-07-28 nor the [design spec](https://github.com/kalonji-tools/oxitest/issues/1707#issuecomment-5101919212) it came from contains a single occurrence of `parallel`, `worker`, or `subprocess` — while oxitest distributes tests across worker subprocesses by default at `min_parallel_tests = 100`. Three statements did not survive contact with the scheduler:

1. **The lifetime ladder said nothing about parallelism.** Under the real scheduler a fixture session is created and torn down per task, so the widest *effective* tier was the module — narrower than every framework surveyed. Rule 2 now states, per tier, what happens when work spans processes, and `package` earns its exactly-once guarantee structurally by collapsing its subtree onto one worker.
2. **`package` was defined as "the Python package (directory with `__init__.py`)".** That made the tier unreachable in oxitest's own test suite, in which no directory holding real test modules carries an `__init__.py`. Rule 2 now defines a package as any directory; `__init__.py` keeps its role as a declaration home.
3. **Rootdir `package` and `session` were declared the same thing.** They are not, once instances can exist per process. Rule 4 carries the retraction with the original argument quoted in full.

Rule 7's autouse table follows from (1) and (3): a `session` autouse fixture fires once per worker process, not "once for the whole run".

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

## Consequences

- **New declaration surface for users.** All fixture and helper declarations move to module-level `@oxi.fixture(lifetime=...)` / `@oxi.helper` in one of four reserved file kinds. Existing users need a migration path (see follow-on impl, Documentation phase). Green-field users see only the new surface.
- **Two catalogs on every proxy.** `FixturesProxy` and `HelpersProxy` implementations carry both the B1-filtered catalog and the full catalog — filtered for resolution decisions, full for diagnostic attribution. Skipping the second catalog produces misleading diagnostics (`FixtureNotFoundError` in place of `BoundaryError`).
- **Prescan-time errors replace collection-time errors for a broader class of violations.** B1 boundary violations and lifetime-cap violations both fire at prescan (before any Python import, before any fixture instantiation). This is faster failure and enables better tooling (e.g., editor squiggles on illegal declarations).
- **Fallback to Python-import discovery survives.** If AST prescan encounters dynamic decoration patterns it cannot statically parse (e.g., `if flag: dec = fixture; @dec def x(): ...`), it emits `PrescanResult::Unavailable` and the file falls through to Python-import-based discovery — the same three-tier collection model already used for tests.
- **Deferred design questions.** The following are real design questions this ADR does not fully resolve; they belong to the impl-plan phase or a follow-on spec: IDE / type-checker stub generation for the `fx` / `hlp` proxies (auto-generated `.pyi` vs. dynamic-only vs. user-declared Protocol overlay); `FixtureRegistry.add()` runtime API details (ordering guarantees, duplicate-name handling); the migration story from the current design to this one (incremental coexistence vs. hard cutover); `FixtureRef[T]` internals under the new source variant; `oxitest inspect` updates for the new source variant and autouse-firing view.
- **Prototype is throwaway.** `scripts/prototype_fixture_redesign/` is a 300-line Python-only simulation with six interactive scenarios. Delete it once the follow-on impl issues are filed, or fold pieces into test fixtures for the real implementation.
- **Follow-on impl work (23 tickets in 5 phases).** This ADR **lists** the follow-on work; it does **not** file the tickets. Filing happens post-merge per the standard project pipeline (grill → spec → PR). Enumeration:

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

- **Wayfinder map [#1703](https://github.com/kalonji-tools/oxitest/issues/1703) reaches its destination on merge of this ADR.** The map's remaining work was tracked by [#1707](https://github.com/kalonji-tools/oxitest/issues/1707), whose task was drafting this document. Once merged, the map closes; the follow-on impl tickets above are filed as fresh project work, not resumed map tickets.
