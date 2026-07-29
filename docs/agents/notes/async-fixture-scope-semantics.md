# Async fixture scope semantics — adjacent-frameworks survey

Feeds the async-fixture work under [ADR-0009](../../adr/0009-fixture-system-redesign.md)
Rule 2 (four lifetime tiers: `function` → `module` → `package` → `session`) and
[ADR-0006](../../adr/0006-async-organizational-strategy.md) (per-test-loop discipline,
loud rejection of the sync-test × function-scope-async-fixture cell).

Companion note: [`fixture-location-adjacent-frameworks.md`](fixture-location-adjacent-frameworks.md)
(the location survey — same house style, different axis).

---

## 1. Scope + method

Three questions were put to every framework surveyed:

| | Question |
|---|---|
| **Q1** | When a framework supports async setup/teardown **wider than one test**, does teardown fire **at the declared scope boundary**, or is it deferred to end of run? Where a framework *changed* its answer, what was the issue/PR and the stated reason? |
| **Q2** | Which **loop/runtime owns** a value made by a wider-than-test async fixture, and how is the **test body** guaranteed to run on that same one? One loop per test / per scope / per run? What happens when one test touches two scopes? Documented cross-loop failure modes? Eager or lazy resolution? |
| **Q3** | Are **implicit** (autouse / auto-applied) async fixtures restricted at non-function scopes? Which (async × scope × autouse) cells are outright rejected, and why? |

**Frameworks read:** pytest-asyncio, pytest core (the sync baseline), pytest-xdist,
anyio, pytest-trio + trio, Vitest, Jest, JUnit 5, xUnit.net, Go `testing`,
Rust `rstest` + `tokio::test` (+ `test-context`, `serial_test`, `libtest-mimic`).

**All sources checked 2026-07-29.** Every version number below was verified against
the repository, changelog, or release notes at time of reading — none is from memory.
Where prose docs and source disagree, the source is reported and the conflict is
flagged explicitly.

**Bias in what follows:** where a framework *changed* its answer, the change and its
stated rationale get more space than the current state. pytest-asyncio's
0.21 → 0.23 → 0.24 arc is the single richest source in the survey and is treated at
length; several frameworks turn out to have **no answer at all**, which is recorded as
a finding rather than omitted.

---

## 2. The one-slide summary

```
                       Q1 teardown at         Q2 who owns          Q3 async×wide×
                       scope boundary?        the loop?            autouse rejected?
                   ┌──────────────────────┬───────────────────┬───────────────────┐
 pytest core       │ YES — by lookahead   │ n/a (sync only)   │ HARD ERROR, and   │
   (sync baseline) │ at the NEXT item     │                   │ msg NAMES autouse │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 pytest-asyncio    │ YES (fixture scope)  │ SEPARATE AXIS     │ NO — autouse not  │
                   │ clamped to loop life │ loop_scope ≥ scope│ in the docs AT ALL│
                   ├──────────────────────┼───────────────────┼───────────────────┤
 anyio plugin      │ YES — pytest drives; │ ONE global runner,│ NO — degrades     │
                   │ but 2 bug fixes here │ refcounted lease  │ SILENTLY instead  │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 pytest-trio       │ n/a — wider scopes   │ one trio.run()    │ ALL wider scopes  │
                   │ don't exist          │ PER TEST; LAZY    │ rejected, always  │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 Jest              │ YES (src-verified,   │ 1 loop per worker │ no such concept — │
                   │ NOT documented)      │ globalSetup value │ hooks are the only│
                   │                      │ CANNOT reach tests│ mode; lint only   │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 Vitest            │ YES (documented per  │ same, + provide/  │ lazy by default,  │
                   │ hook); order is a    │ inject channel    │ {auto:true} opt-in│
                   │ config knob          │                   │ + scope hierarchy │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 JUnit 5           │ YES — store closes   │ no loop; thread   │ n/a (no async in  │
                   │ with its context;LIFO│ confinement       │ Java at all)      │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 xUnit.net         │ YES — awaited; nest  │ no loop; AsyncLoc.│ NO — but assembly │
                   │ enforced by callstack│ TestContext       │ tier warns re ∥   │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 Go testing        │ YES, by barrier join │ NO SUCH QUESTION  │ no fixtures to    │
                   │ (t.Cleanup)          │ (goroutines)      │ restrict          │
                   ├──────────────────────┼───────────────────┼───────────────────┤
 Rust rstest       │ NO TEARDOWN AT ALL   │ per-test runtime  │ HARD COMPILE      │
                   │ (#[once] never drops)│ (tokio::test)     │ ERROR: async×once │
                   └──────────────────────┴───────────────────┴───────────────────┘
```

**Three headlines.**

1. **No framework defers scope-boundary teardown on purpose.** Every deferral in the record is a
   *bug*, and every one of those bugs is the same bug: the loop was already dead (§6.2).
2. **"Same loop" turned out not to be enough.** anyio escalated twice — 2.0 "same loop" → 3.6/4.0
   "same **task**" (a breaking change) — and pytest-asyncio still has the task-level bug open (§6.3).
3. **The fixture-scope / loop-scope coupling is the single most expensive mistake in the survey.**
   pytest-asyncio shipped it in 0.23 and spent eight months telling users to downgrade, *in its own
   changelog*, before splitting them into independent axes in 0.24 (§4).

---

## 3. Q1 — Teardown timing

> Does async teardown fire **at the declared scope boundary**, or is it deferred?

### pytest core — the sync baseline pytest-asyncio has to fit into

pytest fires teardown **eagerly at the scope boundary**, and the boundary is computed by
*lookahead at the next item*, not from a pre-computed plan. The mechanism is `SetupState`
in [`src/_pytest/runner.py`](https://github.com/pytest-dev/pytest/blob/main/src/_pytest/runner.py).
Its class docstring is the clearest statement of the model:

> During the teardown phase of item1, `teardown_exact(item2)` is called, where item2 is the next
> item to item1. What it does is:
>
>     pop item1 from stack, run its teardowns
>     pop mod1 from stack, run its teardowns
>
> mod1 was popped because it ended its purpose with item1.

`teardown_exact` compares the live stack against the *next* item's collector chain and pops
everything the next item does not also need:

```python
needed_collectors = (nextitem and nextitem.listchain()) or []
while self.stack:
    if list(self.stack.keys()) == needed_collectors[: len(self.stack)]:
        break
    node, (finalizers, _) = self.stack.popitem()
    while finalizers:
        fin = finalizers.pop()      # LIFO within a node
```

Two ordering guarantees fall out, both confirmed in the
[fixtures how-to](https://docs.pytest.org/en/stable/how-to/fixtures.html):

- **Innermost first.** `popitem()` on an insertion-ordered dict pops the most recently
  pushed collector — item before module before session.
- **LIFO within a node.** `finalizers.pop()`. Docs: *"Finalizers are executed in a
  first-in-last-out order."*

And the `package` tier is defined there in exactly the terms ADR-0009 Rule 2 uses:

> `package`: the fixture is destroyed during teardown of the last test in the package where
> the fixture is defined, **including sub-packages and sub-directories within it**.

**Consequence worth carrying:** because the boundary is decided by lookahead, *the teardown
point depends on test execution order*. Reordering or sharding tests moves when a
module/package fixture is torn down.

### pytest-asyncio

Teardown fires at the **fixture's caching scope** boundary (pytest's normal `SetupState` path),
but the coroutine driving it runs on the **loop-scope** loop — and there is an explicit clamp
so the fixture can never be torn down *after* its loop dies.
[`pytest_asyncio/plugin.py`](https://github.com/pytest-dev/pytest-asyncio/blob/main/pytest_asyncio/plugin.py),
`pytest_fixture_setup`:

```python
runner_fixture_id = f"_{loop_scope}_scoped_runner"
runner = request.getfixturevalue(runner_fixture_id)
# Prevent the runner closing before the fixture's async teardown.
runner_fixturedef = request._get_active_fixturedef(runner_fixture_id)
runner_fixturedef.addfinalizer(
    functools.partial(fixturedef.finish, request=request)
)
```

So the effective rule is **teardown at `min(fixture-scope boundary, loop-scope boundary)`**,
with the second term normally unreachable because `loop_scope ≥ scope` is a documented
invariant (see Q2).

The async half is driven by `_wrap_asyncgen_fixture`, which registers an ordinary pytest
finalizer that re-enters the generator on the scoped runner:

```python
def finalizer() -> None:
    async def async_finalizer() -> None:
        try:
            await gen_obj.__anext__()
        except StopAsyncIteration:
            pass
        else:
            raise ValueError("Async generator fixture didn't stop.Yield only once.")
    runner.run(async_finalizer(), context=context)
request.addfinalizer(finalizer)
```

**It has not always worked.** The clamp exists because it did not. From the
[changelog](https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/changelog.rst):

| Version | Date | Entry |
|---|---|---|
| 0.23.3 | 2024-01-01 | *"Fixes a bug that caused **event loops to be closed prematurely** when using async generator fixtures with class scope or wider in a function-scoped test"* ([#706](https://github.com/pytest-dev/pytest-asyncio/issues/706)) |
| 0.25.1 | 2025-01-02 | *"Fixes an issue that caused a **broken event loop** when a function-scoped test was executed **in between two tests with wider loop scope**"* ([#950](https://github.com/pytest-dev/pytest-asyncio/issues/950)) |
| 0.25.2 | 2025-01-08 | *"Call `loop.shutdown_asyncgens()` before closing the event loop to ensure async generators are closed in the same manner as `asyncio.run` does"* ([#1034](https://github.com/pytest-dev/pytest-asyncio/pull/1034)) |
| 0.25.3 | 2025-01-28 | *"Avoid errors in cleanup of async generators when event loop is already closed"* ([#1040](https://github.com/pytest-dev/pytest-asyncio/issues/1040)) |
| 1.1.0 | 2025-07-16 | *"**Cancellation of tasks when the `loop_scope` ends**"* ([#200](https://github.com/pytest-dev/pytest-asyncio/issues/200)); *"Warning when the current event loop is closed by a test"* |

Note the shape of that list: four consecutive patch releases about teardown running against a
**dead loop**. This is the dominant failure mode of the "wider-than-test async fixture"
feature, not an edge case.

The surviving guard is a warning, not an error — `_RUNNER_TEARDOWN_WARNING` in `plugin.py`:

> An exception occurred during teardown of an `asyncio.Runner`. The reason is likely that you
> closed the underlying event loop in a test, which prevents the cleanup of asynchronous
> generators by the runner. **This warning will become an error in future versions of
> pytest-asyncio.**

Live user reports of exactly this shape are still open/recent: [#1200](https://github.com/pytest-dev/pytest-asyncio/issues/1200)
(session-scoped fixture, `RuntimeError: Event loop is closed` in the post-`yield` half, closed
for want of a reproducer) and [#1083](https://github.com/pytest-dev/pytest-asyncio/issues/1083)
(`TaskGroup` wrapped around the `yield` **hangs indefinitely** instead of unwinding — still open,
filed 2025-03-25).

### anyio

Teardown fires **at the pytest scope boundary** — anyio does not own fixture ordering at all,
pytest does. anyio only wraps the fixture function so both halves run inside a test runner.
[`src/anyio/pytest_plugin.py`](https://github.com/agronholm/anyio/blob/master/src/anyio/pytest_plugin.py),
`pytest_fixture_setup` → `wrapper`:

```python
with get_runner(backend_name, backend_options) as runner:
    ...
    if isasyncgenfunction(local_func):
        yield from runner.run_asyncgen_fixture(local_func, kwargs)
    else:
        yield runner.run_fixture(local_func, kwargs)
```

Because `with get_runner(...)` encloses the `yield`, a module-scoped async fixture holds its
runner **lease** suspended across the whole module. What is deferred is the *loop*, not the
teardown — see Q2.

**This is where anyio's bugs live.** Two of the changelog's wider-scope teardown fixes, verbatim
from [`docs/versionhistory.rst`](https://github.com/agronholm/anyio/blob/master/docs/versionhistory.rst):

| Version | Entry |
|---|---|
| 4.1.0 (2023-11-22) | *"Fixed `RuntimeError: Runner is closed` when running **higher-scoped async generator fixtures** in some cases"* ([#619](https://github.com/agronholm/anyio/issues/619)) |
| 4.14.1 (2026-06-24) | *"Fixed **teardown of higher-scoped async fixtures** failing on asyncio with `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in` when an async test raise an outcome exception (e.g., `pytest.skip()`, `pytest.xfail()`, or `pytest.fail()`)"* |

Two-and-a-half years apart, same class of bug, still landing in the current stable release.

The enabling changes were also breaking ones, and their stated reasons are instructive:

| Version | Entry |
|---|---|
| 2.0.0 | *"The pytest plugin was refactored to run the test and all its related async fixtures **inside the same event loop**"* |
| 3.6.0 | *"Changed the pytest plugin to run both the setup and teardown phases of asynchronous generator fixtures **within a single task**"* |
| 4.0.0 | *"**BACKWARDS INCOMPATIBLE** Changes the pytest plugin to run all tests and fixtures **in the same task**, allowing fixtures to set context variables for tests and other fixtures"* |

"Same loop" was not enough; anyio had to escalate to "same **task**" — twice — for cancel scopes
and contextvars to survive the `yield`.

### pytest-trio and trio

**No answer exists, because the question is unreachable.** pytest-trio rejects every
non-function-scoped trio fixture outright (see Q3), so there is no wider boundary for teardown
to fire at. All teardown happens inside the single `trio.run()` for that one test.

The one timing subtlety pytest-trio does document is the *opposite* problem — teardown that
never runs at all. From [`docs/source/reference.rst`](https://github.com/python-trio/pytest-trio/blob/master/docs/source/reference.rst),
"An important note about `yield` fixtures":

> Now, here's the punchline: this means that in our examples above, **the teardown code might not
> be executed at all!** *This is different from how pytest fixtures normally work.* Normally, the
> `yield` in a pytest fixture never raises an exception, so you can be certain that any code you
> put after it will execute as normal. But if you have a fixture with background tasks, and they
> crash, then your `yield` might raise an exception, and Python will skip executing the code after
> the `yield`.

Deliberate, per `docs/source/history.rst` **0.6.0**: *"if you use `yield` inside a Trio fixture,
and the `yield` gets cancelled (for example, due to a background task crashing), then the `yield`
will now raise `trio.Cancelled`."* Rationale: *"In our experience, most fixtures are fine with
this, and it prevents some weird problems that can happen otherwise."*

### Jest

Teardown fires **at the declared scope boundary and is awaited**. Verified in
[`packages/jest-circus/src/run.ts`](https://github.com/jestjs/jest/blob/main/packages/jest-circus/src/run.ts),
`_runTestsForDescribeBlock` — `afterAll` for a nested `describe` runs when that `describe`
finishes, not deferred to end of file:

```js
for (const hook of afterAll) {
  await _callCircusHook({describeBlock, hook});
}
```

The [docs](https://jestjs.io/docs/setup-teardown) state ordering but **never state the awaiting
guarantee for `beforeAll`/`afterAll`** — it is only readable in source. They do state:

> Jest calls the `before*` and `after*` hooks in the order of declaration. Note that the
> `after*` hooks of the enclosing scope are called first.

**Changed, with a stated rationale.** [jest#6401](https://github.com/jestjs/jest/issues/6401)
("afterAll times out silently", 2018) — *"the test completes successfully, but no warning or
message is given that the `afterAll` block timed out and wasn't able to finish."* Maintainer
`thymikee`: *"We're probably not gonna fix this in Jasmine, but this is already addressed by
`jest-circus`, our future test runner."* The residual warning still in the docs:
*"If you are using `jasmine2` test runner, take into account that it calls the `after*` hooks in
the reverse order of declaration."*

**Two gaps.** (1) A rejecting `afterAll` does **not** fail the tests — explicit source comment
in `_runTest`: *"`afterAll` hooks should not affect test status (pass or fail), because if we had
a global `afterAll` hook it would block all existing tests until this hook is executed."*
(2) **SIGINT skips all teardown** — [jest#12259](https://github.com/jestjs/jest/issues/12259),
opened 2022-01-20, **closed 2023-10-31 as NOT_PLANNED** by the stale bot with no maintainer reply,
despite the reporter arguing *"it relates to fundamental guarantees of the framework."*

`globalTeardown` **is** awaited, before results are processed, so `--forceExit` does not skip it
(`packages/jest-core/src/runJest.ts`). But it is **not in a `try/finally`** — if test scheduling
rejects, it is skipped.

### Vitest

Same boundary semantics as Jest, but **the awaiting guarantee is documented per hook** rather
than left to source. From [the hooks API](https://vitest.dev/api/hooks):

> `afterAll` — Register a callback to be called once after all tests have run in the current
> suite. **If the function returns a promise, Vitest waits until the promise resolve before
> continuing.**

Vitest adds two things pytest-adjacent designs should notice:

**1. `beforeAll` may return its own teardown**, with a defined position in the order:

> `beforeAll` can also return an optional cleanup function. It's similar to `afterAll`. The only
> difference is that **it's executed after all other `afterAll` hooks**.

This is a real footgun on migration — an implicit-return arrow silently becomes a teardown.
The [migration guide](https://vitest.dev/guide/migration#hooks) calls it out:
`beforeEach(() => setActivePinia(createTestingPinia()))` ✗ vs
`beforeEach(() => { setActivePinia(createTestingPinia()) })` ✓.

**2. Hook order is configurable** — [`sequence.hooks`](https://vitest.dev/config/sequence#sequence-hooks),
`'stack' | 'list' | 'parallel'`, default `'stack'`:

| Value | Meaning |
|---|---|
| `stack` (default) | "after" hooks in reverse order; "before" hooks in definition order — LIFO unwinding |
| `list` | all hooks in definition order — **this is Jest's behaviour** |
| `parallel` | hooks in a single group run concurrently, bounded by `maxConcurrency`; parent-suite hooks still precede the current suite's |

**Changed, with a stated rationale — and the docs lagged 7 months.** The default used to be
`parallel`. [vitest#5599](https://github.com/vitest-dev/vitest/issues/5599) (2024-04-23) reported
that two async `beforeEach`es at the same scope overlap; the maintainer closed it as expected
behaviour, quoting the then-current guide: *"By default, Vitest runs hooks in parallel."*
[PR #5609 `feat!: run suite hooks in a stack`](https://github.com/vitest-dev/vitest/pull/5609)
merged 2024-05-06 and shipped in
[v2.0.0](https://github.com/vitest-dev/vitest/releases/tag/v2.0.0) (2024-07-08) with the reason:

> This feels like a more sensible default. Especially with the new `onTestFinished` hook. **This
> can make your tests run a little bit slower.**

[PR #7492](https://github.com/vitest-dev/vitest/pull/7492) (merged 2025-02-14) fixed the docs:
*"The docs haven't been updated to reflect Vitest 2 breaking change of #5609."* Any secondary
source on Vitest hook ordering dated mid-2024 to Feb-2025 states the old default.

Vitest also **hardened hook-failure reporting over time** — PR #2737 *"call afterAll, if
beforeAll failed"* (2023-01-23), PR #4799 *"mark tests as failed when beforeAll/afterAll failed"*
(2023-12-23). That is the opposite choice from Jest's explicit decision not to let `afterAll`
affect test status. And by default
[`dangerouslyIgnoreUnhandledErrors`](https://vitest.dev/config/dangerouslyignoreunhandlederrors)
is `false`, so an unhandled rejection escaping `afterAll` fails the run.

`globalSetup` teardown is **reverse order, independent of `sequence.hooks`**:
*"Multiple global setup files are possible. `setup` and `teardown` are executed sequentially with
teardown in reverse order."*

### JUnit 5 / Jupiter

**Java has no async lifecycle at all — and this is enforced, not merely unsupported.** A
`@BeforeAll` returning `CompletableFuture` is a **discovery-time ERROR**, not an un-awaited value.
`junit-jupiter-engine/.../descriptor/LifecycleMethodUtils.java`, `returnsPrimitiveVoid` (tag
`r6.1.2`):

```java
return issueReporter.createReportingCondition(method -> getReturnType(method) == void.class, method -> {
    String message = "@%s method '%s' must not return a value.".formatted(...);
    return createIssue(Severity.ERROR, message, method);
});
```

Corroborated by the [User Guide](https://docs.junit.org/current/user-guide/) §2.3: *"test methods
and lifecycle methods must not be abstract and **must not return a value**."* The one closed
proposal in this space ([#444](https://github.com/junit-team/junit-framework/issues/444),
"Introduce mechanism for asynchronous tests to signal test completion") shipped no such mechanism
for Java, and no open issue proposes async Java lifecycle methods.

**But JUnit's disposal model is the most directly relevant thing in the survey for ADR-0009**,
because it ties disposal to *the scope's context object* rather than to a scope keyword.
`ExtensionContext.getStore(Namespace)` Javadoc:

> A store is **bound to its extension context lifecycle**. When an extension context lifecycle ends
> it **closes its associated store**. All stored values that are instances of
> `ExtensionContext.Store.CloseableResource` are notified by invoking their `close()` methods.

Stores are hierarchical, and the tier you get is the context you chose:

> Stores are hierarchical in nature. When looking up a value, if no value is stored in the current
> `ExtensionContext` for the supplied key, the stores of the context's **ancestors** will be
> queried […]. **The root `ExtensionContext` represents the engine level so its `Store` may be used
> to store or cache values that are used by multiple test classes.**

with the isolation direction stated explicitly in the 5.13.4 wording: *"values stored in an
`ExtensionContext` during test execution **will not be available in the surrounding
`ExtensionContext`**."*

**LIFO is documented twice** — User Guide: *"closed […] **in the inverse order they were added
in**"*; `CloseableResource` Javadoc: *"The resources stored in a `Store` are closed in the inverse
order they were added in."*

**Changed, with a stated rationale — `CloseableResource` → `AutoCloseable` in 5.13.** Release note:

> By default, `AutoCloseable` objects put into `ExtensionContext.Store` are now treated like
> instances of `CloseableResource` (which has been deprecated) and are closed automatically when
> the store is closed at the end of the test lifecycle.

The driving issue,
[#4434](https://github.com/junit-team/junit-framework/issues/4434), gives the reason and one
excellent design lesson:

> only instances of `CloseableResource` […] are closed automatically. However, items stored in the
> session-/request-level stores only need to implement `AutoCloseable`. **To address this confusing
> difference in behavior**, we should introduce a configuration parameter […]
>
> ~~Make `CloseableResource` extend `AutoCloseable` so existing implementations would keep
> working~~ — **Doesn't work because `CloseableResource#close` is allowed to throw `Throwable`, not
> just `Exception`.**

Opt-out flag `junit.jupiter.extensions.store.close.autocloseable.enabled`, default `true`
(verified in `DefaultJupiterConfiguration.java`: `.orElse(true)`). `CloseableResource` is still
only *deprecated* in 6.1.2, not removed.

5.13 also added explicit wider-than-root tiers: `ExtensionContext.getStore(StoreScope, Namespace)`
with `StoreScope ∈ {LAUNCHER_SESSION, EXECUTION_REQUEST, EXTENSION_CONTEXT}`, where
`LAUNCHER_SESSION` can *"share data across multiple engines."*

### xUnit.net

**Teardown is awaited at the declared scope boundary — source-verified, and the nesting is
enforced by the call stack rather than by convention.** `src/xunit.v3.core/Runners/XunitTestClassRunnerBase.cs`:

```csharp
protected override async ValueTask<bool> OnTestClassFinished(TContext ctxt, RunSummary summary)
{
    await ctxt.Aggregator.RunAsync(ctxt.ClassFixtureMappings.DisposeAsync);
    return await base.OnTestClassFinished(ctxt, summary);
}
```

with the exact mirror in `XunitTestCollectionRunnerBase.OnTestCollectionFinished`. Because
`TestCollectionRunner.Run` awaits `RunTestClasses` *before* `OnTestCollectionFinished`, and each
`TestClassRunner.Run` awaits `RunTestMethods` before `OnTestClassFinished`, the ordering
**assembly ⊃ collection ⊃ class ⊃ test-instance** is structural. The docs state the same lifetime
in prose:

> the lifetime of a collection fixture object is longer: it is created before any tests are run in
> any of the test classes in the collection, and **will not be cleaned up until all test classes in
> the collection have finished running.**

The tiers map onto oxitest's ladder better than anything else surveyed. From
`src/xunit.v3.core/IAsyncLifetime.cs`:

```csharp
/// Used to provide asynchronous lifetime functionality. Currently supported:
/// - Test classes
/// - Classes used in IClassFixture<TFixture>
/// - Classes used in ICollectionFixture<TFixture>
/// - Classes used in [assembly: AssemblyFixtureAttribute()]
public interface IAsyncLifetime : IAsyncDisposable
{
    ValueTask InitializeAsync();
}
```

**Changed twice, with rationale.** The [v3 migration doc](https://xunit.net/docs/getting-started/v3/migration):

> In v2, `IAsyncLifetime` defined its own `DisposeAsync` method, and if you implemented both
> `IAsyncLifetime` and `IDisposable`, we would call **both** `DisposeAsync` and `Dispose`.
>
> In v3, `IAsyncLifetime` now inherits `IAsyncDisposable` […] **We are also now following framework
> guidance which says that when an object implements both `IAsyncDisposable` and `IDisposable`, you
> should only call one or the other, and not both. For xUnit.net, that means it will call
> `DisposeAsync` but not `Dispose`.** […] **This could be a breaking change if you were previously
> relying on us calling both.**

And a second change visible **only in source**, undocumented: **v2 disposed fixtures concurrently,
v3 disposes them sequentially.** v2's `BeforeTestClassFinishedAsync`:

```csharp
var disposeAsyncTasks = ClassFixtureMappings.Values.OfType<IAsyncLifetime>()
    .Select(fixture => Aggregator.RunAsync(fixture.DisposeAsync)).ToList();
await Task.WhenAll(disposeAsyncTasks);          // v2: all in parallel
```

v3's `FixtureMappingManager.DisposeAsync` is a sequential `foreach … await`, and
`IAsyncDisposable` wins over `IDisposable` via `else if`.

> ⚠️ **xUnit has no documented disposal *order* within a scope.** v3 iterates
> `fixtureCache.Values` in insertion order. This is the one place JUnit is strictly stronger —
> JUnit documents LIFO twice; xUnit documents nothing. It is at least consistent with the docs'
> other statement: *"you cannot control the order that fixture objects are created."*

### Go

Go has no fixtures, but it has the most rigorously-specified *per-test-tree* teardown in the
survey, and a notably unstructured *package-level* one.

**`t.Cleanup` — LIFO, and it waits for subtests.** Doc comment on `func (c *common) Cleanup`
in [`src/testing/testing.go`](https://github.com/golang/go/blob/master/src/testing/testing.go):

> Cleanup registers a function to be called when the test (or subtest) **and all its subtests
> complete**. Cleanup functions will be called in last added, first called order.

The nesting guarantee is a **barrier + signal join, not a scheduler policy**. In `tRunner`:

```go
if len(t.sub) > 0 {
    close(t.barrier)
    for _, sub := range t.sub {          // wait for every parallel subtest
        <-sub.signal
    }
    running.Store(t.name, cleanupStart)
    err := t.runCleanup(recoverAndReturnPanic)
```

Each nesting level owns its own `cleanups` slice on its own `common`, so "cleanup at the right
level" falls out of the tree structure — a parent physically cannot run its cleanups before its
children have signalled. Cleanups run even on `t.FailNow`/panic, and a panicking cleanup does not
lose the remaining ones (*"Make sure that if a cleanup function panics, we still run the
remaining cleanup functions."*). You cannot grow the tree during teardown:
`"testing: t.Run called during t.Cleanup"`.

**`TestMain` is the package tier, and it is *not* a cleanup registry.** `M` has exactly one
exported method, `Run`. Teardown is "statements after `m.Run()` returns" — no LIFO, no
panic-safety, no registration API. And `os.Exit` *"terminates immediately; deferred functions are
not run"*, so the idiomatic `defer teardown(); os.Exit(m.Run())` **silently skips teardown**.

**Changed, with a stated rationale.** [Go 1.15 release notes](https://go.dev/doc/go1.15):

> A `TestMain` function is no longer required to call `os.Exit`. If a `TestMain` function returns,
> the test binary will call `os.Exit` with the value returned by `m.Run`.

That is the fix for the footgun: return instead of exiting, and your `defer`s run.

### Rust

**`rstest` has no teardown concept at all.** Grepping `rstest/src/lib.rs`, `README.md` and
`CHANGELOG.md` for `teardown` returns **zero matches** (rstest 0.26.1, checked 2026-07-29).
Per-test fixtures are cleaned up only by ordinary `Drop`.

For the one wider-than-test scope, `#[once]`, the docs are blunt
([docs.rs/rstest](https://docs.rs/rstest/latest/rstest/)):

> Take care that the `#[once]` fixture value will **never be dropped**.

This is not policy, it is the lowering. `rstest_macros/src/render/fixture.rs`,
`wrap_call_impl_with_call_once_impl`:

```rust
static CELL: #std::sync::OnceLock<#t> = #std::sync::OnceLock::new();
CELL.get_or_init(|| #call_impl )
```

Rust does not run destructors for `static`s at process exit. **`#[once]` is a memoised leak with
no teardown half.**

### Q1 comparison table

| Framework | Wider-than-test async scope exists? | Teardown at declared boundary? | Awaited? | Ordering | Notable historical change |
|---|---|---|---|---|---|
| **pytest core** (sync) | n/a — sync only | **Yes**, by lookahead at next item | n/a | innermost-first, LIFO within a node | — |
| **pytest-asyncio** | Yes (`scope` × `loop_scope`) | **Yes**, at fixture `scope`; clamped to loop life by an explicit finalizer on the runner | Yes, via `Runner.run` | inherits pytest | 4 consecutive patch releases (0.23.3 → 0.25.3) fixing teardown against a **dead loop**; task cancellation at loop-scope end added 1.1.0 |
| **anyio** | Yes | **Yes**, pytest drives it; loop lifetime is a separate refcount | Yes | inherits pytest | 2.0 "same loop" → 3.6 "same task (asyncgen)" → 4.0 "same task (everything)", *breaking*; wider-scope teardown bugs still landing in 4.14.1 |
| **pytest-trio** | **No — rejected** | n/a | n/a | one `trio.run()` per test | 0.6.0 made a cancelled `yield` raise `trio.Cancelled` → **post-`yield` teardown may not run at all** |
| **Jest** | Yes (`beforeAll`/`afterAll`, `globalSetup`) | **Yes** (source-verified, *not* documented) | Yes | declaration order; enclosing scope's `after*` last | jasmine2 → circus fixed silently-swallowed `afterAll` timeouts (#6401) |
| **Vitest** | Yes | **Yes** (documented per hook) | Yes | configurable: `sequence.hooks` | default flipped `parallel` → `stack` in **v2.0.0** (2024-07-08); docs lagged 7 months |
| **JUnit 5** | **No async lifecycle in Java** (returning a value is a discovery ERROR). Sync disposal tied to `ExtensionContext.Store` | **Yes** — store closes when its context's lifecycle ends | n/a | **LIFO, documented twice** | 5.13: `CloseableResource` deprecated in favour of plain `AutoCloseable`, on by default, opt-out flag |
| **xUnit.net** | **Yes** — `IAsyncLifetime` on class / collection / assembly fixtures | **Yes**, awaited in `OnTest{Class,Collection}Finished`; nesting enforced by the call stack | Yes (`ValueTask`) | **none documented** — insertion order | v2→v3: `IAsyncLifetime : IAsyncDisposable`; v3 calls `DisposeAsync` **but not** `Dispose` ("following framework guidance"). Also undocumented: v2 `Task.WhenAll` → v3 sequential |
| **Go** | n/a (no async) | **Yes** — barrier + signal join per nesting level | n/a | LIFO, panic-safe | Go 1.15: `TestMain` no longer *required* to call `os.Exit` — fixes silently-skipped teardown |
| **Rust `rstest`** | `#[once]` only | **NO TEARDOWN AT ALL** | n/a | n/a | `#[once]` added 0.12.0; 0.19.0 added `Sync` bound for parallel-run soundness |

**Two frameworks failed loudly here and changed their answer; one never had one.** Nobody in
the survey defers scope-boundary teardown to end-of-run *by design* — every deferral found was a
**bug**, and in every case the bug was "the loop/runtime was already dead."

---

## 4. Q2 — Event loop / runtime ownership

> Who owns the loop a wider-than-test async value was made on, and how does the test body get on it?

### The three architectures found

```
 A. SEPARATE AXIS (pytest-asyncio)
    fixture caching scope  ──┐
                             ├──►  loop chosen by loop_scope, INDEPENDENT of scope
    loop scope             ──┘      invariant: loop_scope ≥ scope
    ┌──────────── session loop ────────────┐
    │  [session fx]   [module fx]  [test]  │   all can share ONE loop
    └──────────────────────────────────────┘

 B. REFCOUNTED LEASE (anyio)
    ONE global runner. Every async fixture/test takes a lease.
    Loop is created on lease 0→1 and destroyed on lease 1→0.
    lease:  0 →1───────────────2─────3────2────1────0
                 ▲                                  ▲
            session fx creates loop        last lease drops, loop dies

 C. ONE RUN PER TEST, WIDER SCOPES FORBIDDEN (pytest-trio, tokio::test, rstest)
    trio.run(  [fx setup] → [test body] → [fx teardown]  )   ← per test
    nothing may outlive the run; wider scope is a compile/collect error
```

### pytest-asyncio — architecture A, loop scope is a **separate axis** from caching scope

This is the survey's most-argued design, and it exists *because* the naive coupling failed in
public.

**What v0.23 did (Dec 2023).** From the `0.23.0` changelog: event loops for class/module/package/
session became requestable via the `asyncio` mark. But fixture scope was silently welded to loop
scope. The maintainer's own diagnosis in
[#706](https://github.com/pytest-dev/pytest-asyncio/issues/706) (seifertm):

> pytest-asyncio **falsely assumes that the scope of a fixture is tied to the scope of the event
> loop in which it should run**. This results in fixtures to run in a different loop than tests
> and breaks the pytest run.

and:

> There's currently **no way to control the event loop used by a fixture independently from the
> fixture scope**. […] The v0.23 release will not work for your test suite. I suggest that you
> downgrade to v0.21.1.

**The fallout was severe enough to be documented in the changelog itself.** Every release from
0.23.3 through 0.23.8 shipped an identical `Known issues` block:

> As of v0.23, pytest-asyncio attaches an asyncio event loop to each item of the test suite […]
> Pytest-asyncio currently assumes that async fixture scope is correlated with the new event loop
> scope. This prevents fixtures from being evaluated independently from the event loop scope and
> **breaks some existing test suites** (see #706). […] **If you're affected by this issue, please
> continue using the v0.21 release, until it is resolved.**

Shipping "use the previous major for eight months" in your own changelog is about as loud as
this failure gets.

**The exact cross-scope collision oxitest asks about is in that thread.** minrk's report — a
module-scoped async fixture plus a function-scoped async fixture in the same test:

```python
@pytest.fixture(scope="module")
async def app():
    app.event_loop = asyncio.get_running_loop(); yield app

@pytest.fixture(scope="function")
async def app_plugin(app):
    yield
    assert app.event_loop.is_running()          # FAILS

@pytest.mark.asyncio(scope="module")
async def test_loops(app, app_plugin):
    assert app.event_loop is asyncio.get_running_loop()   # FAILS
```

> Both asserts fail because both the function-scoped fixture (expected) **and the test
> (unexpected)** run with the function_scoped event loop — `@pytest.mark.asyncio(scope="module")`
> **is ignored on the test due to the inclusion of the function-scoped fixture**.

That is the failure mode in its purest form: *mixing two fixture scopes in one test silently
demoted the test's own declared loop scope*. Another participant asked the design question
directly — *"Let's say that we have a test with 2 async fixtures with different loop_scope. Should
I expect pytest-asyncio to raise an error?"* — and the answer shipped was **no error; make the
axes independent instead.**

**A rejection *was* tried, and then removed.** v0.23 shipped a dedicated exception for the
two-loops-in-one-test case. From `plugin.py` at tag `v0.23.8`:

```python
class MultipleEventLoopsRequestedError(PytestAsyncioError):
    """Raised when a test requests multiple asyncio event loops."""

_MULTIPLE_LOOPS_REQUESTED_ERROR = dedent("""\
        Multiple asyncio event loops with different scopes have been requested
        by {test_name}. The test explicitly requests the event_loop fixture, while
        another event loop with {scope} scope is provided by {scoped_loop_node}.
        Remove "event_loop" from the requested fixture in your test to run the test
        in a {scope}-scoped event loop or remove the scope argument from the "asyncio"
        mark to run the test in a function-scoped event loop.
    """)
```

**It no longer exists** — zero occurrences in current `plugin.py`. It went away with the
`event_loop` fixture it policed (removed in 1.0.0, [#1106](https://github.com/pytest-dev/pytest-asyncio/issues/1106)).
Note *what* it rejected: not "two async fixtures at different scopes," but "an explicit
`event_loop` request colliding with a scoped loop." **The genuinely ambiguous case — two async
fixtures with different loop scopes in one test — was never rejected at all.** It was made
*expressible* instead, via the `loop_scope ≥ scope` triangle below.

**The fix (0.24.0, 2024-08-22).** `loop_scope` added to both `pytest.mark.asyncio` and
`pytest_asyncio.fixture`, plus the `asyncio_default_fixture_loop_scope` ini option.
The rule is stated in
[`docs/reference/decorators/index.rst`](https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/decorators/index.rst)
and is the single most quotable sentence in the survey:

> The *loop_scope* of a fixture can be chosen **independently** from its caching *scope*.
> However, **the event loop scope must be larger or the same as the fixture's caching scope**.
> In other words, it's possible to **reevaluate an async fixture multiple times within the same
> event loop, but it's not possible to switch out the running event loop in an async fixture.**

So the legal region is a triangle, not a square:

```
                       loop_scope →
              function  class  module  package  session
   function  │   ok      ok      ok      ok       ok
   class     │   ✗       ok      ok      ok       ok
 s module    │   ✗       ✗       ok      ok       ok
 c package   │   ✗       ✗       ✗       ok       ok
 o session   │   ✗       ✗       ✗       ✗        ok
 p            (✗ = would require switching loops mid-fixture)
 e
```

**How the invariant is enforced.** Not by an explicit check — I found none in `plugin.py`. It
falls out of pytest's own machinery: the async fixture *dynamically requests* the runner fixture
for its loop scope, and pytest's `SubRequest._check_scope`
([`src/_pytest/fixtures.py`](https://github.com/pytest-dev/pytest/blob/main/src/_pytest/fixtures.py))
fails on `self._scope > requested_scope` with `ScopeMismatch`. Both halves of that chain are
source-verified; I did not run the combination to confirm the resulting message text.

**Eager, from sync code.** Resolution happens during pytest's ordinary synchronous fixture-setup
phase, by entering the runner from outside any loop — `_wrap_asyncgen_fixture`:

```python
context = contextvars.copy_context()
result = runner.run(setup(), context=context)     # eager, from sync setup
```

The test body is put on the *same* runner in `PytestAsyncioFunction.runtest()`, by
monkeypatching the test callable into a sync wrapper before pytest calls it:

```python
runner = self._request.getfixturevalue(f"_{self._loop_scope}_scoped_runner")
synchronized_obj = _synchronize_coroutine(getattr(*self._synchronization_target_attr), runner, context)
with MonkeyPatch.context() as c:
    c.setattr(*self._synchronization_target_attr, synchronized_obj)
    super().runtest()
```

Because both fixture setup and test body are driven from *sync* frames, the
`RuntimeError: Cannot run the event loop while another loop is running` problem never arises —
pytest's setup phase is never itself inside a running loop.

**Loops per run.** One `asyncio.Runner` per (scope value × collector instance), realised as five
plugin-level fixtures generated in a loop:

```python
for scope in Scope:
    globals()[f"_{scope.value}_scoped_runner"] = _create_scoped_runner_fixture(scope.value)
```

pytest's own scope caching then gives one runner instance per module, per package, etc.

> ⚠️ **Changelog/PR wording conflict.** The 1.0.0 changelog says *"Scoped event loops (e.g.
> module-scoped loops) are created once rather than per scope (e.g. per module)"*, which reads as
> if there is now one module loop for the entire run. [PR #1107](https://github.com/pytest-dev/pytest-asyncio/pull/1107)'s
> body is clearer and contradicts that reading: *"Scoped loop **fixtures** are created once for
> each scope rather than for each occurrence of a scope."* It is a **fixture-definition count**
> optimisation. Trust the PR.

> ⚠️ **Docs/source conflict on the default.** `docs/reference/decorators/index.rst` says *"The
> default event loop scope is *function* scope."* The source disagrees:
> `loop_scope = getattr(fixturedef.func, "_loop_scope", None) or default_loop_scope or fixturedef.scope`
> — i.e. with the ini option unset it falls back to **the fixture's caching scope**, and emits a
> `PytestDeprecationWarning` saying so: *"The event loop scope for asynchronous fixtures will
> default to the 'fixture' caching scope. Future versions of pytest-asyncio will default the loop
> scope for asynchronous fixtures to 'function' scope."* The `configuration.rst` page matches the
> source. The decorators page is describing the *intended future* default.

**Documented cross-loop failure modes.** Beyond #706: [#950](https://github.com/pytest-dev/pytest-asyncio/issues/950)
is the cleanest — a module-scoped connection object that captures its loop, three module-loop
tests, and one function-loop test *interleaved between them*. The final module-scoped test fails
**only if the function-scoped test is present**. Fixed in 0.25.1 as *"a broken event loop when a
function-scoped test was executed in between two tests with wider loop scope."*

And a failure mode that a per-test **loop** does not fix:
[#1191](https://github.com/pytest-dev/pytest-asyncio/issues/1191) — even at function scope, setup
and teardown run in **different asyncio tasks** (each `runner.run(...)` starts a new task), so:

```python
@pytest.fixture
async def scope():
    with anyio.CancelScope() as scope:
        yield scope
# RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

The open fix, [#1193](https://github.com/pytest-dev/pytest-asyncio/pull/1193) ("Ensure async
fixture setup and teardown run in the same task"), is explicitly *"Inspired by anyio's pytest
plugin"* and notes it *"requires some hacky hacks."*

### anyio — architecture B, one refcounted global runner

anyio has **no per-scope loops at all**. There is exactly one runner at a time, whose lifetime is
a reference count. `src/anyio/pytest_plugin.py`:

```python
_current_runner: TestRunner | None = None
_runner_leases = 0

@contextmanager
def get_runner(backend_name, backend_options) -> Iterator[TestRunner]:
    global _current_runner, _runner_leases, _runner_stack
    if _current_runner is None:
        ...
        _current_runner = _runner_stack.enter_context(asynclib.create_test_runner(backend_options))
    _runner_leases += 1
    try:
        yield _current_runner
    finally:
        _runner_leases -= 1
        if not _runner_leases:
            _runner_stack.close()
            _runner_stack = _current_runner = None
```

The maintainer's statement ([anyio#686](https://github.com/agronholm/anyio/issues/686)):

> The AnyIO pytest plugin works on a basis of "leases", so that each test or fixture gets a
> "lease" which is scoped to the function, or in the case of fixtures, the scope of the fixture.
> Therefore, if you have just two tests that don't share any common fixtures, they are run in
> separate event loops. **The only reason to make the `anyio_backend` fixture scoped higher than
> function-scoped, is to make pytest happy so it can run higher order fixtures.**

That last sentence is the important one: `anyio_backend` is a plain *sync* fixture returning a
string. **It does not own or extend the loop.** Widening it only satisfies pytest's
scope-dependency rule. The docs say the same thing from the other end:

> The test runner is created when the first matching async test or fixture is about to be run,
> and shut down when that same fixture is being torn down or the test has finished running. As
> such, if no higher-order (scoped `class` or higher) async fixtures are used, a separate test
> runner is created for each matching test. Conversely, **if even one async fixture, scoped higher
> than `function`, is shared across all tests, only one test runner will be created during the
> test session.**

**Two scopes in one test → same loop, by construction.** The wider fixture is set up first, takes
lease 1 and creates the runner; the narrower fixture and the test body then find
`_current_runner is not None` and are handed the *existing* runner. There is no collision to
resolve. Note the early-return branch does **not** re-check `backend_name` — a wider-scoped
fixture effectively **pins the backend** for everything nested inside its lease.

**Eager, and the re-entrancy guard is explicit.** anyio materialises the value from pytest's sync
setup phase like pytest-asyncio, so it *does* have to defend against nested entry — and it does,
in a comment that answers oxitest's sub-question verbatim:

```python
# re-entrant call into the test runner detected. this happens when an async fixture
# is dynamically requested via request.getfixturevalue() from inside a running async
# test or fixture. on asyncio this raises RuntimeError: This event loop is already
# running, on trio the runner deadlocks - the host loop blocks waiting for the
# coroutine to return, but the coroutine is waiting for the host loop. raising here
# prevents the hang and gives a consistent error across backends.
if runner.is_running():
    raise RuntimeError(
        "Cannot schedule a coroutine in the test runner while another is already running; "
        "likely caused by request.getfixturevalue() on an async fixture."
    )
```

`request.getfixturevalue()` on an async fixture remains unsupported
([anyio#720](https://github.com/agronholm/anyio/issues/720), open since 2024-04-15).

**Documented cross-loop failure modes.** `RuntimeError: Runner is closed`
([#619](https://github.com/agronholm/anyio/issues/619) — the lease counter *is* the fix for it;
3.7.1's `get_runner` had no counter) and `RuntimeError: Event loop is closed`
([#555](https://github.com/agronholm/anyio/issues/555), titled *"when autousing differently scoped
fixtures"*, requiring autouse **and** a wider-than-function scope **and** a particular test order).

> ⚠️ **Docs/source conflict on `anyio_backend`'s scope.** The docs say twice — on `master` and on
> readthedocs *stable* — *"The AnyIO pytest plugin comes with a **function scoped** fixture with
> this name"* and *"you will need to define your own `anyio_backend` fixture because the default
> `anyio_backend` fixture is function scoped."* The source has been `scope="module"` since **4.0.0**
> (verified across the 3.7.1 / 4.0.0 / 4.11.0 / 4.14.1 / master tags), corroborated by
> `pytest_collection_finish` building `_arg2scope={"anyio_backend": Scope.Module}`. No changelog
> bullet announces the change. **Docs stale since 4.0.0.**

### pytest-trio — architecture C, and the only framework that resolves *lazily*

pytest-trio runs **exactly one `trio.run()` per test**, and forbids wider scopes. Its answer to
"how is the test body guaranteed to be on the same runtime" is structurally different from
everyone else's: **it never enters trio during pytest's setup phase at all.** `handle_fixture`
hands pytest a *placeholder object* instead of a value:

```python
fixture = TrioFixture("<fixture {!r}>".format(fixturedef.argname), fixturedef.func, kwargs)
fixturedef.cached_result = (fixture, request.param_index, None)
return fixture
```

The design comment at the top of `pytest_trio/plugin.py` states the whole architecture, including
its price:

> Our trick: from pytest's point of view, trio fixtures return an **unevaluated placeholder
> value**, a `TrioFixture` object. This contains all the information needed to do the actual
> setup/teardown, but doesn't actually perform these operations.
>
> Then, pytest runs what it thinks of as "the test", we enter trio, and use our own logic to setup
> the trio fixtures, run the actual test, and then tear down the trio fixtures. This works pretty
> well, though it has some limitations:
> - **trio fixtures have to be test-scoped**
> - normally pytest considers a fixture crash to be an ERROR, but when a trio fixture crashes, it
>   gets classified as a FAIL.

Because trio is entered exactly once, from inside the test call, the "cannot run the event loop
while another loop is running" class of error **cannot arise** — there is no nested entry. The
docs put it plainly: *"For technical reasons, we can't wrap this whole process in `trio.run()` —
only the test itself."* All fixtures then resolve inside that one run, each in its own trio task
in one nursery, sharing *"a single `Context` which is shared by all fixtures and the test function
itself."*

**Why wider scopes are refused — maintainer reasoning.** From
[pytest-trio#89](https://github.com/python-trio/pytest-trio/issues/89), open since 2020-01-13:

- **oremanj (2024-08-09)**, the definitive statement:
  > The basic difficulty is that **all of pytest's internal test runner logic is synchronous code**,
  > so we can't easily open an event loop "around" the test runner and run tests inside of it,
  > because synchronous functions can't do a blocking call into an enclosing async context.

  and:
  > **Higher-scoped fixtures require a single Trio run that wraps all tests in their scope. This is
  > incompatible with current popular use of fixtures like `autojump_clock`, which require that
  > each test have its own Trio run** so some of them can use the autojump clock while others don't
  > (the clock must be known when the run starts and can't be swapped out partway). So I think this
  > might actually want to be a separate package from `pytest-trio`.
- **touilleMan (2021-05-19)**:
  > Using the same trio loop with different tests is a very slippy slope (typically with the current
  > `nursery` fixture we can endup with a test spawning coroutines that will outlive it) … The two
  > isolated trio loops … is a double edged sword: you have to do extra work if you want to
  > synchronize the two loop, BUT it also mean you cannot break stuff due to subtle interactions
  > between coroutines. **So I guess we shouldn't even add this feature to the pytest-trio library.**
- **jakkdl (trio maintainer, 2024-10-20)**: *"it's currently possible to get higher-scoped fixtures
  if you use the anyio pytest plugin, which you can configure to only run in trio mode."*

pytest-trio's last release is **0.8.0 (2022-11-01)** — none of this has moved in ~3.75 years, and
[#147](https://github.com/python-trio/pytest-trio/issues/147) proposes replacing its internals with
anyio's outright.

### trio — why a nursery cannot outlive its scope

No fixture concept, but the underlying rule is *why* a trio-native wider-than-test fixture is hard.
From [`reference-core.rst`](https://github.com/python-trio/trio/blob/main/docs/source/reference-core.rst):

> Since all of the tasks are running concurrently inside the `async with` block, **the block does
> not exit until *all* tasks have completed.**

and once it has: *"The nursery is marked as 'closed', meaning that no new tasks can be started
inside it."* `Nursery.start_soon` raises *"`RuntimeError` – If this nursery is no longer open."*

Nathaniel Smith, *[Notes on structured concurrency](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/)*:

> any function can open a nursery and run multiple concurrent tasks, but **the function can't
> return until they've all finished.**

> We *never* terminate a task without giving it a chance to run cleanup handlers, and we *never*
> leave a task to run unsupervised outside of the nursery, even if it's in the process of being
> cancelled.

Passing a nursery *into* another task is supported. What is impossible is the nursery surviving its
own `async with` — **which is exactly the shape a session-scoped fixture needs.**

### Jest and Vitest — the question relocates, it does not dissolve

JS has one loop per worker, so "which loop owns it" is trivially answered. But the *equivalent*
question — can a value made at run scope reach a test? — has a hard, documented **no** in both.

**Jest.** From [the config docs](https://jestjs.io/docs/configuration#globalsetup-string):

> Any global variables that are defined through `globalSetup` can only be read in `globalTeardown`.
> **You cannot retrieve globals defined here in your test suites.**

The `globalThis.__MONGOD__` convention is a *main-process* handoff between setup and teardown only.
**Jest offers no channel at all** — to get a value into test files you must serialise out of band
(env var, temp file, port number). Each test file runs in its own VM context with fresh globals.

**Vitest.** Same prohibition, but with a supported, typed channel. A `::: danger` block in
[the globalSetup docs](https://vitest.dev/config/globalsetup):

> Beware that the global setup is running in a **different global scope** before test workers are
> even created, so your tests don't have access to global variables defined here. However, you can
> pass down **serializable** data to tests via `provide` and read them in your tests via `inject`.

with the constraint spelled out: *"Properties have to be strings and values need to be serializable
because this object will be transferred between different processes."*

**This is the JS answer to Q2, and it is a strong one:** the live object stays in the process that
owns it, and only a structured-cloneable **handle** (a port, a URL) crosses the boundary.

Vitest also makes fixture scope an explicit ownership axis —
[`scope: 'test' | 'file' | 'worker'`](https://vitest.dev/guide/test-context#fixture-scopes) (added
3.2.0) — with the isolation interaction documented precisely:

> By default, every file runs in a separate worker, so `file` and `worker` scopes work the same
> way. However, if you disable isolation, then the number of workers is limited by `maxWorkers`,
> and **worker-scoped fixtures will be shared across files running in the same worker.**

**The nearest JS analogue of the cross-loop failure** is Jest's *"You are trying to `import` a file
after the Jest environment has been torn down"* — but note the diagnosis: it is **not** caused by
unawaited `afterAll` (that *is* awaited, and teardown happens strictly after it, per
`packages/jest-runner/src/runTest.ts`). It is caused by work nobody awaited, lazily requiring after
`Runtime#teardown()` cleared the module registry.

The canonical thread on values outliving their environment is
[jest#11202](https://github.com/jestjs/jest/issues/11202), opened by Dan Abramov:

> I'm getting the impression is that Jest **doesn't do anything special for real timers**. Which
> would explain why, for example, `setTimeout` simply won't fire if this is the only test file, but
> *would* fire if the process is still running. […] **This seems like a source of
> non-determinism.**

SimenB's reply is the statement of *intended* semantics — and it is still aspirational, not shipped
(the issue was closed by the stale bot in 2022):

> I think long term, the ideal way for Jest to handle this is to **disallow scheduling any work
> after the test environment has been torn down** (or is in the process if being torn down) or run
> any previously scheduled work. Once a test has completed, no more of its code should run.

### JUnit 5 and xUnit.net — no loop, but a real thread-ownership answer

**JUnit.** No event loop; ownership is thread confinement, and it is **opt-in**. Parallel
execution is off by default (`junit.jupiter.execution.parallel.enabled`), and even when on, nodes
default to `SAME_THREAD`:

> **SAME_THREAD** — Force execution in the same thread used by the parent. For example, when used
> on a test method, the test method will be executed in the same thread as any `@BeforeAll` or
> `@AfterAll` methods of the containing test class.

That is JUnit's whole answer to "how does the test body get on the same X as its wider-scoped
setup": *pin them to a thread*. `PER_CLASS` is carved out of the parallel default — *"test authors
have to ensure that the test class is thread-safe"*.

**The Kotlin case is the one place JUnit gets async, and how it does it is the point.** JUnit
6.0.0 release notes: *"Kotlin's `suspend` modifier may now be applied to test and lifecycle
methods."* The User Guide names the mechanism:

> **When using `suspend` test or lifecycle methods, JUnit internally uses `runBlocking` to execute
> them.** This is sufficient for simple coroutine-based tests. However, `runBlocking` does not
> support skipping calls to `delay` and does not provide control over virtual time or dispatchers.

`runBlocking` means **JUnit does not own a runtime — it borrows the worker thread it already had.**
And this stops hard at annotated methods. Extension callbacks — i.e. exactly the APIs that own
scope-bound disposal — can never be `suspend`.
[#5032](https://github.com/junit-team/junit-framework/issues/5032), closed `not_planned`:

> **Team decision:** We think adding `suspend` function support for every extension interface would
> be too invasive and incur significant maintenance overhead, in particular since there's a
> workaround by using `runBlocking`.

There is a real interaction with the executor, worth noting for anyone doing blocking setup:

> **fork_join_pool (default)** — […] usages of `ForkJoinPool` in test or production code or **calls
> to blocking JDK APIs may cause the number of concurrently executing tests to increase.** To avoid
> this situation, please use `worker_thread_pool`.

**xUnit.net** does own a scheduling primitive, but only in one of two algorithms.
`src/xunit.v3.core/Utility/MaxConcurrencySyncContext.cs`:

> An implementation of `SynchronizationContext` which **runs work on custom threads rather than in
> the thread pool**, and limits the number of in-flight actions.

Used only under the *aggressive* parallel algorithm; the **conservative** algorithm has been the
default since Core Framework v2 2.8, because the sync-context approach broke timing:

> **Aggressive** — the original parallelism algorithm, which starts as many tests as possible […]
> and **uses a `SynchronizationContext` to limit the number of things that are running at any given
> time**. Since tests in this system which encounter async awaits are put back into a pool to
> compete against all potential running tests, they may wait longer to resume which **causes the
> inaccuracy of timing that makes `Timeout` problematic**.

The sharper ownership answer is identity flow, not scheduling. xUnit uses an `AsyncLocal`
`TestContext`, with a documented silent-loss failure:

> xUnit.net v3 uses an **async-local context (`TestContext`)** to be able to associate the current
> thread with the associated test. […] If you write console or trace output to a thread that is not
> associated with a test (such as a **background worker thread created by your test or production
> code**), then that output will **silently discarded** as there is no test to associate the output
> to.

That is the closest non-async-runtime analogue of oxitest's cross-loop problem: work that escapes
the scope's context loses its identity, quietly.

### Go — the question structurally does not exist

Goroutines are multiplexed by the runtime built into the binary. There is no user-constructible
executor, no per-test loop, no API to create/enter/drop a runtime. A `chan`, a `time.Timer`, or a
`net.Conn` created in `TestMain` works identically inside any test. **The entire class of failure
described above is absent.**

What Go has instead is explicit cancellation propagation, and as of **Go 1.24** `testing` supplies
it. `func (c *common) Context() context.Context`:

> Context returns a context that is **canceled just before Cleanup-registered functions are
> called.** Cleanup functions can wait for any resources that shut down on `context.Context.Done`
> before the test or benchmark completes.

The ordering is deliberate — cancel first (from inside `runCleanup`, before the LIFO drain) so
background goroutines wind down, *then* run cleanups that can block waiting for them. Because
`Context` lives on `common`, **cancellation scope mirrors cleanup scope exactly** at every nesting
level. Note there is **no `M.Context()`** — no package-scoped context; `TestMain` must build its own.

### Rust — per-test runtime, enforced by panics

`#[tokio::test]` constructs a **brand-new runtime inside every test function body**. From
[`tokio-macros/src/lib.rs`](https://docs.rs/tokio/latest/tokio/attr.test.html): *"The default test
runtime is single-threaded. **Each test gets a separate current-thread runtime.**"* The codegen
(`tokio-macros/src/entry.rs`, `parse_knobs`) inlines `Builder::new_current_thread()…block_on(body)`
into each test. **There is no way to share a runtime between two `#[tokio::test]` functions.**

The failure modes are panics with fixed message text:

| Situation | Message | Source |
|---|---|---|
| Value used off its runtime | `there is no reactor running, must be called from the context of a Tokio 1.x runtime` | `tokio/src/util/error.rs`, `CONTEXT_MISSING_ERROR` |
| Runtime enabled but timers off | `A Tokio 1.x context was found, but timers are disabled. Call enable_time on the runtime builder to enable timers.` | `tokio/src/runtime/driver.rs` |
| Runtime dropped from async ctx | `Cannot drop a runtime in a context where blocking is not allowed. This happens when a runtime is dropped from within an asynchronous context.` | `tokio/src/runtime/blocking/shutdown.rs`, `Shutdown::wait` |
| Blocking inside a runtime | `Cannot block the current thread from within a runtime.` | `tokio/src/future/block_on.rs` |

And on outliving ([`tokio/src/runtime/runtime.rs`](https://docs.rs/tokio/latest/tokio/runtime/struct.Runtime.html)):

> Once the runtime has been dropped, **any outstanding I/O resources bound to it will no longer
> function.** Calling any method on them will result in an error.

**`rstest` does not own a runtime at all.** From [docs.rs/rstest](https://docs.rs/rstest/latest/rstest/):

> `rstest` supports async tests, but **makes no assumptions about what async runtime you are
> using.** […] You are responsible for providing any appropriate peer dependencies.

Runtime selection is a **syntactic suffix match on the attribute path**: *"If your test contains an
attribute whose path ends in `test`, this is treated as an implicit test attribute, and replaces
the default `#[test]` attribute."* That is why `#[tokio::test]`, `#[async_std::test]` and
`#[actix_rt::test]` all work with no special-casing. Async tests with no such attribute **fail to
compile**: `"async test requires either explicit `test_attr` or implicit (attribute path ends with
`test`)"`.

So rstest inherits tokio's per-test-runtime ownership wholesale, and has no runtime of its own to
scope a fixture to. **This is the direct cause of the `#[once]` async ban** in Q3.

### Q2 comparison table

| Framework | Loops per run | Two scopes in one test | Eager or lazy | Documented cross-loop failure |
|---|---|---|---|---|
| **pytest-asyncio** | one `Runner` per (scope × collector); 5 scope tiers | **Allowed** — `loop_scope` is a separate axis, constrained `loop_scope ≥ scope` (enforced indirectly via pytest `ScopeMismatch`) | **Eager**, from sync setup; no nesting so no re-entrancy problem | #706 (fixture on a different loop than test), #950 (narrow-loop test interleaved between wide-loop tests breaks the wide loop), #1191 (setup/teardown in **different tasks** → anyio `CancelScope` dies) |
| **anyio** | **one global runner**, refcounted by lease | **No collision possible** — wider fixture creates the loop, everything nested reuses it. Wider fixture also **pins the backend** | **Eager**; re-entrancy raises an explicit `RuntimeError` naming `getfixturevalue()` | #619 `Runner is closed`, #555 `Event loop is closed` (autouse × wider scope × ordering) |
| **pytest-trio** | exactly one `trio.run()` **per test** | n/a — wider scopes forbidden | **Lazy** — pytest gets a `TrioFixture` placeholder; trio entered once, *inside* the test call. Re-entrancy structurally impossible | n/a |
| **Jest / Vitest** | one loop per worker; file = isolation unit | n/a | n/a | live `globalSetup` values **cannot** reach tests (both); Jest "environment has been torn down"; timers not policed (#11202) |
| **JUnit 5** | no loop; thread confinement via `SAME_THREAD`, opt-in parallelism | n/a | n/a | Kotlin `suspend` runs via `runBlocking` (borrows the worker thread); extension callbacks can **never** be `suspend` (#5032, `not_planned`) |
| **xUnit.net** | no loop; `MaxConcurrencySyncContext` only under the *aggressive* algorithm (not the default) | n/a | n/a | output from a thread outside the `AsyncLocal` `TestContext` is **silently discarded** |
| **Go** | **no such concept** | n/a | n/a | none — structurally absent. `t.Context()` cancels *just before* cleanups, per nesting level |
| **Rust** | **one runtime per test**, constructed in the test body | n/a — no shared runtime exists | n/a | `there is no reactor running…`; `Cannot drop a runtime in a context where blocking is not allowed` |

---

## 5. Q3 — Autouse / implicit restrictions

> Are implicit async fixtures restricted at wider scopes? Which (async × scope × autouse) cells are rejected?

### pytest core — autouse is *named in the error message*

This is the most directly transferable finding in the survey for ADR-0006's rejection policy,
because it is pytest's own answer to *exactly* the cell ADR-0006 rejects.

`src/_pytest/fixtures.py`, `resolve_fixture_function` path — note that the message is
**parameterised on autouse**:

```python
if inspect.isasyncgenfunction(fixturefunc) or inspect.iscoroutinefunction(fixturefunc):
    auto_str = " with autouse=True" if fixturedef._autouse else ""
    fail(
        f"{request.node.name!r} requested an async fixture {request.fixturename!r}{auto_str}, "
        "with no plugin or hook that handled it. This is an error, as pytest does not natively support it.\n"
        "See: https://docs.pytest.org/en/stable/deprecations.html#sync-test-depending-on-async-fixture",
        pytrace=False,
    )
```

**The escalation arc — verified against changelog, PRs, and the deprecation page:**

| When | What | Source |
|---|---|---|
| 2024-11-17 | PR merged adding a `DeprecationWarning` | [pytest#12930](https://github.com/pytest-dev/pytest/pull/12930) |
| **8.4.0** (2025-06-02) | *"Requesting an asynchronous fixture without a `pytest_fixture_setup` hook that resolves it will now give a DeprecationWarning. […] may affect non-standard hook setups or `autouse=True`."* | changelog, [#10839](https://github.com/pytest-dev/pytest/issues/10839) |
| **9.0.0** (2025-11-05) | *"**`PytestRemovedIn9Warning` deprecation warnings are now errors by default.**"* — blanket escalation | changelog, [#13779](https://github.com/pytest-dev/pytest/issues/13779) |
| 2025-11-30 | PR *"fixtures: turn requesting async fixture without a plugin into a hard error"* | [pytest#14015](https://github.com/pytest-dev/pytest/pull/14015) |

The [deprecation page](https://docs.pytest.org/en/stable/deprecations.html) gives the rationale,
and it is the silent-no-op argument verbatim:

> It has not given any errors if you have an asynchronous fixture that's depended on by a
> synchronous test. […] **Fixture values being cached can make this even more unintuitive, where
> everything will "work" if the fixture is first requested by an async test, and then requested by
> a synchronous test.**

> If a user has an async fixture **with `autouse=True` in their `conftest.py`**, or in a file
> containing both synchronous tests and the fixture, they will receive this warning. […] Unless
> you're using a plugin that specifically handles async fixtures with synchronous tests, **we
> strongly recommend against this practice.**

And the PR author's own note on whether to bother with a deprecation period for the autouse case:

> I'm not sure if we care to have a deprecation period for sync-test + autouse-async-fixture, while
> users may currently have test suites that pass **they're very close to shooting themselves in the
> foot and they will be riddled with `RuntimeWarning: coroutine [...] was never awaited`**.

That last line is a description of ADR-0006's root-cause bug, written by someone else, about a
different codebase.

### pytest-asyncio — **no restriction, and autouse is not mentioned once**

Grepping the entire `docs/` tree plus `README.rst` for `autouse` returns **zero matches**
(checked 2026-07-29). There is no rule, no warning, no discussion. Autouse async fixtures at any
scope are simply allowed.

The only autouse evidence in the repo is a bug report —
[#1052](https://github.com/pytest-dev/pytest-asyncio/issues/1052), literally
`@pytest_asyncio.fixture(loop_scope="package", scope="package", autouse=True)` raising
`fixture 'test/asynchronous::<event_loop>' not found`, fixed in 1.0.0 — and a class-scoped
`@pytest.fixture(autouse=True) async def` in the test suite. Autouse is a supported, untested,
undiscussed combination.

### anyio — **nothing is rejected; it degrades silently, and autouse is the trigger**

There is no error for any (async × scope × autouse) combination. The gating condition is purely
whether the plugin can *see* `anyio_backend` in the fixture closure:

```python
func = fixturedef.func
if isasyncgenfunction(func) or iscoroutinefunction(func):
    if "anyio_backend" in request.fixturenames:
        fixturedef.func = wrapper
```

If it isn't there, **the async fixture is never wrapped** and pytest binds the raw
async-generator object as the fixture value. From
[anyio#555](https://github.com/agronholm/anyio/issues/555):

```
async_fixture = <async_generator object async_fixture at 0xffffabc02980>
E   assert False
E    +  where False = isinstance(<async_generator object async_fixture at 0xffffabc02980>, int)
```

**Autouse is precisely why this bites**, because an autouse fixture is never *named* by the test,
so nothing pulls `anyio_backend` into the closure. The reporter's own diagnosis:

> The "magic" sauce ended up being the addition of the `anyio_backend` fixture from within the
> autouse fixture … because I have a sync fixture that uses an async fixture I assumed it was not
> needed in the sync fixture, **but it is**!

The docs acknowledge it obliquely, without explaining why:

> For `autouse=True` fixtures, you may need to use the other approach:
> ```python
> @pytest.fixture(autouse=True)
> async def server(anyio_backend): ...
> ```

Two more autouse findings: **#555's title is literally *"Pytest `RuntimeError: Event loop is
closed` when autousing differently scoped fixtures"*** — autouse × wider-scope × ordering is
anyio's known bug cluster. And **autouse is the community workaround for pinning one loop per
run**: a no-op `@pytest.fixture(autouse=True, scope="session") async def common_event_loop(anyio_backend)`
holds lease #1 for the whole session, making `id(asyncio.get_running_loop())` stable — which
without it is not ([anyio#686](https://github.com/agronholm/anyio/issues/686)).

### pytest-trio — **autouse is irrelevant; scope alone is rejected, always**

The strongest rejection in the Python half of the survey, and it is unconditional on autouse.
`handle_fixture` in `pytest_trio/plugin.py`:

```python
if _is_trio_fixture(fixturedef.func, coerce_async, kwargs):
    if request.scope != "function":
        raise RuntimeError("Trio fixtures must be function-scope")
    if not is_trio_test:
        raise RuntimeError("Trio fixtures can only be used by Trio tests")
```

Doc text (`docs/source/reference.rst`):

> regular fixtures can be scoped to the test, class, module, or session, but **Trio fixtures must be
> test scoped. Class, module, and session scope are not supported.**

Two amplifiers worth stealing:

**1. Contagion.** `_is_trio_fixture` returns true if `any(isinstance(value, TrioFixture) for value
in kwargs.values())` — so a *synchronous* module-scoped fixture that merely depends on a trio
fixture **is itself a trio fixture** and hits the same error. The restriction propagates up the
dependency graph rather than being checked only at the declaration.

**2. The door is nailed shut at the decorator**, with the intent in a comment:

```python
# It's intentionally impossible to use this to create a non-function-scoped
# fixture (since that would require exposing a way to pass scope= to
# pytest.fixture).
def trio_fixture(func):
    func._force_trio_fixture = True
    return pytest.fixture(func)
```

| Combination | Result |
|---|---|
| trio fixture (or any fixture *depending* on one) × class/module/package/session × autouse **or not** | `RuntimeError: Trio fixtures must be function-scope` |
| trio fixture × any scope × requested by a **sync** test | `RuntimeError: Trio fixtures can only be used by Trio tests` |

### Jest — no restriction possible, because there is no distinction

**Jest hooks are unconditionally implicit.** There is no fixture concept, no dependency injection,
no `usefixtures`. `beforeAll` runs for every test in scope simply because it was declared there.
Nothing lets a test opt in or out. **This is a gap by design:** the framework has no vocabulary for
"this setup is *requested* by this test," so it cannot restrict implicit-vs-explicit.

The only restrictions are opt-in lint rules.
[`jest/no-hooks`](https://github.com/jest-community/eslint-plugin-jest/blob/main/docs/rules/no-hooks.md)
has a one-sentence rationale and no citation: *"The use of these hooks promotes shared state
between tests."*
[`jest/require-top-level-describe`](https://github.com/jest-community/eslint-plugin-jest/blob/main/docs/rules/require-top-level-describe.md)
pushes hooks *out* of file scope and *into* suite scope.

### Vitest — the only JS framework with a real requested-vs-autouse distinction

**Requested (lazy) is the default**, and the request mechanism is the destructuring pattern itself
([test-context docs](https://vitest.dev/guide/test-context#fixture-initialization)):

> Vitest runner will **smartly initialize your fixtures** and inject them into the test context
> **based on usage**.

```ts
test('no fixtures needed', () => {})            // database will NOT run
test('needs database', ({ database }) => {})    // database WILL run
```

> When using `test.extend()` with fixtures, you should **always use the object destructuring
> pattern** `{ database }` to access context both in fixture function and test function.

**Autouse is opt-in and named as such** — `{ auto: true }`:

```ts
.extend('metrics', { auto: true }, ({}, { onCleanup }) => { ... })
```

**And there IS a documented restriction on wider scopes — an enforced access hierarchy**
([scope hierarchy](https://vitest.dev/guide/test-context#scope-hierarchy)):

> Fixtures can only access other fixtures from **the same or higher (longer-lived) scopes**:

| Fixture scope | Can access |
|---|---|
| `worker` | only other `worker` fixtures |
| `file` | `worker` + `file` |
| `test` | `worker` + `file` + `test` + the built-in test context |

> Only test-scoped fixtures have access to the built-in test context (`task`, `expect`, `skip`,
> etc.). **Worker and file fixtures run outside of any specific test**, so test-specific properties
> are not available to them.

Plus a default-scope trap in a `::: warning`: *"By default any fixture without a scope is treated
as a `test` fixture. This means that you **cannot use it inside `worker` and `file` scopes**."*
And an override restriction that throws:
`test.override('port', { scope: 'worker' }, 3000) // throws an error` — *"you cannot override
non-test fixtures inside `describe` blocks."*

`onCleanup` is single-shot: *"can only be called **once per fixture**. If you need multiple cleanup
operations, either combine them into a single cleanup function, or split your fixture into multiple
smaller fixtures."*

### JUnit 5 — implicit disposal is now the *default*, with an opt-out

JUnit went the other way: as of 5.13, merely implementing `AutoCloseable` and putting the object
in a store means it **will** be closed — implicit by default, `…store.close.autocloseable.enabled=false`
to opt out.

`@AutoClose` is *"registered by default"*, and its ordering is deliberately not a contract:

> When multiple `@AutoClose` fields exist within a given test class, the order in which the
> resources are closed depends on **an algorithm that is deterministic but intentionally
> nonobvious.** This ensures that subsequent runs of a test suite close resources in the same
> order, thereby allowing for repeatable builds.

Scope binding is spelled out per lifecycle mode: `static @AutoClose` fields close *"after all tests
in the current test class have completed, effectively after `@AfterAll` methods"*; under
`PER_CLASS`, non-static ones *"will not be closed until the current test class instance is no
longer needed."* Subclass fields close before superclass fields. `null` is skipped with a logged
warning.

Hard rejections are all **discovery-time** `Severity.ERROR` issues: non-static `@BeforeAll` under
`PER_METHOD` (*"must be static unless the test class is annotated with
`@TestInstance(Lifecycle.PER_CLASS)`"*), static `@BeforeEach`/`@AfterEach`, and any lifecycle
method returning a value. Private lifecycle methods are still only a warning: *"should not be
private. This will be disallowed in a future release."*

### xUnit.net — creation is implicit; injection is restricted structurally

**The fixture is created whether you ask for it or not.** Docs:

> xUnit.net uses the presence of the interface `IClassFixture<>` to know that you want a class
> fixture to be created and cleaned up. **It will do this whether you take the instance of the class
> as a constructor argument or not.**

**Restrictions that *are* enforced:**

- `ITestOutputHelper` **cannot** be injected into a fixture. `XunitTestClassRunnerBase.GetConstructorArgument`
  special-cases it only for the *test class* constructor; `FixtureMappingManager.GetFixture`
  resolves only `IMessageSink` and `ITestContextAccessor`, so an `ITestOutputHelper` parameter on a
  fixture falls through to *"had one or more unresolved constructor arguments."* Structurally
  correct — **a fixture outlives any single test, so there is no single test's output to write to.**
  This is the same shape as Vitest's "worker and file fixtures run outside of any specific test."
- *"fixtures cannot take dependencies on other fixtures"* — though the v3 source is more permissive
  than that sentence: constructor args resolve only from `parentMappingManager`, so **same-level**
  deps are impossible (as documented) but **cross-level** ones (class fixture taking a collection
  fixture) work. *Docs/source discrepancy.*
- `TestPipelineException("A test class may not be decorated with ICollectionFixture<> (decorate the
  test collection class instead).")`, also caught statically by analyzer **xUnit1059**.
- `async void` tests are fast-failed at runtime; analyzer **xUnit1049** (Error, v3).

**Async fixture × parallelism is NOT rejected — it is delegated, with an explicit warning at the
widest tier only:**

> Note that unlike collection fixtures, **there is no change in parallelization when using an
> assembly fixture. This means fixtures used as assembly fixtures may be used from multiple tests
> simultaneously, and must be designed for with this parallelism requirement in mind.**

Collections are safe by construction (a collection is the unit of parallel isolation), which is why
only the assembly tier carries the warning. xUnit v3 4.0 adds a third mode `all` — *"all tests are
run in parallel against all other tests"* — which breaks the class-fixture-is-implicitly-serialised
assumption, with per-collection / per-class / per-method opt-outs.

Also worth noting: **fixtures are not instantiated when every test in the scope is statically
skipped** —
`ClassFixtureMappings.InitializeAsync(…, createInstances: ctxt.TestCases.Any(tc => !tc.IsStaticallySkipped()))`.
Registration still happens; only construction is deferred.

### Go and Rust

**Go** has no fixtures, so nothing implicit to restrict. But it does refuse
process-global-mutation × parallelism outright rather than producing flakes —
`testing.go`, `parallelConflict`:

```go
// parallelConflict returns the panic message for a conflict between t.Parallel
// and op, the operation that cannot be combined with a parallel test: one of
// "t.Setenv", "t.Chdir", or "cryptotest.SetGlobalRandom".
func parallelConflict(op string) string {
	return "testing: test using " + op + " can not use t.Parallel"
}
```

**Rust `rstest` ships the survey's hardest rejection: a compile error.** Docs
([docs.rs/rstest](https://docs.rs/rstest/latest/rstest/)):

> There are some limitations when you use `#[once]` fixture. `rstest` forbid to use once fixture
> for:
> - **`async` function**
> - Generic function (both with generic types or use `impl` trait)

`rstest_macros/src/error.rs`, `async_once` — emitted as `compile_error!`, so it never reaches
runtime:

> **`"Cannot apply #[once] to async fixture."`**

**The stated reason is structural, and it is the cleanest articulation of the whole problem.** To
memoise an `async fn` into a `static` you would have to `block_on` it at first access — but rstest
*"makes no assumptions about what async runtime you are using,"* and blocking inside a
`#[tokio::test]` body hits `Cannot block the current thread from within a runtime`. **The
restriction is the honest consequence of not owning a runtime.** (Since 0.19.0 `#[once]` also
requires `Sync`, *"to prevent UB when tests are executed in parallel."*)

### Q3 comparison table

| Framework | Requested vs autouse distinction? | async × wider-scope × autouse | Rejection point | Stated reason |
|---|---|---|---|---|
| **pytest core** | Yes | **Hard error**, and the message *names* autouse | runtime, at fixture setup | "Fixture values being cached can make this even more unintuitive, where everything will 'work' if the fixture is first requested by an async test, and then requested by a synchronous test" |
| **pytest-asyncio** | Yes (pytest's) | **Allowed. Autouse is not mentioned in the docs at all** | — | — |
| **anyio** | Yes (pytest's) | **Allowed, but silently degrades** to a raw async-generator object when `anyio_backend` isn't in the closure — which autouse makes likely | none | not stated; docs give the workaround without the reason |
| **pytest-trio** | Yes (pytest's) | **All non-function scopes rejected**, autouse-irrelevant; restriction is *contagious* up the dependency graph | collection time | pytest's runner is sync, so trio can only be entered once, inside the test call |
| **Jest** | **No such concept** | n/a — all hooks implicit | — | lint-only (`no-hooks`: "promotes shared state between tests") |
| **Vitest** | **Yes** — lazy by destructuring; `{ auto: true }` opt-in | Allowed, but an **enforced scope-access hierarchy**: a fixture may only use fixtures of the same or longer-lived scope | runtime | wider fixtures "run outside of any specific test", so test-scoped context is unavailable to them |
| **JUnit 5** | Store keys are explicit; `@AutoClose` is implicit and on by default | n/a (no async) | discovery time | non-static `@BeforeAll` under `PER_METHOD` etc. |
| **xUnit.net** | **No** — `IClassFixture<>` creates the fixture "whether you take the instance as a constructor argument or not" | Allowed; **assembly tier carries an explicit parallelism warning**, collection tier is safe by construction | runtime / analyzer | `ITestOutputHelper` unavailable to fixtures because a fixture outlives any single test |
| **Go** | n/a | n/a | panic | `testing: test using t.Setenv can not use t.Parallel` |
| **Rust `rstest`** | n/a | **`#[once]` may not be async — compile error** | **compile time** | rstest owns no runtime, so it cannot `block_on` to memoise |

---

## 6. Findings that bear on oxitest

This section lays out what the evidence supports and where frameworks disagree. **It does not
choose for oxitest.**

### 6.1 The two ADRs currently describe different ladders

ADR-0006's dispatch table has **three** tiers; ADR-0009 Rule 2 has **four**:

```
 ADR-0006 (async dispatch)      ADR-0009 Rule 2 (lifetime ladder)
 ────────────────────────       ─────────────────────────────────
 each  ────────────────────►    function
                                module     ◄── NEW: no ADR-0006 dispatch rule
                                package    ◄── NEW: no ADR-0006 dispatch rule
 shared / session ─────────►    session
```

ADR-0006 says async × `shared`/`session` goes to `SharedAsyncManager.resolve()` on the shared async
session, and async × `each` gets a per-test-lifetime loop. **`module` and `package` fall between
those two rules and are unassigned.** Whatever oxitest decides, that gap is the first thing the
impl spec has to close, and it is precisely the gap the survey is about.

### 6.2 Nobody defers scope-boundary teardown by design — every deferral found was a bug

Ten frameworks, and **zero** deliberately defer wider-scope teardown to end of run. Every case of
deferral in the record is a defect, and in **every** case the defect is the same one: *the
loop/runtime was already dead when the post-`yield` half ran*.

| Framework | The bug | Fixed in |
|---|---|---|
| pytest-asyncio | *"event loops closed prematurely […] with class scope or wider in a function-scoped test"* | 0.23.3 |
| pytest-asyncio | *"broken event loop when a function-scoped test was executed in between two tests with wider loop scope"* | 0.25.1 |
| pytest-asyncio | *"errors in cleanup of async generators when event loop is already closed"* | 0.25.3 |
| anyio | `RuntimeError: Runner is closed` on higher-scoped asyncgen fixtures | 4.1.0 |
| anyio | teardown of higher-scoped async fixtures → *"Attempted to exit cancel scope in a different task"* | **4.14.1, 2026-06-24** |

The four-releases-in-a-row shape of the pytest-asyncio list is the signal: this is the *dominant*
failure mode of wider-than-test async fixtures, not an edge case. **Anything oxitest ships for
`module`/`package` async fixtures needs an invariant that makes "teardown on a dead loop"
unrepresentable, not a bug to be fixed later.**

pytest-asyncio's mitigation is worth copying in shape: an explicit finalizer that clamps fixture
teardown to *no later than* loop teardown — `# Prevent the runner closing before the fixture's
async teardown.` — so the effective boundary is `min(scope boundary, loop lifetime)`.

### 6.3 "Same loop" is not enough — the record says you need "same **task**"

This is the finding most likely to bite ADR-0006's per-test-loop decision as written, because
ADR-0006 reasons entirely in terms of *loops*.

- **anyio escalated twice.** 2.0: *"run the test and all its related async fixtures inside the same
  **event loop**."* 3.6: *"run both the setup and teardown phases of asynchronous generator fixtures
  within a **single task**."* 4.0 (**breaking**): *"run all tests and fixtures in the **same
  task**."* The stated driver for 4.0 was contextvar propagation.
- **pytest-asyncio still hasn't.** Because each `runner.run(...)` starts a fresh task, setup and
  teardown run in **different tasks** even at function scope. Consequence
  ([#1191](https://github.com/pytest-dev/pytest-asyncio/issues/1191)):
  ```python
  @pytest.fixture
  async def scope():
      with anyio.CancelScope() as scope:
          yield scope
  # RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
  ```
  The open fix ([#1193](https://github.com/pytest-dev/pytest-asyncio/pull/1193)) is explicitly
  *"Inspired by anyio's pytest plugin"* and *"requires some hacky hacks."*
- **The same class of bug is what anyio was still fixing in 4.14.1** (June 2026), triggered by
  `pytest.skip()` inside an async test.

ADR-0006 already identified the loop-level version of this hazard (`asyncio.run()` finalising async
generators via `GeneratorExit` before the test body runs). The evidence says the task-level version
is a *separate* invariant that has to be stated separately: **one loop AND one task spanning setup
→ body → teardown.** Anything holding a `CancelScope`, `TaskGroup`, or contextvar across the
`yield` depends on the second, not the first.

### 6.4 Two loop-ownership architectures, and they answer the multi-scope question differently

| | pytest-asyncio (A: separate axis) | anyio (B: refcounted lease) |
|---|---|---|
| Loops per run | one per (scope × collector) | **one, ever** |
| Two scopes in one test | user must keep them compatible; `loop_scope ≥ scope` | **impossible to get wrong** — wider fixture creates the loop, narrower reuses it |
| Cost | a real API surface: `loop_scope` on the fixture, `loop_scope` on the mark, two ini options, a migration guide | isolation is silently lost the moment *one* wider-scoped async fixture exists anywhere |
| How it was learned | by shipping the coupled version and telling users to downgrade for 8 months | by refactoring twice (2.0, 4.0) |

The anyio doc sentence states the trade honestly:

> if even one async fixture, scoped higher than `function`, is shared across all tests, **only one
> test runner will be created during the test session.**

**This is a genuine fork in the road for oxitest.** ADR-0006 chose a per-test loop for isolation.
The survey says you cannot have both per-test-loop isolation *and* a `session`-tier async fixture
whose value is usable from a test body — one of them has to give, and the two frameworks that
tried gave in opposite directions.

The pytest-asyncio triangle is the compact statement of the constraint, and it holds regardless of
which architecture is chosen:

> it's possible to **reevaluate an async fixture multiple times within the same event loop, but it's
> not possible to switch out the running event loop in an async fixture.**

If oxitest keeps a strict per-test loop, then by that rule **the only legal async lifetime above
`function` is one whose value never touches loop-bound state** — which is a real, defensible
position (it is essentially what `rstest`'s `#[once]` restriction encodes), but it needs saying out
loud rather than being discovered by users.

### 6.5 Eager vs lazy: pytest-trio is the only lazy design, and its cost is exactly ADR-0009's ladder

Every framework that resolves **eagerly** (pytest-asyncio, anyio) enters the runtime from pytest's
synchronous setup phase, and therefore has to defend against re-entrancy. anyio's guard is the
clearest artefact in the survey:

```python
if runner.is_running():
    raise RuntimeError(
        "Cannot schedule a coroutine in the test runner while another is already running; "
        "likely caused by request.getfixturevalue() on an async fixture."
    )
```

pytest-trio resolves **lazily** — pytest gets a `TrioFixture` placeholder, and trio is entered
exactly once, *inside* the test call. The `RuntimeError: Cannot run the event loop while another
loop is running` class of error is then structurally unreachable. **The price is stated in the same
comment that describes the trick:** *"trio fixtures have to be test-scoped"* and *"when a trio
fixture crashes, it gets classified as a FAIL"* rather than an ERROR.

oremanj's diagnosis of *why* generalises past trio, and applies to oxitest's Rust core just as
much as to pytest's Python one:

> **all of pytest's internal test runner logic is synchronous code**, so we can't easily open an
> event loop "around" the test runner and run tests inside of it, because synchronous functions
> can't do a blocking call into an enclosing async context.

oxitest's orchestration is in Rust and its fixture instantiation in Python; the same structural
constraint applies to whichever side owns the loop.

### 6.6 The rejection precedent ADR-0006 cites is stronger than ADR-0006 says

ADR-0006 cites pytest #12930 → #14015 as evidence that gating was insufficient. Verified, and it is
stronger than stated:

- The escalation was not just "gated → always on." It went **warning (8.4.0)** → **blanket
  warnings-become-errors (9.0.0, #13779)** → **hard `fail()` (#14015)**, and the current
  `src/_pytest/fixtures.py` message **names autouse explicitly**:
  `auto_str = " with autouse=True" if fixturedef._autouse else ""`.
- The stated rationale is oxitest's own bug, described independently: *"Fixture values being cached
  can make this even more unintuitive, where everything will 'work' if the fixture is first
  requested by an async test, and then requested by a synchronous test."*
- Two other frameworks reject in the same direction, earlier and harder: **pytest-trio** rejects at
  collection time, contagiously up the dependency graph; **rstest** rejects at **compile time**
  (`"Cannot apply #[once] to async fixture."`).

Only **anyio** does not reject — and anyio's non-rejection produces exactly the silent no-op
ADR-0006 exists to eliminate: an un-wrapped `<async_generator object …>` bound as the fixture value.

**This bears directly on ADR-0009's open item [#1733](https://github.com/kalonji-tools/oxitest/issues/1733)**
("the registrar never sets `FixtureDef.is_async`, so an `async def` under `@fixture` is injected as
an un-awaited coroutine at every tier"). That is the *same defect* pytest spent 8.4→9.0 removing,
and the same one anyio still has. The survey says: it is universally regarded as a defect, and
every framework that addressed it escalated to a hard error rather than a warning.

### 6.7 Autouse: nobody restricts it for being async, but it is where the bugs cluster

ADR-0009 Rule 7 permits autouse at all four tiers with no async-specific restriction. **The survey
supports that as the mainstream position** — pytest-asyncio does not mention autouse once,
pytest-trio's restriction is scope-based and autouse-irrelevant, and JUnit/xUnit make implicit
creation the *default*.

But two data points argue for treating autouse × wider-scope as a **hazard to instrument**, not to
forbid:

1. anyio's canonical wider-scope bug is titled *"`RuntimeError: Event loop is closed` **when
   autousing differently scoped fixtures**"*, and its reproduction needs autouse **and** a wider
   scope **and** a specific test order.
2. pytest's own hard-error message singles autouse out, because an autouse fixture is not *named*
   by the test — which is exactly why anyio's `anyio_backend`-in-the-closure gate silently misses it.

ADR-0009 already answers the visibility half of this by making autouse-firing a first-class
`oxitest inspect` view (Consequences item 22). The survey suggests that view is load-bearing, not
a nicety.

### 6.8 Two design mechanisms worth stealing outright

**JUnit's `ExtensionContext.Store`** is the closest thing in the survey to ADR-0009's principle
that *"lifecycle is the framework's job."* Disposal is tied to **the scope's context object**, not
to a scope keyword: put a value in the class context's store and it closes when that class ends;
put it in the root's and it closes at end of run — same API, different tier, no per-tier code.
LIFO within a store is documented twice. ADR-0009's four tiers could be expressed as four
contexts rather than four enum branches.

**Vitest's scope-access hierarchy** is the closest thing to ADR-0009 Rule 3 (B1 boundary) applied
to *lifetime* rather than *location*:

> Fixtures can only access other fixtures from **the same or higher (longer-lived) scopes**.

and the reason it gives is the same reason pytest-asyncio needs `loop_scope ≥ scope`:

> Worker and file fixtures **run outside of any specific test**, so test-specific properties are
> not available to them.

xUnit expresses the same constraint by a different route (`ITestOutputHelper` is resolvable for a
test class constructor but not for a fixture constructor). **Three independent frameworks converge
on "a longer-lived fixture may not reach into a shorter-lived one."** ADR-0009 Rule 4 caps
*declaration site* vs lifetime; this is the *dependency* version of the same rule, and the survey
does not show oxitest having it yet.

### 6.9 Parallelism: `session` means "per worker" in every multi-process runner

oxitest runs parallel by default via worker subprocesses. The survey's only direct evidence on what
that does to a `session` tier is pytest-xdist's, and it is blunt
([how-to](https://pytest-xdist.readthedocs.io/en/stable/how-to.html)):

> each worker process will perform its own collection and execute a subset of all tests […] tests
> in different processes requesting a high-level scoped fixture (for example `session`) **will
> execute the fixture code more than once**.

Its answer is a `FileLock` recipe, offered as *"a starting point"* rather than a guarantee.
xUnit is the only framework that documents the parallelism consequence *at the widest tier
specifically*:

> unlike collection fixtures, **there is no change in parallelization when using an assembly
> fixture** […] must be designed for with this parallelism requirement in mind.

**ADR-0009 Rule 2 defines `session` as "the entire test run."** Under oxitest's worker model that is
once *per worker process*, not once per run — and for an async fixture that means N loops, N
values, N teardowns. Nothing in either ADR currently says which of those two meanings `session` has.

---

## 7. Open questions the survey could not settle

1. **Does oxitest's `package` tier have any precedent as an *async* tier?** pytest-asyncio supports
   `loop_scope="package"`, but the only primary evidence found about it is a **bug report**
   ([#1052](https://github.com/pytest-dev/pytest-asyncio/issues/1052): `fixture 'test/asynchronous::<event_loop>' not found`,
   filed 2025-01-27, unfixed until 1.0.0 in May 2025) and a note that the package mark *"is not
   passed down to tests in subpackages."* No framework surveyed has a well-exercised async package
   tier. This is the least-trodden cell in the whole matrix.

2. **Is `loop_scope ≥ scope` actually enforced, or only documented?** The invariant is stated
   plainly in pytest-asyncio's decorator reference, but I found **no explicit check** in
   `plugin.py`. The plausible mechanism is pytest's own `SubRequest._check_scope` →
   `ScopeMismatch`, since the async fixture dynamically requests the runner fixture for its loop
   scope. Both halves of that chain are source-verified, but I did not execute the violating
   combination to confirm the resulting message. Worth a 5-minute experiment before relying on it.

3. **Exactly which anyio release shipped the `getfixturevalue` re-entrancy guard.**
   `docs/versionhistory.rst` lists the corresponding fix under **UNRELEASED**, yet the code appears
   in the 4.14.1 tag as served by GitHub. Treat the first-shipping version as unconfirmed.

4. **Whether anything enforces JUnit's LIFO guarantee across *nested* stores**, as opposed to within
   one store. The Javadoc documents inverse-add-order within a store; the hierarchy documents
   ancestor lookup. I did not find a statement about ordering *between* a class-level store's close
   and its parent's.

5. **Whether Vitest's `worker` fixture scope has a documented teardown-vs-worker-death ordering.**
   The scope table says worker fixtures are shared across files under `isolate: false`, but I found
   no statement about what happens if the worker is recycled or killed mid-run.

6. **oxitest-specific and unanswerable from outside:** whether `SharedAsyncManager` /
   `AsyncioSharedSession` (ADR-0005's `&mut` exception list) can hold one loop *and* one task across
   a `module` or `package` boundary, or whether the per-test-lifetime loop in
   `_fixture_instantiator.py` would need a sibling per-boundary loop. §6.3 says the invariant is
   "one loop AND one task"; whether oxitest's current seam can express that is an implementation
   question this survey cannot reach.

7. **What `session` means under oxitest's parallel worker model** (§6.9). Every framework surveyed
   that runs multiple processes ends up with "once per worker," and the only documented mitigation
   anywhere is a file lock. Whether oxitest wants run-once semantics — and what it would cost — is
   open.

---

## 8. Sources cited

**pytest core**
- https://github.com/pytest-dev/pytest/blob/main/src/_pytest/runner.py (`SetupState`, `teardown_exact`, `addfinalizer`)
- https://github.com/pytest-dev/pytest/blob/main/src/_pytest/fixtures.py (`SubRequest._check_scope`, async-fixture `fail()`)
- https://github.com/pytest-dev/pytest/blob/main/src/_pytest/python.py (`async_fail`)
- https://docs.pytest.org/en/stable/how-to/fixtures.html
- https://docs.pytest.org/en/stable/deprecations.html (`sync-test-depending-on-async-fixture`)
- https://github.com/pytest-dev/pytest/blob/main/doc/en/changelog.rst (8.4.0, 9.0.0)
- https://github.com/pytest-dev/pytest/pull/12930 · https://github.com/pytest-dev/pytest/pull/14015 · https://github.com/pytest-dev/pytest/issues/10839 · https://github.com/pytest-dev/pytest/issues/13779
- https://pytest-xdist.readthedocs.io/en/stable/how-to.html

**pytest-asyncio**
- https://github.com/pytest-dev/pytest-asyncio/blob/main/pytest_asyncio/plugin.py (`pytest_fixture_setup`, `_wrap_asyncgen_fixture`, `_wrap_async_fixture`, `_create_scoped_runner_fixture`, `PytestAsyncioFunction.setup`/`runtest`/`_loop_scope`, `_RUNNER_TEARDOWN_WARNING`, `_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET`)
- https://github.com/pytest-dev/pytest-asyncio/blob/v0.23.8/pytest_asyncio/plugin.py (`MultipleEventLoopsRequestedError`, `_MULTIPLE_LOOPS_REQUESTED_ERROR`)
- https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/changelog.rst
- https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/decorators/index.rst
- https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/configuration.rst
- https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/concepts.rst
- https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/how-to-guides/ (`migrate_from_0_21`, `migrate_from_0_23`, `change_fixture_loop`, `run_package_tests_in_same_loop`)
- Issues/PRs: [#200](https://github.com/pytest-dev/pytest-asyncio/issues/200) · [#706](https://github.com/pytest-dev/pytest-asyncio/issues/706) · [#793](https://github.com/pytest-dev/pytest-asyncio/issues/793) · [#862](https://github.com/pytest-dev/pytest-asyncio/issues/862) · [#950](https://github.com/pytest-dev/pytest-asyncio/issues/950) · [#1052](https://github.com/pytest-dev/pytest-asyncio/issues/1052) · [#1083](https://github.com/pytest-dev/pytest-asyncio/issues/1083) · [#1107](https://github.com/pytest-dev/pytest-asyncio/pull/1107) · [#1191](https://github.com/pytest-dev/pytest-asyncio/issues/1191) · [#1193](https://github.com/pytest-dev/pytest-asyncio/pull/1193) · [#1200](https://github.com/pytest-dev/pytest-asyncio/issues/1200) · [#1502](https://github.com/pytest-dev/pytest-asyncio/pull/1502)

**anyio**
- https://github.com/agronholm/anyio/blob/master/src/anyio/pytest_plugin.py (`get_runner`, `pytest_fixture_setup`/`wrapper`, `anyio_backend`, `pytest_collection_finish`) — also read at tags 3.7.1, 4.0.0, 4.11.0, 4.14.1
- https://github.com/agronholm/anyio/blob/master/docs/testing.rst · https://anyio.readthedocs.io/en/stable/testing.html
- https://github.com/agronholm/anyio/blob/master/docs/versionhistory.rst
- Issues: [#555](https://github.com/agronholm/anyio/issues/555) · [#619](https://github.com/agronholm/anyio/issues/619) · [#635](https://github.com/agronholm/anyio/pull/635) · [#686](https://github.com/agronholm/anyio/issues/686) · [#720](https://github.com/agronholm/anyio/issues/720) · [#805](https://github.com/agronholm/anyio/issues/805)

**pytest-trio + trio**
- https://github.com/python-trio/pytest-trio/blob/master/pytest_trio/plugin.py (`handle_fixture`, `TrioFixture`, `_trio_test_runner_factory`, `trio_fixture`, `_is_trio_fixture`)
- https://github.com/python-trio/pytest-trio/blob/master/docs/source/reference.rst · `history.rst`
- https://github.com/python-trio/pytest-trio/issues/89 · https://github.com/python-trio/pytest-trio/issues/147
- https://github.com/python-trio/trio/blob/main/docs/source/reference-core.rst
- https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/

**Jest**
- https://jestjs.io/docs/setup-teardown · /docs/configuration · /docs/cli · https://jestjs.io/blog/2025/06/04/jest-30
- Source (`main`): `packages/jest-circus/src/run.ts` (`_runTestsForDescribeBlock`, `_runTest`, `_callCircusHook`), `packages/jest-runner/src/runTest.ts` (`runTestInternal`, `tearDownEnv`), `packages/jest-runtime/src/index.ts` (`Runtime#teardown`), `packages/jest-core/src/runGlobalHook.ts`, `packages/jest-core/src/runJest.ts`, `packages/jest-cli/src/run.ts`
- Issues: [#6401](https://github.com/jestjs/jest/issues/6401) · [#7864](https://github.com/jestjs/jest/issues/7864) · [#11202](https://github.com/jestjs/jest/issues/11202) · [#11204](https://github.com/jestjs/jest/issues/11204) · [#12259](https://github.com/jestjs/jest/issues/12259)
- https://github.com/jest-community/eslint-plugin-jest/blob/main/docs/rules/no-hooks.md · `require-top-level-describe.md`

**Vitest**
- https://vitest.dev/api/hooks · /config/globalsetup · /config/sequence · /config/provide · /config/isolate · /config/pool · /config/teardowntimeout · /config/dangerouslyignoreunhandlederrors
- https://vitest.dev/guide/lifecycle · /guide/migration · /guide/parallelism · /guide/test-context
- https://github.com/vitest-dev/vitest/issues/5599 · https://github.com/vitest-dev/vitest/pull/5609 · https://github.com/vitest-dev/vitest/pull/7492 · https://github.com/vitest-dev/vitest/releases/tag/v2.0.0
- https://github.com/vitest-dev/eslint-plugin-vitest/blob/main/docs/rules/ (`no-hooks`, `require-hook`, `prefer-hooks-in-order`, `prefer-hooks-on-top`)

**JUnit 5 / 6**
- https://docs.junit.org/current/user-guide/ · /6.1.2/writing-tests/test-instance-lifecycle.html · /annotations.html · /test-classes-and-methods.html · /parallel-execution.html · /6.1.2/extensions/keeping-state-in-extensions.html · /6.1.2/running-tests/configuration-parameters.html · /5.13.4/user-guide/index.html
- https://docs.junit.org/current/release-notes/ · /5.13.4/release-notes/ · /6.0.0/release-notes/
- Javadoc: `ExtensionContext`, `ExtensionContext.Store`, `ExtensionContext.Store.CloseableResource`, `ExtensionContext.StoreScope`
- Source (`r6.1.2`): `junit-jupiter-engine/.../descriptor/LifecycleMethodUtils.java`, `.../config/DefaultJupiterConfiguration.java`, `.../engine/Constants.java`
- Issues: [#444](https://github.com/junit-team/junit-framework/issues/444) · [#4434](https://github.com/junit-team/junit-framework/issues/4434) · [#5032](https://github.com/junit-team/junit-framework/issues/5032)

**xUnit.net**
- https://xunit.net/docs/shared-context · /docs/getting-started/v3/migration · /docs/getting-started/v3/whats-new · /docs/running-tests-in-parallel · /docs/capturing-output · https://xunit.net/xunit.analyzers/rules/
- Source (v3 `main`): `src/xunit.v3.core/IAsyncLifetime.cs`, `Utility/FixtureMappingManager.cs`, `Utility/MaxConcurrencySyncContext.cs`, `Runners/XunitTestClassRunnerBase.cs`, `Runners/XunitTestCollectionRunnerBase.cs`, `Runners/TestClassRunner.cs`, `Runners/TestCollectionRunner.cs`, `Runners/TestRunner.cs`, `Runners/XunitTestRunnerBase.cs`
- Source (v2): `src/xunit.core/IAsyncLifetime.cs`, `src/xunit.execution/Sdk/Frameworks/Runners/XunitTestClassRunner.cs`, `…/XunitTestCollectionRunner.cs`

**Go**
- https://pkg.go.dev/testing · https://pkg.go.dev/os · https://go.dev/doc/go1.15 · https://go.dev/doc/go1.24
- https://github.com/golang/go/blob/master/src/testing/testing.go (`common.Cleanup`, `common.runCleanup`, `common.Context`, `tRunner`, `T.Run`, `T.Parallel`, `parallelConflict`, `M`, `M.Run`)

**Rust**
- https://docs.rs/rstest/latest/rstest/ · https://docs.rs/tokio/latest/tokio/attr.test.html · https://docs.rs/tokio/latest/tokio/runtime/struct.Runtime.html
- rstest 0.26.1: `rstest/src/lib.rs`, `rstest_macros/src/error.rs` (`async_once`, `generics_once`, `async_test_without_test_attribute`), `rstest_macros/src/render/fixture.rs` (`wrap_call_impl_with_call_once_impl`), `CHANGELOG.md`
- tokio 1.53.1: `tokio/src/util/error.rs` (`CONTEXT_MISSING_ERROR`), `tokio/src/runtime/blocking/shutdown.rs`, `tokio/src/future/block_on.rs`, `tokio/src/runtime/time/handle.rs`, `tokio/src/runtime/driver.rs`, `tokio-macros/src/entry.rs` (`parse_knobs`)
- Also checked and found to have no session-scoped async fixture concept: `test-context` 0.5.8, `serial_test` 4.0.1, `libtest-mimic` 0.8.2
