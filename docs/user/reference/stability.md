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
- Exit codes: 0 (success), 1 (test failure), 2 (usage error), 3 (collection error), 4 (internal error)

**Configuration**

- All `[tool.oxitest]` keys documented in the reference
- Wire protocol version (`PROTOCOL_VERSION`)
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
