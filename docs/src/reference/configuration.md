# Configuration Reference

!!! abstract "Reference"
    Complete reference for oxitest project configuration via `pyproject.toml`.

Most keys can be overridden by CLI flags. See [CLI reference](cli.md) for the flag equivalents.

## Root directory detection

Before reading configuration, oxitest determines the **rootdir** by walking up the
filesystem from the initial path (the first of `PATHS`, or the current working
directory). It stops at the first directory that contains any of:

- `pyproject.toml`
- `setup.cfg`
- `tox.ini`

The rootdir anchors relative paths and is printed at the start of each run.

## Configuration section

Place oxitest settings under `[tool.oxitest]` in `pyproject.toml`. oxitest reads
only this section — it does not fall back to `[tool.pytest]` or
`[tool.pytest.ini_options]`.

## Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `testpaths` | list of strings | `[]` | Directories (relative to rootdir) to search for tests when no `PATHS` are given on the command line. When empty, the rootdir itself is used. |
| `python_files` | list of strings | `["test_*.py", "*_test.py"]` | Glob patterns used to identify Python files that may contain tests during collection. |
| `norecursedirs` | list of strings | `[".git", "__pycache__", ".venv", "venv", ".tox", "dist", "build", "node_modules"]` | Directory name patterns that are skipped entirely during recursive collection. |
| `markers` | list of strings | `[]` | Register custom marker names. Format: `"name: description"` or `"name"`. Unregistered markers abort the run with an error. |
| `timeout` | integer | — | Per-test timeout in seconds. Tests that exceed this are killed and marked as failed. Scaled by `timeout_multiplier` when set. When omitted, no timeout is applied. |
| `cache_max_age` | integer | `50` | A test entry's age increments each run it is not executed, and resets to 0 when it runs. Entries whose age exceeds this value are evicted from `.oxitest_cache/timings.json`. |
| `min_parallel_tests` | integer | `100` | Minimum number of collected tests before parallel workers are used. Below this threshold oxitest runs serially to avoid spawn overhead. |
| `timeout_multiplier` | float | — | Multiplies all timeout values. Useful in slow CI environments (e.g. `2.0` doubles every timeout). When omitted, no multiplier is applied. |
| `spawn_overhead_ms` | float | `250.0` | Estimated cost in milliseconds to spawn a single worker process. The scheduler uses `spawn_overhead_ms × worker_count` as the total spawn budget when deciding whether parallelism is worth it. |
| `workers` | `"auto"` or integer | cpu count | Number of parallel worker processes. `"auto"` uses all available CPUs. A positive integer sets an explicit count. CLI `--workers`/`-n` overrides this value. |
| `schedule` | string | `"longest-first"` | Group scheduling strategy for parallel runs. One of: `"longest-first"` (modules in descending duration order), `"failed-first"` (failed modules first, then by duration), `"random"` (random order). |
| `failed` | string | — | Failed-test mode. `"only"` runs just previously-failed tests; `"first"` runs failures before the rest. When omitted, all tests run in normal order. |
| `keep_tmp` | `str` | `"no"` | Preserve `TempDir` contents. Values: `"no"`, `"failed"` (keep on failure), `"always"`. Also available as `--keep-tmp` CLI flag. |
| `strict` | string | — | Enforce strict conventions at run time. `"abort"` exits with code 3 before any tests run. `"enforce"` runs tests but turns violations into errors. CLI `--strict` overrides this value. |
| `affected_base` | string | — | Default git ref for `--affected`. When set, bare `--affected` compares against this ref instead of `HEAD`. CLI `--affected=REF` overrides. |
| `async_backend` | `str` | `"asyncio"` | Async runtime backend. Used by async test execution. Can be overridden by a plugin providing `AsyncBackend`. |
| `auto_arrange` | `int \| false` | `100` | Number of tests below which shared-fixture auto-arrangement is applied. Set to `false` to disable. Pyproject.toml only (not a CLI flag). |
| `auto_arrange_threshold` | `int` | `70` | If the largest auto-arranged group exceeds this percentage of total tests, fall back to serial execution. Pyproject.toml only. |
| `tb` | string | `"detail"` | Traceback style on failure. One of: `"detail"`, `"line"`, `"no"`. CLI `--tb` overrides this value. |
| `show_locals` | boolean | `false` | Show local variable values in the failing frame. CLI `--show-locals` overrides. |
| `show_internals` | boolean | `false` | Show internal oxitest framework frames in tracebacks. CLI `--show-internals` overrides. |
| `verbosity` | string | `"normal"` | Output verbosity level. One of: `"normal"`, `"detailed"`, `"full"`. CLI `-v`/`-vv`/`--verbose=LEVEL` overrides. **Breaking:** replaces the old `verbose` boolean. |
| `maxfail` | integer | `0` | Stop after N failures. `0` means unlimited. CLI `--maxfail` overrides. |
| `retries` | integer | — | Number of times to retry a failed test before recording it as failed. When omitted, no retries are performed. |
| `retries_delay` | integer | — | Delay in seconds between retry attempts. When omitted, retries run immediately. |
| `durations` | integer | — | Show the N slowest tests at end of run. CLI `--durations` overrides. |
| `serial` | boolean | `false` | Run all tests in a single process. CLI `--serial` overrides. |
| `color` | string | `"auto"` | Color output mode. One of: `"auto"`, `"always"`, `"never"`. CLI `--color` overrides. |
| `plugins` | list of strings | `[]` | Python module paths of oxitest plugins to load. Each module must export an `oxitest_plugin(config=None)` function returning `oxitest.Plugin`. |
| `plugin_settings` | table | `{}` | Per-plugin configuration. Each key is a plugin module name, value is a table of settings passed to `oxitest_plugin(config=...)`. |

## plugins

Declare plugins to extend oxitest. Each entry is a Python module path.

```toml
[tool.oxitest]
plugins = ["oxitest_loguru", "oxitest_sqlalchemy"]
```

Plugins are loaded in order at session start. Each must export:

```python
def oxitest_plugin(config: dict | None = None) -> oxitest.Plugin:
    ...
```

## plugin_settings

Per-plugin configuration passed to the plugin's entry point.

```toml
[tool.oxitest.plugin_settings.oxitest_loguru]
level = "DEBUG"
format = "{time} {message}"
```

The table name must match the plugin module name from the `plugins` list.

## schedule

Group scheduling strategy for parallel runs.

| Value | Description |
|-------|-------------|
| `longest-first` | **(default)** Longest modules first based on cached timing data |
| `failed-first` | Previously-failed modules first, then by duration |
| `random` | Random order — useful for detecting order-dependent tests |

CLI: `--schedule=longest-first`

pyproject.toml:
```toml
[tool.oxitest]
schedule = "failed-first"
```

**Relationship to `--failed`:**
- `--failed=only` filters to only failed tests (any mode)
- `--failed=first` reorders individual tests (serial mode)
- `--schedule=failed-first` is the parallel-mode equivalent — prioritizes failed *modules* for worker dispatch

## failed

Failed-test mode for re-running or prioritizing previously-failed tests.

| Value | Description |
|-------|-------------|
| `only` | Only run tests that failed on the last run |
| `first` | Run failed tests first, then the rest |

CLI: `--failed=only`

pyproject.toml:
```toml
[tool.oxitest]
failed = "only"
```

When `--failed=only` is active, oxitest prints a banner showing how many tests are being run:

```
running 3/412 tests (--failed=only mode)
```

If no failures are recorded in the cache, all tests run with a notice:

```
no recorded failures — running all 412 tests
```

## Example

```toml
[tool.oxitest]
testpaths = [
    "tests",
    "integration",
]
python_files  = ["test_*.py", "*_test.py", "check_*.py"]
norecursedirs = [
    ".git",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    "node_modules",
    "vendor",
]
markers        = ["slow: marks slow tests", "integration: hits real services"]
timeout        = 30
cache_max_age  = 100
min_parallel_tests = 50
spawn_overhead_ms  = 100.0
strict             = "abort"
tb                 = "detail"
show_locals        = true
```
