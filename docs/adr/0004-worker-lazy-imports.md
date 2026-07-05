# ADR-0004: Keep worker subprocess bridge imports lazy for startup performance

**Status:** Accepted
**Date:** 2026-07-05

Worker subprocesses (`python -m oxitest._bridge.worker`) are spawned per-worker during parallel execution. Each worker imports the `worker` module at startup, then calls `run()` which imports the bridge modules (`executor`, `importer`, `conftest_loader`, `_test_meta`, `_runners`). During the PLC0415 lazy imports cleanup (#1242), we needed to decide whether these 5 bridge imports should move to top-level like the other 46 lazy imports.

## Considered Options

1. **Move all imports top-level unconditionally.** Uniform style, lint rule fully enforced with no exceptions. But worker startup is on the hot path — import time multiplies by worker count. Benchmarking showed a **+34ms delta** (62ms → 97ms median), a 55% increase per worker. With 8 workers, that's ~270ms added to every parallel test run.

2. **Keep bridge imports lazy inside `run()`, enforce PLC0415 via per-file ignore.** Worker module loads fast (only stdlib + typing at module level), then imports bridge modules on first `run()` call. The import cost is paid once per worker regardless, but after the subprocess is fully initialized and ready to process tasks — not blocking the spawn path. Per-file ignore in `pyproject.toml` suppresses PLC0415 for `worker.py`.

3. **Restructure worker into a thin shim + heavy module.** Split `worker.py` into `worker_main.py` (stdin/stdout loop, no bridge imports) and `worker_exec.py` (bridge imports at top-level, called lazily). Eliminates the lint exception but adds a file and an indirection for no functional benefit.

## Decision

Option 2. The 34ms delta is meaningful — it's a per-worker tax on every parallel run. The lazy imports inside `run()` are not a code smell here; they're a deliberate performance choice on a hot path. The per-file ignore in `pyproject.toml` documents the exception, and the inline comment in `run()` explains the rationale.

Option 3 was rejected because it adds structural complexity to solve a lint cosmetic — the opposite of the cleanup's intent.

## Consequences

- `worker.py` is the sole per-file PLC0415 exception in `pyproject.toml` (`__init__.py`'s Rust extension guard was removed — the `try/except ImportError` was dead code since maturin always bundles the extension)
- Future bridge module additions to the worker must also be imported inside `run()`, not at top-level
- If Python import performance improves (e.g., lazy module loading in future CPython), this decision can be revisited by re-running the benchmark
