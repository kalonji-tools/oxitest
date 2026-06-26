# Extending oxitest

This chapter walks through the five most common extension points:
CLI flags, subcommands, reporters, plugin protocols, and markers.
Each section follows the actual code paths with real examples from the codebase.

> **User guide:** See [Writing Plugins](../../site/how-to/write-plugins/) for a user-facing guide to authoring oxitest plugins.

---

## Adding a CLI flag

Every CLI flag touches four files. We will trace `--retries` as a concrete example.

### 1. Add the clap field in `src/config/cli.rs`

The `RunArgs` struct derives `clap::Args`. Add your flag there:

```rust
// src/config/cli.rs  (inside RunArgs)

/// Retry failed tests up to N times
#[arg(long, value_name = "N", help_heading = "Execution")]
pub retries: Option<usize>,
```

Key patterns:
- Use `Option<T>` so the flag is optional and `None` means "user did not pass it."
- `help_heading` groups the flag in `--help` output (Execution, Output, Filtering, Reports).
- Boolean flags use bare `bool` (e.g. `pub serial: bool`).
- Enum flags use `value_enum` (e.g. `pub strict: Option<StrictMode>`).
- Flags with optional values use `default_missing_value` plus `num_args = 0..=1` and `require_equals = true` (see `--strict` or `--keep-tmp` for examples).

If the flag belongs on both `RunArgs` **and** `DebugArgs`, add it to both or extract a shared `#[derive(clap::Args)]` group (like `FilteringArgs`, `FailedFilterArgs`, or `VerbosityArgs`).

### 2. Add the field to `Config` in `src/config/mod.rs`

```rust
// src/config/mod.rs  (inside Config struct)

pub retries: usize,
```

And set its default:

```rust
// src/config/mod.rs  (inside Config::default())

retries: 0,
```

### 3. Add the TOML field to `PyprojectConfig` in `src/config/pyproject.rs`

If the option is also settable via `pyproject.toml`:

```rust
// src/config/pyproject.rs  (inside OxitestConfig)

pub(super) retries: Option<usize>,
```

All fields in `OxitestConfig` are `Option<T>` -- absent TOML keys deserialize to `None`.

### 4. Wire the merge logic in `src/config/merge.rs`

The merge module has two entry points: `merge_toml` (pyproject values) and `merge_run_args` (CLI values). CLI always runs **after** TOML, so CLI wins.

For fields that appear in both TOML and CLI, use the `Overrides` struct:

```rust
// src/config/merge.rs  (inside Overrides struct)

retries: Option<usize>,
```

Then populate it in both `merge_toml` and `merge_run_args`:

```rust
// In merge_toml:
self.apply_overrides(Overrides {
    retries: tc.retries,
    // ...other fields...
});

// In merge_run_args:
self.apply_overrides(Overrides {
    retries: args.retries,
    // ...other fields...
});
```

`apply_overrides` uses the `apply_if_some!` macro:

```rust
macro_rules! apply_if_some {
    ($config:expr, $field:ident, $value:expr) => {
        if let Some(v) = $value {
            $config.$field = v;
        }
    };
    // "wrap" variant for Option<T> target fields:
    ($config:expr, $field:ident, $value:expr, wrap) => {
        if let Some(v) = $value {
            $config.$field = Some(v);
        }
    };
}
```

This pattern guarantees the precedence chain: **CLI > pyproject.toml > default**.

For fields unique to CLI (e.g. `--exitfirst`, `--serial`), handle them directly in `merge_run_args` before the shared `apply_overrides` call:

```rust
if args.serial {
    self.serial = true;
}
```

### 5. Use the field in the pipeline

Read from `config` wherever needed. For example, `config.retries` is read during test execution to decide whether to re-run a failed test.

### 6. Add validation if needed

If the new flag conflicts with others, add a check to `RunArgs::validate()`:

```rust
pub fn validate(&self) -> Result<(), String> {
    if self.exitfirst && self.maxfail.is_some() {
        return Err(
            "-x and --maxfail both control when to stop. Use one or the other.".to_string(),
        );
    }
    // ...
}
```

---

## Adding a subcommand

Subcommands are variants of the `Command` enum in `src/config/cli.rs`.

### 1. Define the args struct

```rust
// src/config/cli.rs

#[derive(clap::Args, Debug, Clone)]
pub struct MyNewArgs {
    /// Paths to test files or directories
    pub paths: Vec<Utf8PathBuf>,

    /// Your custom option
    #[arg(long)]
    pub my_option: bool,
}
```

### 2. Add the variant to `Command`

```rust
#[derive(clap::Subcommand, Debug, Clone)]
pub enum Command {
    Run(RunArgs),
    Debug(DebugArgs),
    Query(QueryArgs),
    Env,
    #[command(hide = true)]
    Completions { shell: clap_complete::Shell },
    /// Your new subcommand
    MyNew(MyNewArgs),
}
```

### 3. Add a merge method on `Config`

```rust
// src/config/merge.rs

pub fn merge_my_new_args(mut self, args: &MyNewArgs) -> Self {
    self.merge_paths(&args.paths);
    // apply any args-specific overrides
    self
}
```

### 4. Handle in the pipeline dispatch

The top-level dispatch lives in `src/pipeline/mod.rs`. It follows this pattern:

```rust
// Validation
match &command {
    config::Command::MyNew(a) => { /* validate */ }
    // ...
}

// Config merge
let cfg = match &command {
    config::Command::MyNew(args) => config::Config::load(&rootdir).merge_my_new_args(args),
    // ...
};

// Execution dispatch (after pipeline setup)
match &pipeline.command {
    config::Command::MyNew(_) => my_new_command(py, pipeline),
    // ...
}
```

Simple subcommands like `Env` short-circuit before pipeline setup:

```rust
if matches!(command, config::Command::Env) {
    println!("{}", env_string(py));
    return Ok(Err(ExitCode::Success));
}
```

### Reference: how `Query` is structured

`Query` is the most complex subcommand. It takes a positional `ResourceKind` enum (`tests`, `fixtures`, `marks`, `helpers`, `plugins`) and optional flags (`--fzf`, `--inspect`, `--format`, `--count`, `--tree`). Some resource kinds (`tests`, `marks`, `helpers`) work without a Python session (instant), while others (`fixtures`, `plugins`) require one. The pipeline dispatch checks this:

```rust
config::Command::Query(ref args) => {
    let needs_session =
        query::needs_python(args.resource, args.expression.as_deref()) || args.tree;
    query_command(py, pipeline, needs_session)
}
```

---

## Adding a reporter

### The `Reporter` trait

All reporters implement the `Reporter` trait defined in `src/reporter/traits.rs`:

```rust
pub trait Reporter {
    fn test_started(&mut self, item: &TestItem);
    fn test_completed(
        &mut self,
        item: &TestItem,
        outcome: &TestOutcome,
        duration_ms: DurationMs,
        parallel_ctx: Option<&ParallelContext>,
    );
    fn finish(
        &mut self,
        collect_errors: &[CollectError],
        interrupted: bool,
        session: &ReporterSession,
    ) -> ExitVote;

    // Optional hooks with default no-op implementations:
    fn record_teardown_warning(&mut self, _context: &str, _error: &str) {}
    fn set_fixture_cache_stats(&mut self, _hits: usize, _misses: usize, _breakdown: Vec<FixtureCacheEntry>) {}
    fn set_fixture_timings(&mut self, _timings: Vec<FixtureTimingEntry>) {}
}
```

The lifecycle is: `test_started` -> `test_completed` for each test, then a single `finish` call at the end.

### Built-in reporters

| Type | File | When used |
|------|------|-----------|
| `TtyReporter` | `src/reporter/tty.rs` | stdout is a TTY (interactive terminal) |
| `CiReporter` | `src/reporter/ci.rs` | stdout is not a TTY (CI, pipes) |
| `JsonReporter` | `src/reporter/json.rs` | `--json PATH` flag (CTRF format) |
| `JunitReporter` | `src/reporter/junit.rs` | `--junit-xml PATH` flag |
| `PyPluginReporter` | `src/reporter/plugin.rs` | Plugin provides a Python `Reporter` |

### How reporters are selected

The `make_reporter` function in `src/reporter/mod.rs` builds the reporter stack:

```rust
pub fn make_reporter(
    opts: ReporterOpts,
    is_tty: bool,
    json_path: Option<Utf8PathBuf>,
    junit_xml_path: Option<Utf8PathBuf>,
    plugin_reporters: Vec<Box<dyn Reporter>>,
) -> Box<dyn Reporter> {
    let primary: Box<dyn Reporter> = if is_tty {
        Box::new(TtyReporter::new(opts))
    } else {
        Box::new(CiReporter::new(opts))
    };

    let mut reporters = vec![primary];
    // add optional JSON, JUnit, plugin reporters...

    Box::new(CompositeReporter::new(reporters, strict_suite_count))
}
```

All reporters are wrapped in a `CompositeReporter` that dispatches every event to every inner reporter. On `finish`, it returns the maximum `ExitVote` across all reporters.

### `ExitVote`

```rust
pub enum ExitVote {
    Abstain,            // reporter does not influence exit code
    Code(ExitCode),     // reporter votes for this exit code
}
```

Side-channel reporters (JSON, JUnit, plugins) typically return `ExitVote::Abstain` -- they write output but do not determine the process exit code.

### `ReporterOpts`

Reporters receive a `ReporterOpts` struct built by `ReporterOptsBuilder`. It captures resolved config values: total test count, color mode, traceback style, verbosity, etc. Reporters read these fields directly.

### Adding a Rust-side reporter

1. Create `src/reporter/my_reporter.rs` implementing the `Reporter` trait.
2. Add `mod my_reporter;` to `src/reporter/mod.rs`.
3. Add a CLI flag (e.g. `--my-report PATH`) to `RunArgs` in `cli.rs`.
4. Pass it through to `make_reporter` and conditionally push it into the reporters vec.

### Adding a Python-side reporter (plugin)

Plugin reporters implement the `Reporter` protocol in `python/oxitest/plugin.py`:

```python
@runtime_checkable
class Reporter(Protocol):
    def test_started(self, item: Any) -> None: ...
    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None: ...
    def finish(self, collect_errors: list[Any], interrupted: bool) -> None: ...
```

The plugin's `oxitest_plugin()` function returns a `Plugin` dataclass with populated `reporters` list. Each Python reporter is wrapped in a `PyPluginReporter` (Rust side) that bridges calls via PyO3.

---

## Adding a plugin protocol

The plugin system is defined in two places:
- **Python API**: `python/oxitest/plugin.py` -- protocol classes and the `Plugin` dataclass
- **Plugin loader**: `python/oxitest/_bridge/plugin_loader.py` -- import, validation, registry

### Existing protocols

| Protocol | Python class | Eager/Lazy | Cardinality |
|----------|-------------|------------|-------------|
| `reporter` | `Reporter` | Eager | Many |
| `collector` | `Collector` | Eager | Many |
| `async_backend` | `AsyncBackend` | Eager | At most one |
| `coverage_provider` | `CoverageProvider` | Eager | At most one |
| `log_backend` | `LogBackend` | Lazy | Many |
| `fixture_provider` | `FixtureProvider` | Lazy | Many |
| `execution_wrapper` | `ExecutionWrapper` | Lazy | Many |
| `debugger_backend` | `DebuggerBackend` | Lazy | At most one |

**Eager** protocols must be loaded at session start (before test execution).
**Lazy** protocols are deferred until first use -- their plugin modules are not even imported until needed.

### How lazy import works

When loading plugins, `load_plugins()` checks each plugin's `plugin_settings.protocols` declaration:

```toml
[tool.oxitest.plugin_settings.my_plugin]
protocols = ["fixture_provider"]
```

If the declared protocols are all in `LAZY_PROTOCOLS`, the plugin gets a `PluginEntry.deferred()` entry. Its module is not imported until `ensure_loaded()` is called.

### Step-by-step: adding a new protocol

#### 1. Define the Python protocol class

In `python/oxitest/plugin.py`:

```python
@runtime_checkable
class MyProtocol(Protocol):
    def do_thing(self, arg: str) -> None: ...
```

#### 2. Add a field to the `Plugin` dataclass

```python
@dataclass
class Plugin:
    # ...existing fields...
    my_protocols: tuple[MyProtocol, ...] = ()
```

#### 3. Add a registry accessor in `plugin_loader.py`

In `PluginRegistry`:

```python
@functools.cached_property
def my_protocols(self) -> list[MyProtocol]:
    return list(
        itertools.chain.from_iterable(
            e.plugin.my_protocols for e in self.entries if e.plugin is not None
        )
    )
```

#### 4. Classify as eager or lazy

Add the protocol name to `EAGER_PROTOCOLS` or `LAZY_PROTOCOLS` in `plugin_loader.py`:

```python
LAZY_PROTOCOLS = frozenset({
    "log_backend",
    "fixture_provider",
    "execution_wrapper",
    "debugger_backend",
    "my_protocol",          # <-- add here
})
```

#### 5. Call from the Rust side via PyO3

In the appropriate pipeline phase or bridge module, call into the registry:

```rust
let my_protocols: Vec<PyObject> = py
    .eval("registry.my_protocols", ...)?
    .extract()?;
for proto in my_protocols {
    proto.call_method1(py, "do_thing", ("arg",))?;
}
```

#### 6. Add validation if needed

For at-most-one protocols (like `debugger_backend` or `coverage_provider`), add a check in `PluginRegistry.validate()`.

### Plugin CLI extensions

Plugins can declare CLI flags via a module-level `oxitest_cli_extension` attribute. oxitest discovers these during startup and adds them to the clap parser before re-parsing argv.

#### Init sequence

```
1. Rust: read pyproject.toml → plugin module names
2. Rust→Python: import each plugin module, read oxitest_cli_extension  [EAGER, CHEAP]
3. Rust: introspect config dataclasses → build dynamic clap args
4. Rust: re-parse CLI (static + plugin args)
5. Rust: validate + merge (pyproject + env + CLI) → populate plugin_settings
6. Rust→Python: call oxitest_plugin(config=typed_instance)              [DEFERRED]
```

Heavy plugin modules (`_plugin.py`) are never imported until step 6. The `__init__.py` re-exports `oxitest_cli_extension` cheaply and wraps `oxitest_plugin` with a lazy import.

#### Plugin-side declaration

```python
from oxitest import CliExtension, Both, Cli

@dataclass(frozen=True)
class MyConfig:
    host: Annotated[str, Both(help="Target host", short="H", env="MY_HOST")]
    verbose: Annotated[bool, Cli(help="Verbose output")] = False

oxitest_cli_extension = CliExtension(prefix="myplugin", config_type=MyConfig)
```

Source markers control where each field is read from:
- `Cli` -- CLI-only (no config file)
- `Conf` -- config file only (no CLI flag)
- `Both` -- both sources, CLI overrides config

#### Rust-side infrastructure

| Function | File | Purpose |
|----------|------|---------|
| `discover_plugin_cli()` | `src/bridge.rs` | Import plugins, read extensions, introspect config |
| `add_plugin_args()` | `src/config/cli.rs` | Add plugin flags to clap Command |
| `extract_plugin_values()` | `src/config/cli.rs` | Extract parsed plugin values from ArgMatches |
| `validate_prefix_uniqueness()` | `src/pipeline/mod.rs` | Error if two plugins claim the same prefix |

#### Prefix uniqueness

All plugin CLI args are namespaced: `--{prefix}-{field_name}`. Prefix uniqueness is validated at startup. Users can override the default prefix per plugin:

```toml
[tool.oxitest.plugin_settings.oxi_nixinfra]
cli_prefix = "nix"
```

### Plugin entry point contract

Every plugin module must expose an `oxitest_plugin(config=None)` function that returns a `Plugin` instance:

```python
from oxitest import Plugin

def oxitest_plugin(config=None):
    return Plugin(
        fixture_providers=[MyFixtureProvider()],
    )
```

---

## Adding a marker

Markers have two halves: Rust (collection-time validation) and Python (execution-time evaluation). An integration test enforces they stay in sync.

### 1. Add to `BUILTIN_MARKERS` in `src/filter.rs`

```rust
pub(crate) const BUILTIN_MARKERS: &[&str] =
    &["skip", "xfail", "usefixtures", "timeout", "inprocess"];
```

Add your marker name to this array. These names are exempt from the "unknown marker" validation -- users do not need to register them in `pyproject.toml`.

### 2. Add a Python handler in `python/oxitest/_bridge/_mark_registry.py`

Create a `MarkHandler` subclass:

```python
class _MyMarkerHandler(MarkHandler):
    mark_name = "my_marker"

    def handle(self, mark: MarkInfo, ctx: _HandlerContext) -> MarkEvalResult:
        # Option A: short-circuit (skip execution)
        return MarkEvalResult(short_circuit=TestResult.skipped("reason"))

        # Option B: wrap execution
        def wrapper(next_fn):
            result = next_fn()
            # transform result
            return result
        return MarkEvalResult(wrapper=wrapper)

        # Option C: side effects only (like usefixtures)
        return MarkEvalResult()
```

Register it in `_MARK_REGISTRY`:

```python
_MARK_REGISTRY: dict[str, MarkHandler] = {
    h.mark_name: h
    for h in [
        _UsefixturesHandler(),
        _SkipHandler(),
        _XFailHandler(),
        _TimeoutHandler(),
        _MyMarkerHandler(),    # <-- add here
    ]
}
```

### 3. The cross-language sync test

The integration test at `python/tests/integration/test_marker_sync.py` enforces that every Python handler name appears in the Rust `BUILTIN_MARKERS` constant:

```python
def test_python_markers_are_subset_of_rust() -> None:
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    missing_in_rust = python_markers - rust_markers
    assert not missing_in_rust

def test_no_unexpected_rust_only_markers() -> None:
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    rust_only = rust_markers - python_markers
    assert rust_only == {"inprocess"}
```

If you add a new handler in Python but forget to add it to `BUILTIN_MARKERS` in Rust, the first test fails. If you add a Rust-only marker (like `inprocess`), update the expected set in the second test.

### Special case: Rust-only markers

`inprocess` is a scheduling marker -- it tells the Rust scheduler to run the test in the main process rather than a worker. It has no Python handler because it is evaluated entirely on the Rust side before execution reaches Python. If your marker is purely a Rust-side scheduling hint, you only need step 1 (add to `BUILTIN_MARKERS`) and update the sync test expectation.

### User-registered markers

Non-builtin markers are registered in `pyproject.toml`:

```toml
[tool.oxitest]
markers = [
    "slow: marks tests as slow",
    "integration: integration tests",
]
```

These are parsed by `parse_marker_descriptions()` in `src/config/merge.rs`, which splits `"name: description"` into the registered name and tracks markers missing descriptions (for strict-mode warnings).
