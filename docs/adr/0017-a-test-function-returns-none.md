# ADR-0017: A test function returns `None`

**Status:** Accepted
**Date:** 2026-08-12

A **test function** is a callable oxitest collects and runs to reach an outcome. Its return value is not part of that outcome and never has been: `_runners.py` called it and discarded whatever came back.

Discarding a `None` is correct. Discarding a **generator** is not, because the call that produced it ran no part of the body. Measured on `eaa22a25`, Linux 6.18.41 x86_64, CPython 3.12.13, oxitest 4.0.0, in one project of five items:

```
5 passed, exit 0

body executions recorded:  cls-return 1,  control 1
                           sync-gen 0,  async-gen 0,  cls-gen 0
```

Three of the five tests reported **passed** having executed nothing. The `control` row is a positive control in the same file and the same run: the mechanism records `1` when a body does run, so the three zeros are zeros rather than a broken probe.

The async row is the same defect through a second door. `importer.py` derives `is_async` from `inspect.iscoroutinefunction`, which answers `False` for an async generator, so `async def test_x(): yield` was routed to the **sync** runner and its never-awaited body discarded there.

## The decision

> **A test function returns `None`.**

Enforced at three points, because the rule becomes knowable at three different times and no one of them can see what the others see.

| Point | Classifies | Site | Disposition | Exit |
|---|---|---|---|---|
| Collection | a **function** | `importer.py`, `_collect_items` | refuse the run | **3** |
| Runtime | a **value** | `_runners.py`, both call sites | per-test error | **4** |
| Strict | an **AST node** | `src/test_returns.rs` | `test-returns-value` violation | **3** under `strict` |

### 1. The split is drawn at whether the body executed

This is the line that decides which point owns a case, and what severity it carries.

A **generator test never runs**. Nothing it claims to verify was verified, and the report says it passed. That is an error unconditionally, at whichever point can see it.

A **`return <value>` test does run**. Its assertions, if it has any, executed. What is wrong is that an assertion written as `return a == b` was evaluated and thrown away — a real defect, but one where the surrounding test body still did its work. That is a smell, so it waits for `strict`.

Two consequences follow, and both are deliberate:

- the strict check does **not** report `yield`, which is refused elsewhere with a message about generators. One defect must not be named twice under two names;
- the runtime guard tests for generators, **not** for `returned is not None`. The wider predicate would subsume the strict check and make every returning test a hard error with no opt-in.

### 2. Collection cannot be the only point, and this was measured

`functools.wraps` defeats every function-side predicate. Measured, CPython 3.12.13:

```
shape               isgeneratorfunction  isasyncgenfunction  iscoroutinefunction   call returns
sync_gen            True                 False               False                 generator
async_gen           False                True                False                 async_generator
wrapped_sync_gen    False                False               False                 generator
wrapped_async_gen   False                False               False                 async_generator
```

The wrapper answers `False` to all three while the call still returns a generator. A decorator is ordinary in a test suite, so a collection-only design would ship a hole in the common case.

The value side has no such blind spot: `inspect.isgenerator` and `inspect.isasyncgen` answer `True` for both wrapped rows. This mirrors `_async_orchestrator.py`, which already dispatches on the returned value rather than on the declared shape.

**A wrapper is not the only route, and assuming it was put a false statement in the shipped message.** An ordinary coroutine that *returns* a generator — `async def test_x(): return (i for i in items)` — reaches the same guard with no decorator involved, because the generator exists only once the coroutine is awaited. The first version of the runtime message read *"A wrapper hid the shape until the call"*, which sent that reader hunting for a decorator that is not there. The message now describes the value rather than a cause it cannot observe, and a test asserts the word `wrapper` is absent from the output.

That route was found at stage 8, by covering the async arm. It had shipped untested: a mutation deleting the call in `run_base_async` left the whole suite green.

### 3. Collection cannot reach every test item either

A plugin `Collector` returns `CollectedItem`s directly (`plugin.py`), so its items never pass through either walk in `importer.py`. The collection guard classifies a function object, and a `CollectedItem` carries a name rather than a function, so that point cannot reach them by construction.

The runtime point should, because the runner executes whatever callable the item resolves to. **This is reasoned, not measured** — no plugin `Collector` was exercised — and it is recorded here as an argument for the runtime point that stands independently of the wrapper case.

### 4. The exit code follows the class of the error, and the class splits

`docs/user/reference/exit-codes.md` already ships this shape for fixture wiring: a wiring error exits `4` at both points that can catch it, while the misspelt-fixture-name variant is refused at collection and exits `3`.

The same applies here. A refusal during collection **is** a collection error and exits `3`. A runtime result cannot vote `3` — `compute_exit_code` returns `CollectError` only when a module failed to collect, and a test that reached the runner did not — so the runtime point votes `4` through `usage_error`, which is what exit 4 already means: *the request itself was invalid*.

Exit 4 does **not** stop the run. Every sibling test still reports; only the final code changes.

Rejected: **build a second vote path so a runtime result can reach 3.** It would print `collection error` for a module that collected, which is false, in order to make a table read uniformly.

`TestReturnedValueError` is its own class rather than `UsageError` so it can be enrolled in `_USAGE_ERROR_TYPES` alone. `UsageError` is raised from code that runs during a test — `_fixture_session.py` among it — and enrolling the whole class would re-vote those to exit 4 as a side effect.

### 5. The item cache had to be invalidated

The item cache serves a file's collected items **without importing it**. A cache written before a collection guard exists keeps serving items the guard would now refuse, and an oxitest upgrade does not change a file's mtime, so the stale entry would survive indefinitely.

`CACHE_VERSION` is bumped 2 → 3, which discards every existing cache once. That is the only moment at which a new guard can see a previously-cached file.

**This generalises: bump `CACHE_VERSION` whenever a change makes collection refuse something it previously accepted.** The note is on the constant.

## Consequences

- Ships as `feat!:`. A suite containing a generator test **goes red**. That green was false: the body never ran.
- `python/tests` contained **zero** generator test functions before this change — AST scan, 534 files, with a positive control that found three in a probe — so this repo's own suite is unaffected.
- The `is_async`-is-`False`-for-async-generators defect is now unreachable rather than fixed. Refusing the shape at collection means nothing routes it to the wrong runner; a fix would be dead code.
- The three points are independently testable and were mutation-tested one at a time, each with a predicted failure named before the run.
