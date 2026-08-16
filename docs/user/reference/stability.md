# API Stability

oxitest follows [semantic versioning](https://semver.org/). This page defines what is covered by the stability guarantee.

## Supported platforms

A platform is supported when CI runs the full test suite on it. Wheel targets and PyPI classifiers are derived from this list, never the reverse — see [ADR-0013](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0013-platform-support-is-what-ci-tests.md) for the decision and its rationale.

Every release is also gated on the artifact itself. Before any file reaches PyPI, each wheel in the table below is installed on a runner matching its tag, imported from outside the source tree, and used to run the command once; the source distribution is built from source and gets the same treatment. A failure stops the upload — see [ADR-0019](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0019-a-test-belongs-to-the-band-of-what-it-starts.md).

| Platform | Wheel |
|---|---|
| Linux x86_64 | `manylinux` x86_64 |
| Linux aarch64 | `manylinux` aarch64 |
| macOS arm64 | `universal2` |
| macOS x86_64 | `universal2` |
| Windows x86_64 | `win_amd64` |

Removing a platform from this list is a backward-incompatible change and needs a major version bump. Adding one does not.

Wheels are built for Python 3.11 through 3.14 on every platform in the table. Python version and platform are separate axes and this page's definition of support ranges over the platform: the platform jobs run 3.12, and all four versions run on Linux x86_64.

## Stable (semver-protected)

These surfaces will not change in backward-incompatible ways without a major version bump:

**Python API**

- `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`
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

Newer than any released version, and still being amended. `Fixtures` was
retired as a registry in #1720; the name survives as the `fx:` injection
annotation, and calling it raises.

## Internal (no guarantee)

- `oxitest._bridge.*` modules (underscore-prefixed)
- Exact output formatting (reporters may change styling)
- Exact error message wording
- Internal module structure and Rust crate layout
