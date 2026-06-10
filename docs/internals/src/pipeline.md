# Pipeline Deep Dive

The oxitest pipeline uses a **typestate pattern** to enforce phase ordering at compile time.
Every pipeline stage is a distinct Rust type, and each transition method consumes one type
to produce the next. Calling phases out of order is a compile error, not a runtime bug.

This chapter covers the mechanics in enough detail that you can add a new phase yourself.

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
| `strict_or_skip()` | `Collected` -> `PreFilter`           |   8   |    8    |           -            |          -           |
| `filter()`         | `PreFilter` -> `Ready`               |   9   |    9    |           -            |          -           |
| `execute()`        | `Ready` -> `Executed`                |  10   |   10    |           -            |          -           |
| `retry()`          | `Executed` -> `Executed`             |  11   |    -    |           -            |          -           |
| `finalize()`       | `Executed` -> `ExitCode`             |  12   |   11    |           -            |          -           |
| `query()`          | `SessionReady` -> `ExitCode`         |   -   |    -    |           4            |          -           |
| `query_without_session()` | `FilesCollected` -> `ExitCode` |   -   |    -    |           -            |          3           |

**\*** The `query` command skips `prescan` and `filter_metadata`. When it needs a session (for fixture/plugin queries or `--tree`), it calls `session()` directly on `FilesCollected` -- note there is a separate `session()` impl on `Pipeline<FilesCollected>` in `files_collected.rs`. When it does not need a session, it calls `query_without_session()` and never enters the Python runtime for session setup.

Here is the exact code from `run_command`:

```rust
fn run_command(py: Python<'_>, pipeline: Pipeline<Empty>) -> Result<ExitCode, ExitCode> {
    let p = pipeline.collect_files()?;
    let p = p.affected()?;
    let p = p.prescan()?;
    let p = p.filter_metadata()?;
    let p = p.session(py)?;
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.strict_or_skip(py)?;
    let p = p.filter(py)?;
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
    pipeline: Pipeline<Empty>,
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

## Pipeline\<S\> and PipelineShared

The core type is a two-field struct:

```rust
pub(crate) struct Pipeline<S> {
    pub(crate) shared: PipelineShared,
    pub(crate) state: S,
}
```

`S` is the typestate marker (e.g., `Empty`, `FilesCollected`, `Prescanned`, ...).
Each state type is a plain struct holding the data produced by the phase that created it.

### PipelineShared

`PipelineShared` carries data that lives for the entire pipeline run, regardless of which state the pipeline is in:

```rust
pub(crate) struct PipelineShared {
    pub(crate) cfg: config::Config,
    pub(crate) command: config::Command,
    pub(crate) rootdir: Utf8PathBuf,
    pub(crate) is_tty: bool,
    pub(crate) use_color: bool,
    pub(crate) base: reporter::ReporterOptsBuilder,
    pub(crate) cache: cache::TestCache,
    pub(crate) python_bin: String,
    pub(crate) ast_weight_ms: Option<f64>,
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
| `ast_weight_ms` | Sum of AST-derived body weights from prescan; `None` if prescan produced nothing |

**Deref trick.** `Pipeline<S>` implements `Deref<Target = PipelineShared>` and `DerefMut`, so
you can write `pipeline.cfg`, `pipeline.cache`, `pipeline.rootdir`, etc. directly on a
`Pipeline<AnyState>` without going through `.shared`:

```rust
impl<S> std::ops::Deref for Pipeline<S> {
    type Target = PipelineShared;
    fn deref(&self) -> &PipelineShared {
        &self.shared
    }
}

impl<S> std::ops::DerefMut for Pipeline<S> {
    fn deref_mut(&mut self) -> &mut PipelineShared {
        &mut self.shared
    }
}
```

This keeps phase code concise. For example, `self.cfg.strict` reads the config through Deref,
while `self.state.items` accesses state-specific data directly.

### Transition helpers

Two methods facilitate state transitions:

```rust
// Decompose Pipeline<S> into its shared half and state half.
impl<S> Pipeline<S> {
    fn into_parts(self) -> (PipelineShared, S) {
        (self.shared, self.state)
    }
}

// Wrap shared state back into a Pipeline with a new state type.
impl PipelineShared {
    fn into_pipeline<T>(self, state: T) -> Pipeline<T> {
        Pipeline { shared: self, state }
    }
}
```

Every phase follows the same three-step pattern:

1. Call `self.into_parts()` to consume the current pipeline and get `(shared, old_state)`.
2. Destructure `old_state` to extract what the next state needs, do the work.
3. Call `shared.into_pipeline(NewState { ... })` to produce the next pipeline.

## The State Types

Each state type lives in `src/pipeline/mod.rs` and is a plain `pub(crate) struct`. Here is the full chain:

| State              | Key fields                                           | Created by          |
|--------------------|------------------------------------------------------|---------------------|
| `Empty`            | (unit struct -- no fields)                            | `run()`             |
| `FilesCollected`   | `test_files`, `conftest_files`                       | `collect_files()`   |
| `Prescanned`       | `test_files`, `conftest_files`, `prescan_data`, `module_markers` | `prescan()` |
| `MetadataFiltered` | `test_files`, `conftest_files`, `modules_to_import`, `is_filtered` | `filter_metadata()` |
| `SessionReady`     | `test_files`, `conftest_files`, `session`, `session_violations` | `session()` |
| `Collected`        | `test_files`, `conftest_files`, `session`, `items`, `raw_violations`, `collection_profile` | `collect()` |
| `PreFilter`        | `test_files`, `conftest_files`, `session`, `clean_items`, `violated_items`, `all_violations`, `suite_lines` | `strict_or_skip()` |
| `Ready`            | `test_files`, `conftest_files`, `session`, `clean_items`, `violated_items`, `all_violations`, `suite_lines` | `filter()` |
| `Executed`         | `test_files`, `conftest_files`, `session`, `items`, `execution_results` | `execute()` |

## Phase Files

Each phase is implemented in its own file under `src/pipeline/phases/`:

```
src/pipeline/phases/
  mod.rs                 -- re-exports all phase modules
  empty.rs               -- Pipeline<Empty>::collect_files()
  files_collected.rs     -- Pipeline<FilesCollected>::affected(), prescan(),
                            session(), query_without_session()
  prescanned.rs          -- Pipeline<Prescanned>::filter_metadata()
  metadata_filtered.rs   -- Pipeline<MetadataFiltered>::session()
  session_ready.rs       -- Pipeline<SessionReady>::collect(), query()
  collected.rs           -- Pipeline<Collected>::validate(), strict_or_skip()
  pre_filter.rs          -- Pipeline<PreFilter>::filter()
  ready.rs               -- Pipeline<Ready>::execute()
  executed.rs            -- Pipeline<Executed>::retry(), finalize()
```

The convention is that the file is named after the **input state**, not the output state. For
example, `empty.rs` contains the transition *from* `Empty` (to `FilesCollected`).

Some states have multiple methods. `files_collected.rs` defines four methods on
`Pipeline<FilesCollected>`: `affected()`, `prescan()`, `session()`, and
`query_without_session()`. The `session()` on `FilesCollected` is a shortcut used by the
`query` command to skip the prescan/filter_metadata phases, while `metadata_filtered.rs`
defines the `session()` used by the `run` and `debug` commands.

## How Phases Skip or Abort

Phases do not use a "skip me" flag or Option wrapper. Instead, the codebase uses four patterns:

### Pattern 1: Identity transition (return the same state type)

When a phase is conditional and the condition is not met, it returns the pipeline unchanged
in the same state type. The caller does not know it was a no-op.

**Example -- `affected()`** returns `Result<Pipeline<FilesCollected>, ExitCode>` regardless:

```rust
impl Pipeline<FilesCollected> {
    pub(crate) fn affected(mut self) -> Result<Pipeline<FilesCollected>, ExitCode> {
        if let Some(base_ref) = self.cfg.affected.as_ref() {
            // ... filter test files based on git diff ...
            self.state.test_files = files;
        }
        Ok(self) // Same type whether or not --affected was active
    }
}
```

`validate()` (returns `Pipeline<Collected>`) and `retry()` (returns `Pipeline<Executed>`)
use the same pattern.

### Pattern 2: Pass through empty data

When the phase always transitions to a new state but the work is conditional, it fills the
new state with empty/default data when the condition is not met.

**Example -- `strict_or_skip()`** always transitions from `Collected` to `PreFilter`, but when
strict mode is off, it passes all items through as `clean_items` with empty violation lists:

```rust
if shared.cfg.strict.is_none() {
    return Ok(shared.into_pipeline(PreFilter {
        test_files,
        conftest_files,
        session,
        clean_items: items,
        violated_items: vec![],    // nothing violated
        all_violations: vec![],    // no violations
        suite_lines: vec![],       // no strict lines
    }));
}
```

### Pattern 3: Early abort via Err(ExitCode)

All phase methods return `Result<Pipeline<NextState>, ExitCode>`. When a phase needs to abort
the pipeline entirely, it returns `Err(ExitCode::...)`:

```rust
// In affected(): no files changed, nothing to test
if files.is_empty() {
    println!("no changes detected -- nothing to test");
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
    pub inprocess_groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
    pub arranged_groups: Vec<Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>>,
    pub parallel_groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
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
- **`arranged_groups`** -- Groups of tests that share session-scoped fixtures. These run
  serially on the main process to avoid fixture duplication across workers. Built by
  `partition_by_fixture_groups()` and subject to an `auto_arrange_threshold` check: if any
  single fixture group exceeds the threshold percentage of total tests, the entire plan falls
  back to serial to avoid starving the parallel pool.
- **`parallel_groups`** -- Everything else. When `strategy` is `Parallel`, these are dispatched
  to subprocess workers. When `Serial`, they run on the main process.

**The plan is pure.** `plan_execution()` takes all its inputs as parameters -- groups, flags,
worker count, estimated duration, CPU count, shared fixture groups, etc. -- and returns an
`ExecutionPlan` with no I/O and no PyO3 calls. This makes it fully testable in isolation (and
the test suite in `arrange.rs` exercises it extensively).

## How to Add a New Pipeline Phase

Suppose you want to add a `Linted` phase between `Collected` and `PreFilter` that runs a
lint pass over collected items.

### Step 1: Define the state type

In `src/pipeline/mod.rs`, add a new struct alongside the existing state types:

```rust
pub(crate) struct Linted {
    pub(crate) test_files: Vec<Utf8PathBuf>,
    pub(crate) conftest_files: Vec<Utf8PathBuf>,
    pub(crate) session: bridge::FixtureSession,
    pub(crate) items: Vec<Arc<types::TestItem>>,
    pub(crate) lint_warnings: Vec<String>,
}
```

### Step 2: Add the transition method

The convention is that transition methods live in the phase file named after the **input state**.
Since this transition consumes `Collected`, add it to `src/pipeline/phases/collected.rs`:

```rust
impl Pipeline<Collected> {
    pub(crate) fn lint(self) -> Result<Pipeline<Linted>, ExitCode> {
        let (shared, Collected {
            test_files, conftest_files, session, items, ..
        }) = self.into_parts();

        let lint_warnings = my_lint_pass(&items);

        Ok(shared.into_pipeline(Linted {
            test_files,
            conftest_files,
            session,
            items,
            lint_warnings,
        }))
    }
}
```

You will also need to add the import: `use super::super::Linted;` at the top of the file.

### Step 3: Update the downstream phase

The phase that previously consumed `Collected` -- in this case `strict_or_skip()` -- must now
consume `Linted` instead. Update its `impl` block signature and destructuring:

```rust
// Was: impl Pipeline<Collected>
impl Pipeline<Linted> {
    pub(crate) fn strict_or_skip(self, _py: Python<'_>)
        -> Result<Pipeline<PreFilter>, ExitCode>
    {
        let (shared, Linted { items, lint_warnings, .. }) = self.into_parts();
        // ...
    }
}
```

### Step 4: Register the module (if you created a new file)

If you put the transition in a new file instead of `collected.rs`, add it to
`src/pipeline/phases/mod.rs`:

```rust
mod linted;  // add this line
```

### Step 5: Wire it into the dispatcher

In `src/pipeline/mod.rs`, insert the call in the chain:

```rust
fn run_command(py: Python<'_>, pipeline: Pipeline<Empty>) -> Result<ExitCode, ExitCode> {
    // ...
    let p = p.collect(py)?;
    let p = p.validate(py)?;
    let p = p.lint()?;              // <-- new phase
    let p = p.strict_or_skip(py)?;
    // ...
}
```

If the compiler complains about a type mismatch, that is the typestate pattern working as
designed -- follow the error to find which transition needs its input type updated.

## ExecutionHarness Trait

`src/pipeline/traits.rs` defines a single trait seam used to abstract over execution strategies:

```rust
pub(crate) trait ExecutionHarness {
    fn execute_groups(
        &self,
        groups: Vec<(Utf8PathBuf, Vec<Arc<types::TestItem>>)>,
        rep: &mut dyn crate::reporter::Reporter,
    ) -> parallel::PhaseResult;
}
```

This lets tests swap in a mock harness that returns canned results without spawning real
subprocess workers or touching the Python runtime.
