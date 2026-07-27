# Built-ins shape audit

Resolves the research half of [`Wayfinder: fixture-vs-helper identity for oxitest built-ins` #1697](https://github.com/kalonji-tools/oxitest/issues/1697) child ticket [#1698](https://github.com/kalonji-tools/oxitest/issues/1698). Companion to the adjacent-framework survey ([#1699](https://github.com/kalonji-tools/oxitest/issues/1699)). Feeds the grilling ticket [#1700](https://github.com/kalonji-tools/oxitest/issues/1700).

## Inventory

Eight built-ins in `oxitest._bridge._builtins/` + `_builtin_context.py`:

| Built-in | Module | Shipped shape |
|---|---|---|
| `TempDir` | `_tempdir.py` | Fixture (bare-type annotation) |
| `TempDirFactory` | `_tempdir.py` | Fixture (bare-type annotation) — session-scoped by construction |
| `StdCapture` | `_capture.py` | Fixture (bare-type annotation) |
| `FdCapture` | `_capture.py` | Fixture (bare-type annotation) |
| `Patcher` | `_patch.py` | Fixture (bare-type annotation) |
| `LogCapture` | `_logcapture.py` | Fixture (bare-type annotation) |
| `WarnCapture` | `_warncapture.py` | Fixture (bare-type annotation) |
| `TestContext` | `_builtin_context.py` | Fixture (bare-type annotation) — registered via `BuiltinFixture._registry` |

**All eight are shipped as fixtures. Zero are shipped as helpers.**

## Real-world usage evidence

Method-call census over 60 test files in the loguru migration ([kalonji-tools/loguru:oxitest-migration](https://github.com/kalonji-tools/loguru/tree/oxitest-migration), commit `dcfddf9`):

| Built-in | Tests using as parameter | Imperative method calls on the object |
|---|---:|---|
| `TempDir` | 101 | `tmp.path` × 251 |
| `StdCapture` | 108 | `cap.readouterr()` × 116 |
| `LogCapture` | 2 | `log.records` × 2 (no `set_level`, no `at_level`) |
| `Patcher` | **0** | n/a — [patch_context() helper](https://github.com/kalonji-tools/loguru/blob/oxitest-migration/tests/conftest.py#L29) invented instead (41 uses) |
| `WarnCapture` | **0** | n/a — [`oxitest.warns(...)` context manager](https://github.com/kalonji-tools/loguru/blob/oxitest-migration/tests/test_add_sinks.py) used for the 3 assertion cases |
| `FdCapture` | **0** | n/a — loguru writes at Python stream level, StdCapture suffices |
| `TempDirFactory` | **0** | n/a — no session-shared temp-dir need |
| `TestContext` | **0** | n/a — `Yields[T]` fixture teardown used everywhere instead of `ctx.addfinalizer()` |

**Two clear patterns emerge:**

1. **Value-carrying built-ins** (`TempDir`): the fixture yields a passive object with a data attribute (`.path`). Test uses it as if the attribute were the value. No imperative surface. **Fixture shape is a clean fit.**
2. **State-carrying-with-imperative-methods built-ins** (`StdCapture`, `LogCapture`, `Patcher`): the fixture yields an object; test calls methods on it (`.readouterr()`, `.records`, `.setattr()`) throughout the test body to inspect or mutate captured/patched state. **Fixture shape works but couples the imperative operations to test-level lifetime.**

The migration evidence shows the second pattern breaks down when adopters want block-scope isolation on subsets of the imperative operations. Patcher is the canonical case: `Patcher.setattr()` is a test-level operation, but adopters routinely want `with patch_context() as p: p.setattr(...)` for nested scoping — which the fixture shape doesn't support.

---

## Per-built-in assessment

### 1. `TempDir` — VERDICT: right shape

**How it's shipped.** `@injectable class TempDir` in `_tempdir.py`. Instance carries one attribute: `path: pathlib.Path`. Fixture creates the directory before the test, deletes it after.

**How it's typically used.**
```python
def test_file_delayed(tmp: TempDir) -> None:
    file = tmp.path / "test.log"
    logger.add(file, format="{message}", delay=True)
    assert not file.exists()
```
101 tests use it as a parameter; `tmp.path` accessed 251 times. Value-carrier pattern — attribute access only, no imperative methods invoked.

**Would the other shape work?** A helper (`with helpers.oxi.tempdir() as tmp:`) would work mechanically — but every use is scoped exactly to the test (create at start, delete at end). Nested block-scoping over subsets of `.path` doesn't have a natural interpretation. The fixture-shipped-once-per-test lifetime IS the right lifetime.

**Verdict.** Right shape. The imperative-surface concern doesn't apply — there is no imperative surface.

---

### 2. `TempDirFactory` — VERDICT: right shape (unused in loguru; not a shape concern)

**How it's shipped.** `@injectable class TempDirFactory` in `_tempdir.py`. Session-scoped by construction: one factory per session, each `factory.mktemp(name)` call returns a fresh sub-`TempDir`. Cleanup happens once at session end.

**How it's typically used.** N/A in loguru (0 uses). Intended for tests that need multiple related temp directories (e.g., stress-testing rotation with 20 subdirs) where session-level lifetime is desirable.

**Would the other shape work?** A helper would defeat the session-lifetime purpose (each `with helpers.oxi.tempdir_factory():` invocation would create then destroy the factory). Session-scoped factory is definitionally a fixture concept.

**Verdict.** Right shape. Loguru's non-use is legitimate — not a shape signal.

---

### 3. `StdCapture` — VERDICT: right shape, `readouterr()` imperative surface works

**How it's shipped.** `@injectable class StdCapture` (via `_CaptureBase`). Replaces `sys.stdout` / `sys.stderr` with buffers at fixture-setup; restores at teardown. Exposes `readouterr()` returning `CaptureResult(out, err)` and `disabled()` context manager for pass-through blocks.

**How it's typically used.**
```python
def test_catch_is_true(cap: StdCapture) -> None:
    logger.add(broken_sink, catch=True)
    logger.debug("Fail")
    captured = cap.readouterr()
    assert captured.err != "", "catch=True must report the sink's exception ..."
```
108 tests inject as parameter; `readouterr()` called 116 times. Imperative pattern — inject once, call multiple times to check accumulated output at different points.

**Would the other shape work?** `with helpers.oxi.stdcapture() as cap:` would work but LOSES the "capture the whole test" default that adopters actually want. If capture is only in a block, output before/after the block leaks through — usually not desired. `StdCapture.disabled()` already handles the "temporarily let output through" case within the fixture-scoped default. **Fixture-shaped default with an imperative escape hatch is the correct polarity.**

**Verdict.** Right shape. The `.disabled()` context manager pattern proves that "fixture with imperative escape-hatch" is workable — same pattern would fit Patcher (see #5) and would resolve the loguru migration's `patch_context()` complaint.

---

### 4. `FdCapture` — VERDICT: right shape (unused in loguru; not a shape concern)

Same shape as `StdCapture`, targeting fd 1 / fd 2 rather than Python-level streams. Meant for tests exercising code that writes via C extensions or `os.write` where Python's `sys.stdout` intercept doesn't catch the output. Loguru writes at Python stream level, so `FdCapture` is legitimately unneeded — not a shape signal.

---

### 5. `Patcher` — VERDICT: wrong-shape-as-default; needs dual shape

**How it's shipped.** `@injectable class Patcher` in `_patch.py`. Fixture-shaped: bind `patch: Patcher` param, call `patch.setattr(obj, name, value)` / `patch.setenv(name, value)` / `patch.chdir(path)` — all undone at test end via `close()` in `ctx.teardown_stack`.

**How it's typically used.** In the loguru migration: **not at all** (0 tests use `Patcher`). Every one of the 41 patching sites uses a bespoke `patch_context()` helper the migration invented in conftest.py. The helper is called as `with patch_context() as ctx: ctx.setattr(...)` — block-scoped, matches `pytest.monkeypatch.context()` semantics.

Original pytest usage the migration had to port: 55 tests used `monkeypatch` as a parameter (test-wide scoping). The migration DID NOT map these 55 → 55 `patch: Patcher`. It collapsed everything to 41 uses of block-scoped `patch_context()`, standardizing on the tighter-scoped shape.

**Would the other shape work?** Yes — a `with helpers.oxi.patcher() as p:` helper is what the migration built and what the API-surface-gap analysis (below) argues should ship.

**API surface gaps between `Patcher` fixture and `patch_context()` helper the migration built:**

| Concern | `Patcher` (built-in fixture) | `patch_context()` (invented helper) |
|---|---|---|
| Lifetime | Test-scoped | Block-scoped |
| `setattr(raising=False)` | ❌ Always raises | ✅ (matches pytest-monkeypatch) |
| `delattr` | ❌ | ✅ |
| `setitem` / `delitem` on arbitrary mappings | ❌ | ✅ (and `setenv` derives from `setitem`) |
| `chdir` | ✅ | ❌ (unused by loguru) |

**Verdict.** Ship **both shapes** — `Patcher` fixture for whole-test scoping (kept as-is), plus `patcher` helper for block scoping. Broaden both API surfaces to match pytest-monkeypatch (add `raising=` kwarg, `delattr`, `setitem`, `delitem`). Same-name convention like pytest's `monkeypatch` / `monkeypatch.context()` — or Astral-style separate objects — either works. **This is the strongest shape-mismatch finding across all built-ins**, corroborated by 41-to-0 usage data.

Already filed as showcase-eval issue [#1696](https://github.com/kalonji-tools/oxitest/issues/1696).

---

### 6. `LogCapture` — VERDICT: right shape with methods that are helper-shaped

**How it's shipped.** `@injectable class LogCapture` in `_logcapture.py`. Aggregates records from registered `LogBackend` instances; primary backend is `StdlibLogBackend(logging.getLogger())`. Attributes: `records: list[LogRecord]`, `text: str`. Methods: `set_level(level, logger=None)`, `at_level(level, logger=None)` context manager, `close()`.

**How it's typically used.** In the loguru migration: 2 tests, both in test_coroutine_sink.py. Only `log.records` accessed — never `set_level`, never `at_level`. Trivial usage — inject, read records.

**Would the other shape work?** The `at_level()` context manager is ALREADY a block-scoped API surface on a fixture-scoped object — same "fixture with imperative escape-hatch" polarity as `StdCapture.disabled()`. That combination is the right shape.

**Verdict.** Right shape. Both the fixture-scoped default (capture everything the test emits) and the block-scoped escape hatch (`at_level(logging.DEBUG, "myapp.subsystem"): ...`) exist. Loguru's minimal usage doesn't stress the shape at all.

**Docs observation (not a shape verdict):** The set of `log.at_level`/`log.set_level` context-manager methods is exactly the pattern Patcher lacks. If Patcher shipped `.setattr_scope()` returning a context manager for the same "narrow this operation to a block within the test-scoped patcher" pattern, the 41-to-0 usage collapse would likely not have happened. LogCapture is a good model.

---

### 7. `WarnCapture` — VERDICT: right shape (unused in loguru; `oxitest.warns` covers assertion case)

**How it's shipped.** `@injectable class WarnCapture` in `_warncapture.py`. Installs a `warnings.simplefilter("always")` + capturing handler at fixture setup. Attributes: `warnings: list[WarningMessage]`. Methods: `clear()`.

**How it's typically used.** In the loguru migration: 0 uses of `WarnCapture` fixture. But `oxitest.warns(Category, match=...)` context manager is used 3 times — the "assert that this warning is emitted" case.

**Would the other shape work?** `oxitest.warns()` IS a helper (top-level function returning context manager). The two things coexist. `WarnCapture` covers the aggregate-inspection case ("give me all warnings emitted by this test so I can inspect them all"), `oxitest.warns()` covers the assertion case ("verify this specific warning was emitted"). Both patterns are valid.

**Verdict.** Right shape. Loguru's non-use of `WarnCapture` reflects loguru's test needs (assertion cases only), not a shape mismatch. Similar to `StdCapture` / `LogCapture`, having a helper alternative (`oxitest.warns`) for the block-scoped assertion case is the right ergonomic — and it already exists.

---

### 8. `TestContext` — VERDICT: right shape (dual-purpose)

**How it's shipped.** `@injectable class TestContext` in `_builtin_context.py`. Injected when parameter is annotated `TestContext`. Exposes:
- Read-only metadata: `name`, `module_path`, `node_id`, `param_id`, `marks`, `param`
- Imperative teardown: `addfinalizer(fn)`, `on_teardown(fn)` alias

**How it's typically used.** In the loguru migration: 0 uses. Migration uses `Yields[T]` fixture teardown pattern everywhere instead.

**Would the other shape work?** The identity metadata (`name`, `param_id`, etc.) can only come from the running session — helper-shape would defeat the point. The `addfinalizer` case is different — a helper could plausibly register a callback into the current session's teardown stack. But that's less ergonomic than `Yields[T]` for most cases.

**Verdict.** Right shape. Dual purpose (metadata + teardown registration) benefits from being one injected object. `Yields[T]` covers the common teardown case; `addfinalizer` covers the rare "register teardown inside a helper called from a test body" case that can't be expressed via `yield`. Loguru just doesn't have that pattern.

---

## Cross-cutting observations

### Pattern: "state-carrying fixture with imperative escape-hatch context manager"

Three of the eight built-ins ship this pattern:
- `StdCapture` → fixture-scoped capture + `.disabled()` context manager for pass-through
- `LogCapture` → fixture-scoped capture + `.at_level()` / `.set_level()` for narrowing
- (`FdCapture` mirrors `StdCapture`)

The pattern lets adopters get "capture the whole test by default" AND "narrow to a block for a specific operation" from one API. It's the right polarity for that use case.

**`Patcher` is missing this pattern.** It ships the fixture-scoped default but no block-scoped escape hatch. Adopters who want "patch narrowly for a block within a wider-patched test" must invent it themselves — as the loguru migration did with `patch_context()`.

### Pattern: "helper as sibling to fixture"

Two of the eight built-ins have a helper counterpart shipped in oxitest:
- `WarnCapture` fixture ↔ `oxitest.warns()` helper — different use cases (aggregate inspection vs. assertion)
- (`raises` is a helper without a fixture counterpart — assertion-only)

**`Patcher` is missing this pattern too.** No `oxitest.patch()` helper (or `oxitest.patch_context()`) that adopters can use inline for block-scoped patching. The loguru migration wrote its own; every other adopter with `monkeypatch.context()` will re-derive it.

### Pattern: "one built-in, one primary shape"

Six of the eight ship a clean single shape (`TempDir`, `TempDirFactory`, `FdCapture`, `LogCapture`, `WarnCapture`, `TestContext`) — no ambiguity, no shape mismatch.

The two exceptions (`StdCapture`, `LogCapture`) get away with dual-surface behavior because their escape-hatch methods (`disabled()`, `at_level()`) are complementary, not alternative — you use the fixture-scoped default AND the block-scoped narrowing together, not either/or.

`Patcher`'s alternative shape (patch_context) is NOT complementary — it fully substitutes for the fixture. That's the shape mismatch signal.

## Preliminary verdicts summary

| Built-in | Shape verdict | Confidence | Notes |
|---|---|---|---|
| `TempDir` | Right | High | Value-carrier, no imperative surface |
| `TempDirFactory` | Right | Medium | Loguru non-use is legit; a project needing multi-temp session state would exercise it |
| `StdCapture` | Right | High | Fixture default + `.disabled()` escape hatch is the right polarity |
| `FdCapture` | Right | Medium | Mirror of StdCapture; loguru non-use is legit |
| **`Patcher`** | **Wrong-shape-as-default; needs dual shape** | **High** | 41-to-0 loguru migration usage evidence; already filed as [#1696](https://github.com/kalonji-tools/oxitest/issues/1696) |
| `LogCapture` | Right | High | Fixture + `.at_level()` / `.set_level()` methods = right polarity; useful model for what Patcher lacks |
| `WarnCapture` | Right | High | Complemented by `oxitest.warns()` helper for assertion case; both shapes ship |
| `TestContext` | Right | Medium | Metadata is fixture-inherent; teardown case rare enough that `addfinalizer` is enough |

**Score: 7 right / 1 wrong.** The single misshapen built-in (Patcher) has an established real-world usage counter-example (loguru: 0 fixture uses vs 41 helper uses) and a proposed fix (dual shape + broader API surface).

## Deliverables for the grilling ticket ([#1700](https://github.com/kalonji-tools/oxitest/issues/1700))

Concrete evidence this audit contributes to the principle-formation grilling:

1. **The "escape-hatch context manager" pattern (`disabled()`, `at_level()`, `set_level()`) is what makes fixture-shaped-with-imperative-methods work.** Patcher's missing escape hatch is what forces adopters to invent `patch_context()`. Any principle for Fixture-vs-Helper should probably include: "if the built-in has imperative methods, does it also need block-scoped narrowing?"
2. **The "sibling helper" pattern (`oxitest.warns` alongside `WarnCapture`) is the alternative to the escape-hatch approach.** Both are valid. The principle should probably guide when each is better.
3. **Value-carriers (TempDir) belong to fixture unambiguously.** No design choice — passive attribute access requires an injected owner.
4. **Session-scoped built-ins (TempDirFactory) belong to fixture unambiguously.** Session semantics is a fixture concept.

The principle to grill toward: **"Fixture shape for state that must exist across the test's lifetime; helper shape for scoped operations. When both apply, ship both — either as separate names (like `WarnCapture` + `oxitest.warns()`) or as fixture-with-escape-hatch-methods (like `StdCapture.disabled()`, `LogCapture.at_level()`)."**

Patcher fails this principle: it's the only built-in with test-lifetime state AND a legitimate block-scoping need, but ships only the fixture shape without either escape-hatch methods or a sibling helper. The proposed fix (either shape) closes the gap and validates the principle.
