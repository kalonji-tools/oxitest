# API Stability

oxitest follows [semantic versioning](https://semver.org/). This page defines what is covered by the stability guarantee.

## Stable (semver-protected)

These surfaces will not change in backward-incompatible ways without a major version bump:

**Python API**

- `Fixtures`, `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`
- `@oxi.parametrize`, `@oxi.partial`
- `mark.skip`, `mark.xfail`, `mark.timeout`, custom marks
- Built-in fixtures: `TempDir`, `TempDirFactory`, `StdCapture`, `FdCapture`, `Patcher`, `LogCapture`, `WarnCapture`, `TestContext`
- `oxi.raises()`, `oxi.warns()`, `oxi.approx()`, `oxi.importorskip()`
- `Plugin` dataclass and protocol interfaces
- `ExitCode` enum values (0–4)

**CLI**

- Subcommands: `run` (default), `debug`, `query`, `env`
- All documented flags and their behavior
- Exit codes: 0 (success), 1 (test failure), 2 (interrupted), 3 (collection error), 4 (usage error)

**Configuration**

- All `[tool.oxitest]` keys documented in the reference
- Wire protocol version (`PROTOCOL_VERSION`) — the guarantee is that the
  constant is meaningful and that the Rust and Python halves stay in step, not
  that incrementing it is a breaking change. The worker wire is internal to a
  single wheel, so a bump ships in a normal minor or patch release; see
  `src/worker_result/wire.rs` for when to bump.
- Cache format version (`CACHE_VERSION`)

## Experimental

These may change in minor releases:

- Plugin protocols may gain new **optional** methods
- Query DSL syntax may expand (backward-compatible additions only)
- `--collection-profile` output format

## Internal (no guarantee)

- `oxitest._bridge.*` modules (underscore-prefixed)
- Exact output formatting (reporters may change styling)
- Exact error message wording
- Internal module structure and Rust crate layout
