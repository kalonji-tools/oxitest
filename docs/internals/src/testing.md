# Testing Strategy

oxitest has three tiers of tests: Rust unit tests (fast, isolated), Python
integration tests (end-to-end through the bridge), and the external
`oxitest-consumer` repo (full installed-wheel validation).  This chapter
explains when to reach for each tier, plus how snapshot testing and
cross-language sync tests fit in.

## Rust unit tests

Rust tests live inline in `#[cfg(test)]` modules at the bottom of each source
file.  56 source files contain test modules.  Use a Rust unit test when:

- **the logic is pure Rust** -- no Python interpreter needed;
- **the function is deterministic** -- given the same inputs it always produces
  the same output;
- **the surface area is small** -- you are testing a single function or struct
  method.

### Good candidates

| Area | Example file | What is tested |
|------|-------------|----------------|
| Filtering / grouping | `src/filter.rs` | `group_by_module`, `filter_last_failed`, keyword matching |
| Cache operations | `src/cache/timing.rs` | `invalidate`, `sort_groups`, `merge_timings` |
| Scheduler | `src/scheduler.rs` | round-robin dispatch, empty-queue edge cases |
| Config deserialization | `src/config/mod.rs`, `src/config/merge.rs` | TOML parsing, CLI-over-file merge |
| Reporter formatting | `src/reporter/format/diff.rs` | colored diffs, summary lines |
| Query DSL compiler | `src/query/compile.rs` | expression parsing, token lexing |
| Query DSL | `src/query/compile.rs`, `src/query/eval.rs` | expression compilation, evaluation |
| Strict-mode checks | `src/strict.rs` | bare-assert detection, missing mark reason |

### Anatomy of a Rust test

```rust
// src/filter.rs

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::TestItem;
    use camino::Utf8PathBuf;

    #[test]
    fn test_group_by_module_single_module() {
        // Arrange
        let items = vec![
            TestItem::builder("tests/test_mod.py", "test_a").arc(),
            TestItem::builder("tests/test_mod.py", "test_b").arc(),
        ];
        // Act
        let groups = group_by_module(&items);
        // Assert
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].1.len(), 2);
    }
}
```

Key patterns:

- **`TestItem::builder(...).arc()`** -- a builder on `TestItem` that returns an
  `Arc<TestItem>`, the type used throughout the pipeline.
- **Test helpers live in `#[cfg(test)]` helper modules** -- for example
  `src/cache/test_helpers.rs` provides `cache_with_entries()` and
  `make_timing()` so every cache test starts from a known state without
  touching the filesystem.
- **No `#[tokio::test]`** -- oxitest's Rust layer is synchronous.  Async lives
  entirely on the Python side.

### Running Rust tests

```bash
just test-rust            # all Rust unit tests
just test-rust <name>     # a single test by name
```

## Python integration tests

Integration tests live in `python/tests/`.  There are roughly 50 test files at
the top level (unit-style, testing individual bridge modules) and another 40
inside `python/tests/integration/` (end-to-end, invoking the full runner as a
subprocess).

### When to write a Python test

- End-to-end behavior through the CLI or Python API.
- Fixture injection, parametrize, marks -- anything that crosses the
  Rust/Python bridge.
- Reporter output format and exit codes.
- Plugin loading and protocol dispatch.

### Conventions

These rules are enforced by code review and documented in `CLAUDE.md`:

1. **No class-based tests.** Use standalone `def test_*()` functions.
2. **Arrange / Act / Assert.** Three clear phases; do not interleave setup and
   assertions.
3. **Dogfood oxitest features.** Prefer `oxi.raises()` over `try/except`,
   `TempDir` over `tempfile`, `@oxi.parametrize` over copy-pasted tests, etc.
4. **Import helpers from oxitest.** Shared utilities live in
   `python/tests/conftest.py` (namespace `helpers.common`) and
   `python/tests/integration/conftest.py` (namespace `helpers.integ`).  Access
   them via `from oxitest import helpers`.

### Integration test anatomy

```python
# python/tests/integration/test_basic.py

from oxitest import TempDir, helpers


def test_all_pass_exits_zero(tmp: TempDir):
    # Arrange -- write a tiny project into a temp directory
    (tmp / "test_ok.py").write_text(
        "def test_a(): assert 1 == 1\ndef test_b(): assert True\n"
    )
    # Act -- run oxitest as a subprocess
    out, _, rc = helpers.common.run_oxitest(tmp)
    # Assert
    helpers.integ.assert_passed(out, rc)
```

`helpers.common.run_oxitest(tmp)` invokes the built `oxitest` binary in a
subprocess, captures stdout/stderr, and returns the tuple
`(stdout, stderr, returncode)`.  The `helpers.integ.assert_passed` /
`assert_failed` / `assert_contains` helpers standardize exit-code and output
checks across all integration tests.

### Helper namespaces

Each conftest defines a `Helpers()` instance whose variable name becomes
the namespace.  Helpers are registered via the `@helpers.helper` decorator
and accessed via `from oxitest import helpers`:

| Conftest | Namespace | Key helpers |
|----------|-----------|-------------|
| `python/tests/conftest.py` | `helpers.common` | `run_oxitest`, `write_test_file`, `make_session`, `make_meta` |
| `python/tests/integration/conftest.py` | `helpers.integ` | `write_project`, `assert_passed`, `assert_failed`, `assert_contains` |

### Running Python tests

```bash
just test-python                          # run all Python tests (no rebuild)
just test-python python/tests/test_fixtures.py  # single file
```

## oxitest-consumer

The `oxitest-consumer` repository is a separate project that depends on
`oxitest` as an installed package.  It exercises the full installed pipeline:

- Verifying the built wheel works end-to-end.
- Testing CLI behavior with real subprocess invocations against a real
  `pyproject.toml`.
- Regression testing that spans the Rust/Python boundary in a realistic
  environment (not a test harness).

This tier catches packaging and distribution issues that the in-repo tests
cannot.

## Snapshot testing with insta

oxitest uses the [insta](https://insta.rs) crate for Rust snapshot tests.
There are 34 snapshot files across three directories and 24 snapshot assertions
across 9 source files.

### Where snapshots live

Snapshots are stored in `snapshots/` directories next to the source file that
creates them:

```
src/reporter/format/snapshots/    # diff, summary, diagnostic, suggestions
src/reporter/snapshots/           # JUnit XML, CTRF JSON
src/query/snapshots/              # columnar, tab, JSONL, detail, highlight
src/inspect/snapshots/            # TUI layout, footer, help overlay
```

Each `.snap` file is named after the full test path:

```
_oxitest__reporter__format__diff__snapshot_tests__multi_line_diff.snap
```

### Workflow

```bash
# Run tests; new/changed snapshots are written as .snap.new files
cargo insta test

# Interactively review pending snapshots (accept or reject each)
cargo insta review
```

### CI enforcement

CI runs:

```bash
cargo insta test --unreferenced=reject
```

`--unreferenced=reject` fails the build if any `.snap` file on disk is no
longer referenced by an `insta::assert_snapshot!` call.  This catches stale
snapshots left behind after refactors.

### Example

```rust
// src/reporter/format/diff.rs

#[cfg(test)]
mod snapshot_tests {
    use super::*;

    #[test]
    fn multi_line_diff() {
        let result = fmt_diff(
            "line1\nline2\nline3",
            "line1\nchanged\nline3",
            "==",
            false,
        );
        insta::assert_snapshot!(result);
    }
}
```

The corresponding snapshot file contains:

```
---
source: src/reporter/format/diff.rs
expression: result
---
  line1
- line2
+ changed
  line3
```

### Dev-dependencies

The snapshot testing stack is declared in `Cargo.toml`:

```toml
[dev-dependencies]
tempfile = "3"
assert_fs = "1"
insta = { version = "1", features = ["redactions"] }
```

The `redactions` feature allows replacing volatile values (timestamps,
absolute paths) with placeholders so snapshots stay stable across machines.

## Cross-language sync tests

Because oxitest splits built-in marker definitions between Rust
(`BUILTIN_MARKERS` in `src/filter.rs`) and Python (`_BUILTIN_HANDLER_NAMES` in
`python/oxitest/_bridge/_mark_registry.py`), a dedicated integration test
verifies the two sides stay in sync.

The test lives at `python/tests/integration/test_marker_sync.py`:

```python
from oxitest._bridge._mark_registry import _BUILTIN_HANDLER_NAMES
from oxitest._oxitest import builtin_markers


def test_python_markers_are_subset_of_rust() -> None:
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    missing_in_rust = python_markers - rust_markers
    assert not missing_in_rust, (
        f"Python defines handlers not in Rust BUILTIN_MARKERS: {missing_in_rust}"
    )


def test_no_unexpected_rust_only_markers() -> None:
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    rust_only = rust_markers - python_markers
    assert rust_only == {"inprocess"}, (
        f"Unexpected Rust-only markers: {rust_only - {'inprocess'}}"
    )
```

There is also a Rust-side unit test in `src/filter.rs` that asserts the
expected set of built-in names.  Together, these tests guarantee that adding a
new built-in marker on either side without updating the other will fail CI.

This pattern -- a cross-language sync test -- should be used whenever a
constant, enum, or protocol must be identical on both sides of the bridge.
