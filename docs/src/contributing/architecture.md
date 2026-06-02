# Architecture

!!! abstract "Contributing"
    A map of oxitest's internal modules and pipeline execution order.

## Architecture layers

oxitest has two layers: a Rust core that orchestrates the full test pipeline, and a Python
bridge that handles module import and test execution. The layers communicate via PyO3
(in-process) and stdio JSON (parallel workers).

```mermaid
graph TD
    subgraph CLI["CLI Entry Point"]
        A["python -m oxitest"]
    end

    subgraph Rust["Rust Core (src/)"]
        LIB["lib.rs<br/>PyO3 entry point"]
        CONFIG["config/<br/>CLI + pyproject.toml"]
        PIPELINE["pipeline/<br/>11-phase orchestrator"]

        subgraph Discovery
            COLLECTOR["collector.rs<br/>filesystem walk"]
            AFFECTED["affected.rs<br/>git-aware filtering"]
        end

        subgraph Filtering
            FILTER["filter.rs<br/>-k / -m / --failed=only / --failed=first"]
            MARKER["marker.rs<br/>boolean expression parser"]
            STRICT["strict.rs<br/>violation checking"]
        end

        subgraph Scheduling
            CACHE["cache.rs<br/>timing cache + 3 traits"]
            SCHEDULER["scheduler.rs<br/>work-stealing"]
        end

        subgraph Execution
            PARALLEL["parallel.rs<br/>subprocess worker pool"]
            WORKER_RESULT["worker_result.rs<br/>JSON wire contract"]
            RETRY["retry.rs<br/>flaky detection"]
        end

        subgraph Reporting
            REPORTER["reporter/<br/>tty · ci · json · junit"]
            FORMAT["format/<br/>diagnostics · diffs · suggestions"]
        end

        TYPES["types.rs<br/>TestItem · TestOutcome · NodeId"]
        BRIDGE["bridge.rs<br/>PyO3 ↔ Python calls"]
    end

    subgraph Python["Python Bridge (python/oxitest/_bridge/)"]
        IMPORTER["importer.py<br/>collect test functions"]
        EXECUTOR["executor.py<br/>run single test"]
        WORKER["worker.py<br/>subprocess entry point"]
        CONFTEST["conftest_loader.py<br/>fixture registration"]
        AST["_assert_error.py<br/>assertion error types"]

        subgraph Fixtures
            FX_SESSION["_fixture_session.py<br/>resolution + lifecycle"]
            FX_REG["_fixture_registry.py<br/>FixtureDef registry"]
            FX_PUB["fixtures.py<br/>public API"]
        end

        subgraph Marks
            MARK_API["_mark_api.py<br/>mark · skip"]
            MARK_REG["_mark_registry.py<br/>evaluate_marks"]
        end

        RESULT["result.py<br/>TestResult + to_wire()"]
        PLUGIN["plugin_loader.py<br/>plugin registry"]
    end

    A --> LIB
    LIB --> CONFIG
    LIB --> PIPELINE
    PIPELINE --> Discovery
    PIPELINE --> Filtering
    PIPELINE --> Scheduling
    PIPELINE --> Execution
    PIPELINE --> Reporting

    BRIDGE --> IMPORTER
    BRIDGE --> EXECUTOR
    BRIDGE --> CONFTEST
    PARALLEL -->|"stdio JSON"| WORKER
    WORKER --> EXECUTOR
    WORKER --> RESULT
    RESULT -->|"JSON lines"| WORKER_RESULT

    style CLI fill:#f5f5f5,stroke:#333
    style Rust fill:#fef3e2,stroke:#e67e22
    style Python fill:#e8f4fd,stroke:#2980b9
```

## Pipeline flow

The pipeline is a sequence of 11 phases, each implementing the `PipelinePhase` trait.
Phases can be conditionally skipped (`should_run`) and may exit early (`PhaseOutcome::Abort`).

```mermaid
flowchart TD
    START(["run()"])

    FC["1. FileCollection<br/>walk filesystem → test files + conftests"]
    AF{"2. Affected<br/>--affected flag?"}
    AF_Y["filter to git-changed files"]
    SE["3. Session<br/>import conftests → FixtureSession"]
    FX{"4. Fixtures<br/>fixtures subcommand?"}
    FX_Y["list fixtures → exit"]
    CO["5. Collection<br/>import modules → TestItem list"]
    ST{"6. Strict<br/>strict mode?"}
    ST_Y["check violations → abort if any"]
    FI["7. Filter<br/>apply -k / -m / --failed=only / --failed=first"]
    LI{"8. List<br/>--list?"}
    LI_Y["print items → exit"]

    EX["9. Execution"]
    DECIDE{"warm cache?"}
    SERIAL["Serial<br/>run in-process via bridge"]
    PAR["Parallel<br/>spawn N worker subprocesses"]
    PAR_DETAIL["scheduler distributes groups<br/>workers stream JSON results"]

    RT{"10. Retry<br/>retries > 0 &<br/>failures exist?"}
    RT_Y["re-run failed tests serially<br/>mark pass-on-retry as Flaky"]

    FIN["11. Finalize<br/>merge timings → save cache<br/>reporter.finish() → exit code"]

    DONE(["exit"])

    START --> FC
    FC --> AF
    AF -->|yes| AF_Y --> SE
    AF -->|no| SE
    SE --> FX
    FX -->|yes| FX_Y --> DONE
    FX -->|no| CO
    CO --> ST
    ST -->|yes| ST_Y
    ST_Y -->|violations| DONE
    ST_Y -->|clean| FI
    ST -->|no| FI
    FI --> LI
    LI -->|yes| LI_Y --> DONE
    LI -->|no| EX

    EX --> DECIDE
    DECIDE -->|"serial: est ≤ overhead × workers"| SERIAL
    DECIDE -->|"parallel: est > overhead × workers"| PAR
    PAR --> PAR_DETAIL
    SERIAL --> RT
    PAR_DETAIL --> RT

    RT -->|yes| RT_Y --> FIN
    RT -->|no| FIN
    FIN --> DONE

    style START fill:#4CAF50,color:#fff
    style DONE fill:#4CAF50,color:#fff
    style FC fill:#fff3e0
    style SE fill:#fff3e0
    style CO fill:#fff3e0
    style EX fill:#fff3e0
    style FIN fill:#fff3e0
    style SERIAL fill:#e3f2fd
    style PAR fill:#e3f2fd
    style PAR_DETAIL fill:#e3f2fd
    style RT_Y fill:#fff9c4
    style FX_Y fill:#f3e5f5
    style LI_Y fill:#f3e5f5
```

**Parallel vs serial decision:**

- *Cold cache* (no timing data): run serially if collected test count is below `min_parallel_tests` (default: 100).
- *Warm cache* (timing data available): run serially if estimated total duration ≤ `spawn_overhead_ms × worker_count`.

When running in parallel, `src/parallel.rs` spawns subprocess workers (`python -m oxitest._bridge.worker`).
Each worker receives test groups over stdin as JSON, executes them, and streams results back over stdout
as JSON. The work-stealing scheduler in `src/scheduler.rs` distributes groups and collects results —
all in Rust, without GIL contention at the coordination layer.

## Module reference

| Module | File | Responsibility |
|--------|------|----------------|
| `lib` | `src/lib.rs` | Entry point — delegates to `pipeline::run()` |
| `pipeline` | `src/pipeline/` | Pipeline orchestrator (`mod.rs`): discovery → execution → cache update. Trait seams (`traits.rs`): `Session`, `ModuleCollector`, `TestRunner`, `ParallelRunner`. `ExecutionContext` bundles config/cache/session/conftest for the run phase. |
| `config` | `src/config/` | `mod.rs`: shared types + `Config` struct. `cli.rs`: CLI parsing (`clap`). `pyproject.rs`: TOML deserialization. |
| `collector` | `src/collector.rs` | File system walk: finds test files and conftest files |
| `bridge` | `src/bridge.rs` | PyO3 bridge: imports Python modules, collects `TestItem` list, runs individual tests. `_with_session_obj` variants take `Bound<'_, PyAny>`. |
| `types` | `src/types.rs` | Core data types: `TestItem`, `TestOutcome`, `OutcomeKind`, `NodeId`, `CollectError`, `TestTiming`, `Frame`, `FailureAccumulator` |
| `worker_result` | `src/worker_result.rs` | Worker subprocess JSON contract: `WorkerResult` (receive), `WorkerTask`/`WorkerTaskItem` (send) |
| `filter` | `src/filter.rs` | Keyword filtering, marker name validation, `--failed=only`/`--failed=first` logic, `group_by_module` |
| `marker` | `src/marker.rs` | Marker expression parser and evaluator (`and`/`or`/`not`) |
| `cache` | `src/cache.rs` | Timing cache: load/save `timings.json`, invalidation, duration estimation, `sort_groups` |
| `scheduler` | `src/scheduler.rs` | Work-stealing scheduler; preserves insertion order; cache pre-sorts groups by duration |
| `parallel` | `src/parallel.rs` | Subprocess worker pool: spawns `python -m oxitest._bridge.worker`, communicates over stdio JSON |
| `strict` | `src/strict.rs` | Strict-mode violation checking: bare asserts, dict parametrize, missing mark reason, unregistered markers |
| `reporter` | `src/reporter/` | Terminal output (`tty.rs`, `ci.rs`), JSON output (`json.rs`), plugin reporters (`plugin.rs`), exit codes (`exit.rs` + `ExitVote` enum), progress bars, timing summaries. Formatting in `format/` (diagnostics, summaries). |
