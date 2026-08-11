# Pipeline Deep Dive

The oxitest pipeline uses a **runtime `PipelinePhase` enum** to track its current phase.
`Pipeline` is a non-generic struct; each transition method consumes the pipeline, guards on
the expected `PipelinePhase` variant with `let ... else { unreachable!(...) }`, and returns
a new `Pipeline` with the next variant. Calling phases out of order triggers a runtime panic
via the `unreachable!` guard.

This chapter covers the mechanics in enough detail that you can add a new phase yourself.

> **These guards are exception E1 and nothing else is.** `clippy::unreachable` is denied
> crate-wide; `src/pipeline/transitions/mod.rs` carries a module-level `#![allow]` for the
> phase destructures, listed as E1 in
> [ADR-0011](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0011-no-unhandled-panic-routes.md).
> A new phase added inside that module inherits the allow. Anywhere else -- including a
> guard *inside* the module that checks something other than the phase -- `unreachable!`
> fails the build, and the fix is a type or an error return, never a per-site `#[expect]`.

## Phase Sequence per Command

The pipeline dispatches in `run()` (in `src/pipeline/mod.rs`) based on the parsed `Command`
variant, then calls one of three functions: `run_command`, `debug_command`, or `query_command`.
Each chains a different subset of phases.

| Phase method       | State transition                     | `run` | `debug` | `query` (with session) | `query` (no session) |
|--------------------|--------------------------------------|:-----:|:-------:|:----------------------:|:--------------------:|
| `collect_files()`  | `Empty` -> `FilesCollected`          |   1   |    1    |           1            |          1           |
| `affected()`       | `FilesCollected` -> `FilesCollected` |   2   |    2    |           2            |          2           |
| `prescan()`        | `FilesCollected` -> `Prescanned`     |   3   |    3    |           -            |          -           |
| `filter_metadata()`| `Prescanned` -> `MetadataFiltered`   |   4   |    4    |           -            |          -           |
| `session()`        | `MetadataFiltered` -> `SessionReady` (or `FilesCollected` -> `SessionReady`) |   5   |    5    |           3\*          |          -           |
| `collect()`        | `SessionReady` -> `Collected`        |   6   |    6    |           -            |          -           |
| `validate()`       | `Collected` -> `Collected`           |   7   |    7    |           -            |          -           |
| `strict_or_skip()` | `Collected` -> `Ready`               |   8   |    8    |           -            |          -           |
| `execute()`        | `Ready` -> `Executed`                |   9   |    9    |           -            |          -           |
| `retry()`          | `Executed` -> `Executed`             |  10   |    -    |           -            |          -           |
| `finalize()`       | `Executed` -> `ExitCode`             |  11   |   10    |           -            |          -           |
| `query()`          | `SessionReady` -> `ExitCode`         |   -   |    -    |           4            |          -           |
| `query_without_session()` | `FilesCollected` -> `ExitCode` |   -   |    -    |           -            |          3           |

**\*** The `query` command skips `prescan` and `filter_metadata`. When it needs a session (for fixture/plugin queries or `--tree`), it calls `session()` directly from `FilesCollected` -- note there is a separate `session()` transition guarded by `PipelinePhase::FilesCollected` in `files_collected.rs`. When it does not need a session, it calls `query_without_session()` and never enters the Python runtime for session setup.

Here is the exact code from `run_command`:

```rust
fn run_command(py: Python<'_>, pipeline: Pipeline) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    let p = p.prescan()?;
    let p = p.filter_metadata()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.strict_or_skip(py)?;
    let p = p.execute(py)?;
    let p = p.retry(py)?;
    let result = p.finalize(py);
    result
}
```

`debug_command` is identical but omits `retry()`.
`query_command` branches on `needs_session`:

```rust
fn query_command(
    py: Python<'_>,
    pipeline: Pipeline,
    needs_session: bool,
) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    if needs_session {
        let p = p.session(py)?;
        p.query(py)
    } else {
        p.query_without_session(py)
    }
}
```

## Pipeline and PipelineShared

The core type is a two-field struct:

```rust
pub struct Pipeline {
    pub shared: PipelineShared,
    pub phase: PipelinePhase,
}
```

`PipelinePhase` is a runtime enum whose variants carry per-phase data (see below).

### PipelineShared

`PipelineShared` carries data that lives for the entire pipeline run, regardless of which phase the pipeline is in:

```rust
pub struct PipelineShared {
    pub cfg: config::Config,
    pub command: config::Command,
    pub rootdir: Utf8PathBuf,
    pub is_tty: bool,
    pub use_color: bool,
    pub base: reporter::ReporterOptsBuilder,
    pub cache: cache::TestCache,
    pub python_bin: String,
    pub ast_weight: Option<types::DurationMs>,
    pub test_files: Vec<Utf8PathBuf>,
    pub conftest_files: Vec<Utf8PathBuf>,
}
```

| Field           | Purpose |
|-----------------|---------|
| `cfg`           | Merged config from `pyproject.toml` + CLI flags |
| `command`       | Which subcommand is running (`Run`, `Debug`, `Query`, ...) |
| `rootdir`       | Canonical project root |
| `is_tty`        | Whether stdout is a terminal (controls progress bars) |
| `use_color`     | Whether to emit ANSI color codes |
| `base`          | Builder for reporter options, accumulated across phases |
| `cache`         | Timing/outcome cache for `--lf`/`--ff` and parallel scheduling |
| `python_bin`    | Path to the Python interpreter (`sys.executable`) |
| `ast_weight`    | Sum of AST-derived body weights from prescan; `None` if prescan produced nothing |
| `test_files`    | Discovered test file paths (persists across all phases) |
| `conftest_files`| Discovered conftest.py paths (persists across all phases) |

**Deref trick.** `Pipeline` implements `Deref<Target = PipelineShared>` and `DerefMut`, so
you can write `pipeline.cfg`, `pipeline.cache`, `pipeline.rootdir`, etc. directly on a
`Pipeline` without going through `.shared`:

```rust
impl std::ops::Deref for Pipeline {
    type Target = PipelineShared;
    fn deref(&self) -> &PipelineShared {
        &self.shared
    }
}

impl std::ops::DerefMut for Pipeline {
    fn deref_mut(&mut self) -> &mut PipelineShared {
        &mut self.shared
    }
}
```

This keeps phase code concise. For example, `self.cfg.strict` reads the config through Deref,
while phase-specific data is accessed by destructuring the `PipelinePhase` variant.

### Transition helpers

One method facilitates phase transitions:

```rust
// Decompose Pipeline into its shared half and phase half.
impl Pipeline {
    fn into_parts(self) -> (PipelineShared, PipelinePhase) {
        (self.shared, self.phase)
    }
}
```

Every transition follows the same three-step pattern:

1. Call `self.into_parts()` to consume the current pipeline and get `(shared, phase)`.
2. Guard on the expected `PipelinePhase` variant with `let ... else { unreachable!(...) }`, destructure its fields, and do the work.
3. Construct a new `Pipeline { shared, phase: PipelinePhase::NextVariant { ... } }`.

## The PipelinePhase Enum

All phase variants are defined in `src/pipeline/mod.rs` as variants of a single `pub enum PipelinePhase`. Each variant carries the data that was previously held by separate typestate structs:

| Variant            | Key fields                                           | Created by          |
|--------------------|------------------------------------------------------|---------------------|
| `Empty`            | (unit -- no fields)                                   | `run()`             |
| `FilesCollected`   | (unit -- `test_files`/`conftest_files` live in `PipelineShared`) | `collect_files()` |
| `Prescanned`       | `prescan_data`, `module_markers`                     | `prescan()`         |
| `MetadataFiltered` | `modules_to_import`                                  | `filter_metadata()` |
| `SessionReady`     | `session`, `session_violations`                      | `session()`         |
| `Collected`        | `session`, `items`, `raw_violations`                 | `collect()`         |
| `Ready`            | `session`, `clean_items`, `violated_items`, `all_violations`, `suite_lines` | `strict_or_skip()` |
| `Executed`         | `session`, `items`, `execution_results`              | `execute()`         |

## Transition Files

Each transition is implemented in its own file under `src/pipeline/transitions/`:

```
src/pipeline/transitions/
  mod.rs                 -- shared helpers and module declarations
  empty.rs               -- from Empty: collect_files()
  files_collected.rs     -- from FilesCollected: affected(), prescan(),
                            session(), query_without_session()
  prescanned.rs          -- from Prescanned: filter_metadata()
  session_ready.rs       -- from SessionReady: collect(), query()
  collected.rs           -- from Collected: validate(), strict_or_skip()
  ready.rs               -- from Ready: execute()
  executed.rs            -- from Executed: retry(), finalize()
```

The convention is that the file is named after the **input phase**, not the output phase. For
example, `empty.rs` contains the transition *from* `Empty` (to `FilesCollected`).

Some phases have multiple methods. `files_collected.rs` defines four methods on
`Pipeline` guarded by `PipelinePhase::FilesCollected`: `affected()`, `prescan()`, `session()`,
and `query_without_session()`. The `session()` from `FilesCollected` is a shortcut used by the
`query` command to skip the prescan/filter_metadata phases, while `prescanned.rs`'s
`filter_metadata()` feeds into the `session()` used by the `run` and `debug` commands.

## How Phases Skip or Abort

Phases do not use a "skip me" flag or Option wrapper. Instead, the codebase uses four patterns:

### Pattern 1: Identity transition (return the same state type)

When a phase is conditional and the condition is not met, it returns the pipeline unchanged
in the same state type. The caller does not know it was a no-op.

**Example -- `affected()`** returns `Result<Pipeline, ExitCode>` with `PipelinePhase::FilesCollected` regardless:

```rust
impl Pipeline {
    pub fn affected(mut self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::FilesCollected = self.phase else {
            unreachable!("affected called outside FilesCollected phase");
        };
        if let Some(base_ref) = self.cfg.affected.as_ref() {
            // ... filter test files based on git diff ...
        }
        Ok(self) // Same phase whether or not --affected was active
    }
}
```

`validate()` (stays in `Collected`) and `retry()` (stays in `Executed`)
use the same pattern.

### Pattern 2: Pass through empty data

When the phase always transitions to a new state but the work is conditional, it fills the
new state with empty/default data when the condition is not met.

**Example -- `strict_or_skip()`** always transitions from `Collected` to `Ready`, but when
strict mode is off, it passes all items through as `clean_items` with empty violation lists:

```rust
if shared.cfg.strict.is_none() {
    return Ok(Pipeline {
        shared,
        phase: PipelinePhase::Ready {
            session,
            clean_items: items,
            violated_items: vec![],    // nothing violated
            all_violations: vec![],    // no violations
            suite_lines: vec![],       // no strict lines
        },
    });
}
```

### Pattern 3: Early abort via Err(ExitCode)

All transition methods return `Result<Pipeline, ExitCode>`. When a phase needs to abort
the pipeline entirely, it returns `Err(ExitCode::...)`:

```rust
// In affected(): no files changed, nothing to test
if files.is_empty() {
    eprintln!("affected: 0 of {} test files selected [base: {}]", ...);
    return Err(ExitCode::Success);
}

// In collect_files(): bad glob pattern
Err(ExitCode::UsageError)

// In strict_or_skip(): abort mode with violations
if shared.cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
    reporter::print_strict_abort(&abort_lines, shared.use_color);
    return Err(ExitCode::CollectError);
}
```

The caller (e.g., `run_command`) uses `?` to propagate. In `run()`, both `Ok(code)` and
`Err(code)` are collapsed to the same integer exit code:

```rust
match result {
    Ok(code) | Err(code) => Ok(code.as_i32()),
}
```

### Pattern 4: Conditional chain in the dispatcher

The `query_command` function uses a Rust `if` to choose between two completely different
pipeline paths at the call site rather than inside a phase:

```rust
if needs_session {
    let p = p.session(py)?;
    p.query(py)
} else {
    p.query_without_session(py)
}
```

This is how the `query` command skips the prescan/filter_metadata/collect/strict/filter/execute
phases entirely -- they are simply not called.

## ExecutionPlan

`ExecutionPlan` is a pure value object in `src/pipeline/arrange.rs`. It bundles all scheduling
decisions into a single struct before any I/O happens. The `execute()` phase in `ready.rs`
delegates to the `execution` module, which calls `plan_execution()` to build this plan, then
dispatches based on it.

```rust
pub(super) struct ExecutionPlan {
    pub strategy: ExecutionStrategy,
    pub inprocess_groups: Vec<ModuleGroup>,
    pub arranged_groups: Vec<Vec<ModuleGroup>>,
    pub parallel_groups: Vec<ModuleGroup>,
}

pub(super) enum ExecutionStrategy {
    Serial,
    Parallel { worker_count: usize },
}
```

**Fields:**

- **`strategy`** -- `Serial` or `Parallel { worker_count }`. Determined by whether parallelism
  makes sense given estimated duration, spawn overhead, and CPU count.
- **`inprocess_groups`** -- Tests marked with `@oxi.mark.inprocess`. These always run on the
  main Python process, never in subprocess workers. Partitioned out first by
  `partition_inprocess_groups()`.
- **`arranged_groups`** -- Groups of tests that named a fixture in `@oxi.arrange`. These run
  serially on the main process. Built by `partition_by_fixture_groups()` over the connected
  components of the arranged names. No threshold guards this: before #1848 the component set
  was inferred from a lifetime tier and could swallow a suite nobody had asked to serialise,
  so a ratio fallback existed to catch that. A component now exists only where a test asked
  for one.
- **`parallel_groups`** -- Everything else. When `strategy` is `Parallel`, these are dispatched
  to subprocess workers. When `Serial`, they run on the main process.

**The plan is pure.** `plan_execution()` takes all its inputs as parameters -- groups, flags,
worker count, estimated duration, CPU count, shared fixture groups, etc. -- and returns an
`ExecutionPlan` with no I/O and no PyO3 calls. This makes it fully testable in isolation (and
the test suite in `arrange.rs` exercises it extensively).

## How to Add a New Pipeline Phase

Suppose you want to add a `Linted` phase between `Collected` and `Ready` that runs a
lint pass over collected items.

### Step 1: Add a variant to PipelinePhase

In `src/pipeline/mod.rs`, add a new variant to the `PipelinePhase` enum:

```rust
pub enum PipelinePhase {
    // ...existing variants...
    /// Lint pass complete; holds lint warnings alongside collected data.
    Linted {
        session: bridge::FixtureSession,
        items: Vec<Arc<types::TestItem>>,
        raw_violations: Vec<bridge::RawViolation>,
        lint_warnings: Vec<String>,
    },
}
```

### Step 2: Add the transition method

The convention is that transition methods live in the file named after the **input phase**.
Since this transition starts from `Collected`, add it to `src/pipeline/transitions/collected.rs`:

```rust
impl Pipeline {
    pub fn lint(self) -> Result<Pipeline, ExitCode> {
        let (shared, phase) = self.into_parts();
        let PipelinePhase::Collected { session, items, raw_violations } = phase else {
            unreachable!("lint called outside Collected phase");
        };

        let lint_warnings = my_lint_pass(&items);

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Linted {
                session,
                items,
                raw_violations,
                lint_warnings,
            },
        })
    }
}
```

### Step 3: Update the downstream transition

The transition that previously guarded on `Collected` -- in this case `strict_or_skip()` -- must
now guard on `Linted` instead. Update its `let ... else` destructuring:

```rust
impl Pipeline {
    pub fn strict_or_skip(self, _py: Python<'_>)
        -> Result<Pipeline, ExitCode>
    {
        let (shared, phase) = self.into_parts();
        let PipelinePhase::Linted { session, items, lint_warnings, .. } = phase else {
            unreachable!("strict_or_skip called outside Linted phase");
        };
        // ...
    }
}
```

### Step 4: Register the module (if you created a new file)

If you put the transition in a new file instead of `collected.rs`, add it to
`src/pipeline/transitions/mod.rs`:

```rust
mod linted;  // add this line
```

### Step 5: Wire it into the dispatcher

In `src/pipeline/mod.rs`, insert the call in the chain:

```rust
fn run_command(py: Python<'_>, pipeline: Pipeline) -> Result<ExitCode, ExitCode> {
    // ...
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.lint()?;              // <-- new phase
    let p = p.strict_or_skip(py)?;
    // ...
}
```

If the `unreachable!` guard fires at runtime, that means the call chain is wrong --
check which transition feeds into your new phase and ensure the ordering is correct.

## ExecutionDispatch Enum

`src/pipeline/execution.rs` defines an enum that dispatches serial vs. parallel execution
without dynamic dispatch:

```rust
pub(super) enum ExecutionDispatch<'a> {
    /// Runs tests in-process, one at a time.
    Serial {
        py: Python<'a>,
        session: &'a bridge::FixtureSession,
        cache: &'a cache::TestCache,
        timeout_secs: Option<u64>,
        timeout_multiplier: Option<f64>,
        maxfail: usize,
        opts: DebugOptions<'a>,
    },
    /// Delegates to worker subprocesses.
    Parallel {
        cfg: &'a config::Config,
        workers: usize,
        conftest_files: &'a [Utf8PathBuf],
        python_bin: &'a str,
        pool: Option<Vec<parallel::PrewarmedWorker>>,
    },
}
```

Each variant carries the data it needs directly. The `execute_groups()` method matches on the
variant and runs the appropriate path -- `Serial` loops over tests in-process, `Parallel`
delegates to `parallel::run_phase_parallel()`. This replaces the former `ExecutionHarness` trait
and its `SerialHarness`/`ParallelHarness` implementers, eliminating dynamic dispatch overhead.
