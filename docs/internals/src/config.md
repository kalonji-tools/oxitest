# Config System

oxitest configuration follows a three-layer merge chain:
**CLI flags > pyproject.toml > compiled defaults**.
This chapter explains the merge mechanics, the data structures involved,
and how to add a new config option end-to-end.

> **User guide:** See [Configuration Reference](../../site/reference/configuration/) for the user-facing configuration options.

---

## Merge chain

### Priority order

1. **CLI flags** -- highest priority. If the user passes `--timeout 10`, it wins.
2. **pyproject.toml** -- `[tool.oxitest]` section. Applied first during `Config::load`.
3. **Compiled defaults** -- `Config::default()`. Used when neither CLI nor TOML provides a value.

### How it works in code

Config construction happens in three phases inside `src/pipeline/mod.rs`:

```rust
// Phase 1: Core CLI parse + config load
let (command, _) = config::OxitestCli::resolve(&argv)?;
let cfg = config::Config::load(&rootdir);
let cfg = match &command {
    config::Command::Run(args) => cfg.merge_run_args(args),
    config::Command::Debug(args) => cfg.merge_debug_args(args),
    config::Command::Query(args) => cfg.merge_query_args(args),
    // ...
};

// Phase 2: Plugin CLI extension discovery (if plugins configured)
// Imports plugin modules, reads oxitest_cli_extension attributes,
// rebuilds clap with plugin-specific flags, re-parses argv,
// and merges plugin CLI values into plugin_settings.
let extensions = bridge::discover_plugin_cli(py, &cfg.features.plugins, ...)?;
if !extensions.plugins.is_empty() {
    validate_prefix_uniqueness(&extensions)?;
    let extended_cmd = config::cli::add_plugin_args(base_cmd, &extensions);
    let matches = extended_cmd.try_get_matches_from(&argv)?;
    let plugin_values = config::cli::extract_plugin_values(&matches, &extensions);
    // CLI values merged into cfg.features.plugin_settings (CLI > pyproject)
}
```

`Config::load` (in `src/config/mod.rs`) reads `pyproject.toml`, deserializes it into a `PyprojectToml` -> `OxitestConfig`, then calls `config.merge_toml(tc, rootdir)`.

The merge methods (`merge_run_args`, `merge_debug_args`, etc.) in `src/config/merge.rs` apply CLI values on top.

Plugin CLI values follow the same precedence: **CLI > env > pyproject > default**. See the plugin CLI extension section in `docs/internals/src/extending.md` for details.

### The `apply_if_some!` pattern

Both TOML and CLI sources use `Option<T>` for every overridable field. The `apply_if_some!` macro applies a value only when `Some`:

```rust
macro_rules! apply_if_some {
    // For T target fields:
    ($config:expr, $field:ident, $value:expr) => {
        if let Some(v) = $value {
            $config.$field = v;
        }
    };
    // For Option<T> target fields:
    ($config:expr, $field:ident, $value:expr, wrap) => {
        if let Some(v) = $value {
            $config.$field = Some(v);
        }
    };
}
```

### The `Overrides` struct

Fields that can be set by **both** TOML and CLI are collected in an `Overrides` struct:

```rust
#[derive(Default)]
struct Overrides {
    schedule: Option<ScheduleStrategy>,
    retries: Option<usize>,
    retries_delay_secs: Option<u64>,
    failed: Option<FailedMode>,
    tb: Option<TbStyle>,
    color: Option<ColorMode>,
    durations: Option<usize>,
    strict: Option<StrictMode>,
    keep_tmp: Option<KeepTmpMode>,
    show_locals: Option<bool>,
    show_internals: Option<bool>,
}
```

Both `merge_toml` and `merge_run_args` build an `Overrides` and call `Config::apply_overrides`. Because CLI runs after TOML, CLI values overwrite TOML values. When the CLI field is `None` (user did not pass the flag), the TOML value survives.

---

## How to add a config option

### End-to-end checklist

This example adds a hypothetical `my_threshold` option.

#### 1. Add to `OxitestConfig` in `src/config/pyproject.rs`

```rust
#[derive(Deserialize, Default)]
pub(super) struct OxitestConfig {
    // ...existing fields...
    pub(super) my_threshold: Option<u32>,
}
```

All fields are `Option<T>` with serde default -- absent TOML keys become `None`.

For custom types, implement `serde::Deserialize`. See `WorkerCount` in `pyproject.rs` for a complex example (accepts both `"auto"` string and integer).

#### 2. Add to the appropriate sub-config in `src/config/mod.rs`

`Config` is composed of nested sub-structs: `PathConfig`, `ExecConfig`, `OutputConfig`, `MarkerConfig`, `FilterConfig`, `FeatureConfig`. Add your field to whichever sub-struct it belongs to:

```rust
// For an execution-related field, add to ExecConfig:
pub struct ExecConfig {
    // ...existing fields...
    pub my_threshold: u32,
}

impl Default for ExecConfig {
    fn default() -> Self {
        Self {
            // ...existing fields...
            my_threshold: 100,  // compiled default
        }
    }
}
```

Access it as `config.exec.my_threshold` (not `config.my_threshold`).

#### 3. Add CLI flag in `src/config/cli/run.rs`

```rust
// Inside RunArgs:
/// My threshold value
#[arg(long, value_name = "N", help_heading = "Execution")]
pub my_threshold: Option<u32>,
```

#### 4. Add merge logic in `src/config/merge.rs`

**Option A: Shared override** (TOML and CLI both set it via `Overrides`):

Add the field to `Overrides`, `apply_overrides`, `merge_toml`, and `merge_run_args`.

**Option B: Separate handling** (TOML-only or CLI-only field):

For TOML-only fields, handle in `merge_toml` directly:

```rust
apply_if_some!(self, my_threshold, tc.my_threshold);
```

For CLI-only fields, handle in `merge_run_args` directly:

```rust
apply_if_some!(self, my_threshold, args.my_threshold);
```

#### 5. Test it

Add unit tests in `src/config/merge.rs` (for merge behavior) and `src/config/mod.rs` (for defaults and TOML parsing):

```rust
#[test]
fn test_my_threshold_default() {
    let cfg = Config::default();
    assert_eq!(cfg.exec.my_threshold, 100);
}

#[test]
fn test_my_threshold_from_pyproject() {
    let cfg = Config::from_str("[tool.oxitest]\nmy_threshold = 50\n").unwrap();
    assert_eq!(cfg.exec.my_threshold, 50);
}

#[test]
fn test_my_threshold_cli_overrides_pyproject() {
    let dir = TempDir::new().unwrap();
    fs::write(dir.path().join("pyproject.toml"), "[tool.oxitest]\nmy_threshold = 50\n").unwrap();
    let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
    let args = parse_run(&["--my-threshold", "200"]);
    let merged = cfg.merge_run_args(&args);
    assert_eq!(merged.exec.my_threshold, 200);
}
```

---

## Config struct fields

`Config` is composed of nested sub-structs. Access fields as `config.<sub>.<field>` (e.g. `config.exec.maxfail`):

| Sub-struct | Field | Type | Default | CLI flag | TOML key |
|------------|-------|------|---------|----------|----------|
| *(top)* | `rootdir` | `Utf8PathBuf` | `"."` | (auto-detected) | -- |
| **`paths`** | `testpaths` | `Vec<Utf8PathBuf>` | `["."]` | positional args | `testpaths` |
| | `python_files` | `Vec<String>` | `["test_*.py", "*_test.py"]` | -- | `python_files` |
| | `norecursedirs` | `Vec<String>` | `.git`, `__pycache__`, `.venv`, etc. | -- | `norecursedirs` |
| | `use_gitignore` | `bool` | `true` | `--no-use-gitignore` | `use_gitignore` |
| | `doctest_modules` | `bool` | `false` | `--doctest-modules` | `doctest_modules` |
| **`exec`** | `mode` | `ExecutionMode` | `Parallel { workers: Auto }` | `--serial`, `-n N`, `debug` | -- |
| | `maxfail` | `usize` | `0` (no limit) | `--maxfail N`, `-x` | `maxfail` |
| | `timeout_secs` | `Option<u64>` | `None` | `--timeout SECS` | `timeout` |
| | `timeout_multiplier` | `Option<f64>` | `None` | -- | `timeout_multiplier` |
| | `spawn_overhead` | `DurationMs` | `250.0` | -- | `spawn_overhead_ms` |
| | `min_parallel_tests` | `usize` | `100` | -- | `min_parallel_tests` |
| | `retries` | `usize` | `0` | `--retries N` | `retries` |
| | `retries_delay_secs` | `u64` | `0` | -- | `retries_delay` |
| **`output`** | `tb` | `TbStyle` | `Detail` | `--tb` | `tb` |
| | `show_locals` | `bool` | `false` | `--show-locals` | `show_locals` |
| | `show_internals` | `bool` | `false` | `--show-internals` | `show_internals` |
| | `verbosity` | `Verbosity` | `Normal` | `-v`, `-vv`, `--verbose[=LEVEL]` | `verbosity` |
| | `durations` | `Option<usize>` | `None` | `--durations N` | `durations` |
| | `color` | `ColorMode` | `Auto` | `--color` | `color` |
| | `collection_profile` | `bool` | `false` | `--collection-profile` | -- |
| | `keep_tmp` | `Option<KeepTmpMode>` | `None` | `--keep-tmp[=MODE]` | `keep_tmp` |
| **`markers`** | `registered_markers` | `Vec<String>` | `[]` | -- | `markers` |
| | `markers_without_description` | `Vec<String>` | `[]` | -- | (derived) |
| | `strict` | `Option<StrictMode>` | `None` | `--strict[=MODE]` | `strict` |
| **`filter`** | `schedule` | `ScheduleStrategy` | `LongestFirst` | `--schedule` | `schedule` |
| | `failed` | `Option<FailedMode>` | `None` | `--failed MODE`, `--lf`, `--ff` | `failed` |
| | `node_ids` | `Vec<NodeId>` | `[]` | positional (path::test) | -- |
| | `has_explicit_paths` | `bool` | `false` | (derived) | -- |
| | `affected` | `Option<String>` | `None` | `--affected[=REF]` | -- |
| | `affected_base` | `String` | `"HEAD"` | -- | `affected_base` |
| **`features`** | `plugins` | `Vec<String>` | `[]` | -- | `plugins` |
| | `plugin_settings` | `HashMap<String, toml::Value>` | `{}` | -- | `plugin_settings.*` |
| | `async_backend` | `String` | `"asyncio"` | -- | `async_backend` |
| | `cov` | `bool` | `false` | `--cov` | -- |
| | `cov_report` | `Option<CovReportFormat>` | `None` | `--cov-report FORMAT` | -- |
| | `cache_max_age` | `u32` | `50` | -- | `cache_max_age` |

### Key enum types

**`WorkerCount`** -- `Auto` or `Fixed(usize)`. Accepts `"auto"` or a positive integer in both CLI and TOML. Custom serde `Visitor` in `pyproject.rs` handles both string and integer deserialization.

**`StrictMode`** -- `Abort` (violations are hard errors, exit 3), `Enforce` (violations reported as per-test errors), or `Off` (disables strict mode, overriding any pyproject.toml value).

**`ScheduleStrategy`** -- `LongestFirst` (default, uses cached timing data), `FailedFirst`, or `Random`.

**`KeepTmpMode`** -- `Failed` (preserve on test failure only) or `Always`.

**`Verbosity`** -- `Normal` (0), `Detailed` (1), `Full` (2). Implements `Ord` for comparison.

**`TbStyle`** -- `Detail` (user frames), `Line` (one-line summary), `No` (suppress).

**`ColorMode`** -- `Auto` (TTY detection), `Always`, `Never`. Resolved to bool via `ColorMode::resolve(self, is_tty: bool) -> bool`.

**`FailedMode`** -- `Only` (run just failures) or `First` (failures first, then rest).

---

## Rootdir detection

`find_rootdir` in `src/config/mod.rs` walks up from the first positional path (or `.`) looking for `pyproject.toml`, `setup.cfg`, or `tox.ini`. If none is found, it returns the starting directory. The rootdir determines where `pyproject.toml` is read from and how `testpaths` are resolved.

---

## Debug mode side effects

`merge_debug_args` in `src/config/merge.rs` applies debug-specific overrides directly before the shared `apply_overrides` call:

```rust
pub fn merge_debug_args(mut self, args: &DebugArgs) -> Self {
    // ...paths and node_ids merged first...

    let mode = args.mode();
    self.exec.mode = ExecutionMode::Debug(mode.clone());  // force debug execution mode
    self.exec.timeout_secs = None;                        // disable timeouts
    self.output.show_internals = true;                    // show oxitest frames
    if args.tb.is_none() {
        self.output.tb = TbStyle::Detail;  // only if user didn't pass --tb
    }
    if matches!(mode, DebugMode::PostMortem) {
        self.exec.maxfail = 1;  // stop on first failure
    }

    // ...apply_overrides called after, so explicit --tb from user overwrites the default...
}
```

`ExecutionMode::Debug(_)` implies serial execution: `is_serial()` returns `true` for both `Serial` and `Debug(_)` variants, and `worker_count()` returns 1 for both. An explicit `--tb` flag from the user will overwrite the debug default because it goes through `Overrides` after the direct field assignments above.

---

## Worker count resolution

Two functions collaborate:

1. **`Config::worker_count()`** -- simple resolution: serial -> 1, `Fixed(n)` -> n, `Auto`/`None` -> CPU count.

2. **`compute_optimal_workers()`** -- heuristic: given an estimated total runtime and `spawn_overhead_ms`, it caps the worker count so we do not spawn more workers than the estimated runtime warrants. Used after timing estimates are available (from cache).

```rust
pub fn compute_optimal_workers(
    mode: &ExecutionMode,
    cpu_count: usize,
    estimated: Option<std::time::Duration>,
    spawn_overhead_ms: f64,
) -> usize
```

The `spawn_overhead_ms` config field (default 250.0) represents the cost of spawning one worker subprocess. The heuristic divides the estimated total runtime by this overhead to decide how many workers are worthwhile.

---

## Inspect subcommand timeout

`oxitest inspect` starts the TUI as soon as Phase 1 (Rust prescan) finishes; Phase 2 (Python-tier fixture resolution, plugin instantiation, etc.) runs in the background and populates additional sections as data arrives. If Phase 2 does not complete within `inspect_timeout` seconds, the TUI logs the timeout and continues serving whatever data is already loaded.

- **Pyproject key:** `[tool.oxitest] inspect_timeout` (integer seconds)
- **Default:** `30`
- **Resolved field:** `Config::exec.inspect_timeout_secs` (`src/config/mod.rs`)
- **Consumer:** `src/inspect/mod.rs` wraps the Phase-2 future in `Duration::from_secs(cfg.exec.inspect_timeout_secs)`.
