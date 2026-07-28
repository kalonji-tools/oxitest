# ADR-0009: Fixture system redesign

**Status:** Accepted
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
| `__init__.py` (any package level) | ✓ `package` only (recommended) | ✓ | Package-init file; may host package-scope things |
| `test_*.py` | ✓ `function` or `module` only | ✓ | Inline; fixture lifetime capped at module |

A fixture accidentally placed in `helpers.py` or `utils.py` is invisible to the framework — dead code by design. Enforced by convention, not by walking every module. Declaration API is a pure decorator with zero import-time side effects: `@oxi.fixture(lifetime=...)` (required kwarg) and `@oxi.helper` (no lifetime), both writing marker attributes directly on the wrapped function.

### Rule 2 — Lifetime tiers and boundaries

`Lifetime` is a `StrEnum` with four ordered values. Each tier has exactly one boundary — the code-structural unit whose exit triggers teardown.

| Lifetime | Boundary | Disposal trigger |
|----------|----------|------------------|
| `function` | The individual test | After the test completes |
| `module` | The Python module (test file) | After all tests in the module complete |
| `package` | The Python package (directory with `__init__.py`) | After all tests in the package + subpackages complete |
| `session` | The entire test run | At run teardown |

Yield-based fixtures use `Yields[T]` and expose their teardown code after the `yield` statement (unchanged from the current shape).

### Rule 3 — B1 strict boundary

A fixture is usable only by tests in its **anchor package** or descendant packages. The anchor package is the Python package containing the declaration file. For a test at `a.b.c.test_x`, the ancestor chain is `[a, a.b, a.b.c]`; the test may use fixtures anchored anywhere in that chain plus its own module.

Sibling and unrelated packages cannot access the fixture. Attempted use is a **collection-time error** naming the fixture's anchor and the test's location. No allow-comment escape hatch. No `strict = "warn"` softening. This follows [ADR-0006](0006-async-organizational-strategy.md)'s loud-rejection precedent: violations fire at collection time, before any test runs, at the shallowest catchable frame.

Package-scope fixtures anchored at `tests/api/` are usable from `tests/api/v1/test_x.py` (descendant) but not from `tests/other/test_y.py` (sibling). Hierarchical prescan enforces this at discovery: given a test being collected, the framework prescans only the ancestor-chain declaration files.

### Rule 4 — Lifetime cap

Declared `lifetime` cannot exceed the declaration site's boundary.

| Declaration site | Max legal lifetime |
|------------------|-------------------|
| Inline in `test_*.py` | `module` |
| `__fixtures__.py` or `__helpers__.py` at package X | `package` (anchored at X) |
| `__init__.py` at package X | `package` (anchored at X) |
| Any of the above at the rootdir package (`tests/`) | `session` |

Anything else is a **declaration error at prescan time** with three legal-exit hints (move to `__fixtures__.py` at package level; drop to `module` lifetime; restructure as a rootdir `session` fixture). `session` is available only at rootdir because below root, `package(root)` and `session` collapse to the same runtime behavior (the fixture's visibility subtree is smaller than the run — either it's never referenced outside the anchor package under B1 and equals `package`, or it would leak). At rootdir the two ARE the same thing; the framework accepts `session` as the idiomatic name for the run-lifetime tier.

### Rule 5 — Access via `fx` / `hlp` proxies

Tests receive fixtures and helpers via two synthesized proxy parameters — the type annotations `Fixtures` and `Helpers` reappear here as access proxies (the old instance-registry meaning is retired, see Rule 8):

```python
def test_flow(fx: Fixtures, hlp: Helpers):
    conn = fx.api.conn                    # qualified
    resp = hlp.api.make_request(conn, "/users")
    tx = fx.tx                            # shortcut (gated by strict dial)
```

**Qualified access** (`fx.<segment>.<name>`) walks the package path and always works when the fixture is in the test's ancestor chain. Cross-boundary use raises `BoundaryError` with an actionable diagnostic.

**Shortcut access** (`fx.<name>` without a package prefix) is gated by the strict dial:

| `strict` value | `fx.tx` shortcut | Diagnostic |
|----------------|-----------------|------------|
| `"off"` | Allowed | Silent |
| `"warn"` (default) | Allowed | `NOTICE` diagnostic suggesting the qualified form |
| `"abort"` | Collection error `fixture-shortcut-in-strict` | Names the fixture's qualified path |

The strict-dial pattern (rather than always-allow or always-forbid) matches industry precedent for scope-narrowing shortcuts: Rust Clippy's `wildcard_imports` (in `pedantic`, not `default`), Java Checkstyle's configurable `AvoidStarImport`, C++ Core Guidelines' scope-stratified rules on unqualified names. All three leave the shortcut legal at the language level and delegate suppression to opt-in strict modes. oxitest's `strict = "warn"` default mirrors this: shortcuts work, users get a `NOTICE` on each use, `strict = "abort"` upgrades the notice to a collection error.

**Two-catalogs design constraint.** Both `FixturesProxy` and `HelpersProxy` hold two references — the **B1-filtered catalog** (fixtures visible to *this* test, used for resolution) and the **full catalog** (every fixture in the run, used for diagnostic quality). The prototype surfaced this: without the full catalog, the proxy cannot tell "package `api`" apart from "fixture `api`" when neither is in the filtered set (both would look like unknown names), and cross-boundary access reports as `FixtureNotFoundError` — "you have a typo" — when the correct diagnostic is `BoundaryError`. Neither catalog is optional.

**Naming clash rule.** A fixture named the same as a sibling package segment is shadowed by the segment in shortcut form (`fx.api` returns the sub-proxy, not a fixture named `api`); the fixture remains reachable via the qualified path. Convention: avoid the collision. Applies identically to helpers.

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
| `package` | Once per package boundary in scope |
| `session` | Once for the whole run |

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
- **[ADR-0006](0006-async-organizational-strategy.md) (Async organizational strategy)** — Async fixture behavior is orthogonal to declaration mechanism. `@fixture(lifetime="function")` on an `async def` continues to behave per ADR-0006's per-test-loop rules. **Not yet true as implemented** — the registrar never sets `FixtureDef.is_async`, so an `async def` under `@fixture` is injected as an un-awaited coroutine at every tier; tracked in [#1733](https://github.com/kalonji-tools/oxitest/issues/1733). This paragraph states the intended end state, not current behavior. Illegal cell combinations (sync test + function-scope async fixture) still rejected loud at arrange time. Loud-rejection DNA is *reinforced* by this ADR: B1 boundary violations, lifetime-cap violations, and strict-abort shortcut violations all fire at collection time.
- **[ADR-0008](0008-config-fail-closed-narrow-scope.md) (Config fail-closed)** — B1 boundary violation, lifetime-cap violation, and strict-dial-forbidden shortcut all fail closed with `UsageError` exit codes. No per-callsite bypass anywhere in the new surface; all configurability lives on the strict dial.

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
  9. Shortcut behavior + strict-dial gating.
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
