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

Config construction happens in two phases inside `src/pipeline/mod.rs`:

```rust
// Phase 1: load pyproject.toml on top of defaults
let cfg = config::Config::load(&rootdir);

// Phase 2: merge CLI args on top (CLI wins)
let cfg = match &command {
    config::Command::Run(args) => cfg.merge_run_args(args),
    config::Command::Debug(args) => cfg.merge_debug_args(args),
    config::Command::Query(args) => cfg.merge_query_args(args),
    // ...
};
```

`Config::load` (in `src/config/mod.rs`) reads `pyproject.toml`, deserializes it into a `PyprojectToml` -> `OxitestConfig`, then calls `config.merge_toml(tc, rootdir)`.

The merge methods (`merge_run_args`, `merge_debug_args`, etc.) in `src/config/merge.rs` apply CLI values on top.

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
    workers: Option<WorkerCount>,
    failed: Option<FailedMode>,
    tb: Option<TbStyle>,
    color: Option<ColorMode>,
    durations: Option<usize>,
    strict: Option<StrictMode>,
    keep_tmp: Option<KeepTmpMode>,
    show_locals: Option<bool>,
    show_internals: Option<bool>,
    auto_arrange_threshold: Option<Option<u8>>,
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

#### 2. Add to `Config` in `src/config/mod.rs`

```rust
pub struct Config {
    // ...existing fields...
    pub my_threshold: u32,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            // ...existing fields...
            my_threshold: 100,  // compiled default
        }
    }
}
```

#### 3. Add CLI flag in `src/config/cli.rs`

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
    assert_eq!(cfg.my_threshold, 100);
}

#[test]
fn test_my_threshold_from_pyproject() {
    let cfg = Config::from_str("[tool.oxitest]\nmy_threshold = 50\n").unwrap();
    assert_eq!(cfg.my_threshold, 50);
}

#[test]
fn test_my_threshold_cli_overrides_pyproject() {
    let dir = TempDir::new().unwrap();
    fs::write(dir.path().join("pyproject.toml"), "[tool.oxitest]\nmy_threshold = 50\n").unwrap();
    let cfg = Config::load(Utf8Path::from_path(dir.path()).unwrap());
    let args = parse_run(&["--my-threshold", "200"]);
    let merged = cfg.merge_run_args(&args);
    assert_eq!(merged.my_threshold, 200);
}
```

---

## Config struct fields

All fields of the `Config` struct in `src/config/mod.rs`, their types, defaults, and corresponding CLI flags:

| Field | Type | Default | CLI flag | TOML key |
|-------|------|---------|----------|----------|
| `rootdir` | `Utf8PathBuf` | `"."` | (auto-detected) | -- |
| `testpaths` | `Vec<Utf8PathBuf>` | `["."]` | positional args | `testpaths` |
| `python_files` | `Vec<String>` | `["test_*.py", "*_test.py"]` | -- | `python_files` |
| `norecursedirs` | `Vec<String>` | `.git`, `__pycache__`, `.venv`, `venv`, `.tox`, `dist`, `build`, `node_modules` | -- | `norecursedirs` |
| `maxfail` | `usize` | `0` (no limit) | `--maxfail N`, `-x` | `maxfail` |
| `registered_markers` | `Vec<String>` | `[]` | -- | `markers` |
| `timeout_secs` | `Option<u64>` | `None` | `--timeout SECS` | `timeout` |
| `serial` | `bool` | `false` | `--serial` | `serial` |
| `debug` | `Option<DebugMode>` | `None` | `debug` subcommand | -- |
| `workers` | `Option<WorkerCount>` | `None` | `-n N`, `--workers N` | `workers` |
| `cache_max_age` | `u32` | `50` | -- | `cache_max_age` |
| `min_parallel_tests` | `usize` | `100` | -- | `min_parallel_tests` |
| `timeout_multiplier` | `Option<f64>` | `None` | -- | `timeout_multiplier` |
| `spawn_overhead_ms` | `f64` | `250.0` | -- | `spawn_overhead_ms` |
| `strict` | `Option<StrictMode>` | `None` | `--strict[=MODE]` | `strict` |
| `markers_without_description` | `Vec<String>` | `[]` | -- | (derived from `markers`) |
| `schedule` | `ScheduleStrategy` | `LongestFirst` | `--schedule` | `schedule` |
| `failed` | `Option<FailedMode>` | `None` | `--failed MODE`, `--lf`, `--ff` | `failed` |
| `tb` | `TbStyle` | `Detail` | `--tb` | `tb` |
| `show_locals` | `bool` | `false` | `--show-locals` | `show_locals` |
| `show_internals` | `bool` | `false` | `--show-internals` | `show_internals` |
| `verbosity` | `Verbosity` | `Normal` | `-v`, `-vv`, `--verbose[=LEVEL]` | `verbosity` |
| `durations` | `Option<usize>` | `None` | `--durations N` | `durations` |
| `color` | `ColorMode` | `Auto` | `--color` | `color` |
| `plugins` | `Vec<String>` | `[]` | -- | `plugins` |
| `plugin_settings` | `HashMap<String, toml::Value>` | `{}` | -- | `plugin_settings.*` |
| `async_backend` | `String` | `"asyncio"` | -- | `async_backend` |
| `affected` | `Option<String>` | `None` | `--affected[=REF]` | -- |
| `affected_base` | `String` | `"HEAD"` | -- | `affected_base` |
| `retries` | `usize` | `0` | `--retries N` | `retries` |
| `retries_delay_secs` | `u64` | `0` | -- | `retries_delay` |
| `keep_tmp` | `Option<KeepTmpMode>` | `None` | `--keep-tmp[=MODE]` | `keep_tmp` |
| `auto_arrange_threshold` | `Option<u8>` | `Some(70)` | -- | `auto_arrange` |
| `collection_profile` | `bool` | `false` | `--collection-profile` | -- |
| `use_gitignore` | `bool` | `true` | `--no-use-gitignore` | `use_gitignore` |
| `doctest_modules` | `bool` | `false` | `--doctest-modules` | `doctest_modules` |
| `node_ids` | `Vec<NodeId>` | `[]` | positional (path::test) | -- |
| `node_id_source_files` | `HashSet<Utf8PathBuf>` | `{}` | (derived) | -- |
| `cov` | `bool` | `false` | `--cov` | -- |
| `cov_report` | `Option<CovReportFormat>` | `None` | `--cov-report FORMAT` | -- |
| `has_explicit_paths` | `bool` | `false` | (derived) | -- |

### Key enum types

**`WorkerCount`** -- `Auto` or `Fixed(usize)`. Accepts `"auto"` or a positive integer in both CLI and TOML. Custom serde `Visitor` in `pyproject.rs` handles both string and integer deserialization.

**`StrictMode`** -- `Abort` (violations are hard errors, exit 3) or `Enforce` (violations reported as per-test errors).

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

`DebugMode::apply_to` modifies multiple config fields at once:

```rust
pub fn apply_to(&self, cfg: &mut Config, cli_tb: Option<&TbStyle>) {
    cfg.debug = Some(self.clone());
    cfg.serial = true;          // force serial execution
    cfg.timeout_secs = None;    // disable timeouts
    cfg.show_internals = true;  // show oxitest frames
    if cli_tb.is_none() {
        cfg.tb = TbStyle::Detail;  // only if user didn't pass --tb
    }
    if matches!(self, DebugMode::PostMortem) {
        cfg.maxfail = 1;          // stop on first failure
    }
}
```

This runs inside `merge_debug_args`, before the shared `apply_overrides` call. An explicit `--tb` flag from the user will overwrite the debug default because it goes through `Overrides` after `apply_to`.

---

## Worker count resolution

Two functions collaborate:

1. **`Config::worker_count()`** -- simple resolution: serial -> 1, `Fixed(n)` -> n, `Auto`/`None` -> CPU count.

2. **`compute_optimal_workers()`** -- heuristic: given an estimated total runtime and `spawn_overhead_ms`, it caps the worker count so we do not spawn more workers than the estimated runtime warrants. Used after timing estimates are available (from cache).

```rust
pub(crate) fn compute_optimal_workers(
    explicit_workers: Option<WorkerCount>,
    serial: bool,
    cpu_count: usize,
    estimated: Option<Duration>,
    spawn_overhead_ms: f64,
) -> usize
```

The `spawn_overhead_ms` config field (default 250.0) represents the cost of spawning one worker subprocess. The heuristic divides the estimated total runtime by this overhead to decide how many workers are worthwhile.
