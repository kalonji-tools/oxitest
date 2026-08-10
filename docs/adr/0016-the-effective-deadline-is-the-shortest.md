# ADR-0016: The effective deadline is the shortest live deadline

**Status:** Accepted
**Date:** 2026-08-10

A **Deadline** is the time limit one test runs under. On Unix it is delivered by **one process-global timer** — `ITIMER_REAL`, raising `SIGALRM`. Before [#2001](https://github.com/kalonji-tools/oxitest/issues/2001), `_UnixTimeoutContext` used that timer as though it owned it exclusively: `__enter__` armed it and discarded whatever was already there, `__exit__` cancelled it outright.

Nothing owns that timer exclusively. Measured on `652af379`, Linux x86_64, CPython 3.12.13, with an ambient `timeout = 3`:

```
a test that nests a 1s run, then sleeps 20s  → PASSED after 20s
a test that calls signal.alarm(1); alarm(0)  → PASSED after 20s
@timeout(2) stacked over @timeout(20)        → an 8s body PASSED
```

Every row is a **passing test that never ran under the deadline it declared**. The first row is also the shape behind three CI occurrences across two platforms: with the deadline gone, a hang is unbounded, so the coordinator's watchdog kills the whole worker and errors every test in flight — naming none of them.

## The decisions

### 1. The effective deadline is the shortest of the live deadlines

Where more than one Deadline is live, the shortest wins. Nesting can never extend a Deadline that is already running.

Rejected: **honour the innermost as written**, which is what the code did by accident. It lets an inner 60-second deadline silently grant a test enclosed by a 5-second deadline another 55 seconds.

Rejected: **refuse to nest at all**. Cheapest to reason about, and unavailable: oxitest's own suite runs tests in-process from inside tests at ten sites. A rule the project must violate is not a rule.

### 2. One invariant, both platforms

The Unix arm reaches it by capping to a single timer. The Windows arm reaches it already, by accident of construction: `_WindowsTimeoutContext` builds a per-instance `threading.Timer`, so nesting runs two independent timers and the first to fire wins.

This is written down because it was true of one arm and undocumented in both. **No Windows behaviour changed under this ADR.**

### 3. Interference is detected, never prevented

The process timer is global and test code is entitled to write it. oxitest does not lock it and does not try. `__exit__` compares the timer against what it armed, and reports a mismatch.

The comparison is exact rather than heuristic: `ITIMER_REAL` counts down in wall time and `time.monotonic()` measures wall time, so the two are the same quantity and a run nobody interfered with reports zero drift — measured clean at a 0.02 s margin from the deadline. The tolerance absorbs the gap between two syscalls, nothing more.

A deadline that **fired** and a deadline that was **cancelled** both leave the timer at zero and cannot be told apart by arithmetic. `__exit__` therefore reads the in-flight exception, which says directly which happened.

### 4. A voided deadline warns; it does not fail

A test whose deadline was taken, and which otherwise passed, is reported **warned**.

oxitest did not observe a failure. It observed that it *could not observe*, and manufacturing a failure out of an absence is a worse contract than reporting the absence. `warned` is already an Outcome, and it is visible in default output — unlike a warning diagnostic, whose count prints but whose text needs `--warnings` and which names no test.

A test that failed on its own keeps its failure. The deadline is irrelevant to it.

### 5. One `@mark.timeout` per test

A second is refused where it is written, at import. The effective deadline makes stacking harmless, but two deadlines on one test is an authoring mistake, it is visible statically, and which of the two survived depended on decorator order.

## Known limits

Three, measured rather than assumed.

**An enclosing deadline that expires during a nested run is not reported.** A capped deadline can only fire when the enclosing one is also spent, so restoring it re-arms a timer that fires within microseconds — measured at 0.010 ms. That fire lands while the nested context's own `OxitestTimeoutError` is still propagating, and the wrapper's `except` cannot tell whose deadline raised, because both are the same exception type. The nested run reports its timeout and the enclosing test reports passed: measured 8/8, and unchanged by restoring the signal handler before re-arming rather than after — the attribution is lost in the wrapper, not at the handler. The case is narrow, it requires the enclosing deadline to expire *during* the nested run, and it is strictly better than the previous behaviour, where that deadline was destroyed outright. A deadline with time left after the nested run is unaffected and fires normally.

**The Windows message keeps the same imprecision, for the same reason.** With two independent timers, an enclosing deadline firing inside a nested context is caught by the nested wrapper and reported with the nested limit. Unix avoids this only because capping means the context that fired *is* the context that was armed. It cannot be fixed the way Unix is: `PyThreadState_SetAsyncExc` takes an exception **type**, not an instance, so nothing can ride the injected exception.

**The async blocking case is detected, not closed.** `asyncio.wait_for` cannot bound a coroutine that never yields, so once the OS timer is gone an async test that blocks has no deadline at all. This fix reports the loss; it cannot restore enforcement inside a blocking call.

## Consequences

- `signal.alarm` is gone from `_timeout.py`. Save-and-restore is not implementable on it: its read is destructive — `alarm(0)` returns the remaining seconds *and* cancels — and its granularity is integer seconds, so any sub-second remainder would restore as zero, which means cancel. `signal.setitimer`/`getitimer` drive the same `ITIMER_REAL` slot with a non-destructive read and float precision.
- A capped deadline reports the limit it enforced, and says it was capped. The uncapped message is unchanged, because that is every ordinary test.
- **This is the mirror of [#1998](https://github.com/kalonji-tools/oxitest/issues/1998) and [#2018](https://github.com/kalonji-tools/oxitest/issues/2018)**, which were Windows-only. This defect and its detection are Unix-only, because Windows has no shared slot and no `SIGALRM` for test code to write.
