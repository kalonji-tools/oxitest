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
- `oxi.current_test()` and `TestContextUnavailableError`
- `Plugin` dataclass and protocol interfaces
- `ExitCode` enum values (0–4)

**CLI**

- Subcommands: `run` (default), `debug`, `query`, `env`
- All documented flags and their behavior
- Exit codes: 0 (success), 1 (test failure), 2 (interrupted), 3 (collection error), 4 (usage error)

**Configuration**

- All `[tool.oxitest]` keys documented in the reference

## Legacy — prefer the replacement

Still covered by the stability guarantee. They keep working; they are no
longer the documented way. No retirement version is named: this project does
not schedule retirements while it has no users
([ADR-0015](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0015-releases-are-cut-when-earned.md)).

| Surface | Prefer instead |
|---|---|
| `ctx: TestContext` parameter injection **on a test** | `oxi.current_test()` |

`oxi.current_test()` — and `TestContext.current()`, the classmethod it aliases
— are preferred because they are also reachable from a plain function the test
calls, code that no injection mechanism can see (#1949). On a fixture the
parameter is not legacy; see
[Built-in fixtures](python-api/builtins.md).

## Experimental

These may change in minor releases:

- Plugin protocols may gain new **optional** methods
- Query DSL syntax may expand (backward-compatible additions only)
- `--collection-profile` output format

**The fixture declaration API**

- `@oxi.fixture` and its required `lifetime` keyword
- The four `lifetime` values — `"function"`, `"module"`, `"package"`, `"process"`
- The declaration file `__fixtures__.py`, and fixture declarations in
  `__init__.py` or inline in a test module

Newer than any released version, and still being amended. `Fixtures` keeps its
Stable guarantee until it is retired.

## Internal (no guarantee)

- `oxitest._bridge.*` modules (underscore-prefixed)
- Exact output formatting (reporters may change styling)
- Exact error message wording
- Internal module structure and Rust crate layout
