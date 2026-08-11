# Write plugins

!!! abstract "How-to"
    Extend oxitest with custom reporters, fixtures, collectors, log backends, and
    execution wrappers using the plugin API.

!!! info "Deep dive"
    See [Extending oxitest](../../../internals/book/extending.html) for the Rust-side extension points, plugin protocols, and how reporters are multiplexed.

## Overview

Plugins extend oxitest through eight protocols: **Reporter**, **LogBackend**,
**FixtureProvider**, **Collector**, **ExecutionWrapper**, **AsyncBackend**, **DebuggerBackend**, and **CoverageProvider**. Each plugin is a
Python package declared in `pyproject.toml` and loaded at startup. Per-plugin
configuration is passed via `plugin_settings` as a dictionary to the plugin's
entry point function.

## Quick start

A plugin is any Python package that exports an `oxitest_plugin` function
returning a `Plugin` dataclass.

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:quick-start"
```

Declare the plugin in your project's `pyproject.toml`:

```toml
# pyproject.toml
[tool.oxitest]
plugins = ["my_plugin"]
```

oxitest imports `my_plugin`, calls `oxitest_plugin()`, and registers whatever
protocols the returned `Plugin` provides. The empty plugin above is valid but
does nothing -- it serves as a starting point.

## Configuration

Plugins receive configuration from `pyproject.toml` via the
`[tool.oxitest.plugin_settings.<name>]` table. The entire table is passed as a
dictionary to the `config` parameter of `oxitest_plugin()`.

```toml
# pyproject.toml
[tool.oxitest]
plugins = ["my_plugin"]

[tool.oxitest.plugin_settings.my_plugin]
output = "report.json"
retries = 3
```

The plugin receives the config as a plain dict:

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:config-entry"
```

If no `plugin_settings` table exists for the plugin, `config` is `None`.

### Typed config with CLI extensions

For richer configuration, plugins can declare a frozen dataclass as their config
schema. oxitest introspects it to generate CLI flags, validate types, and merge
values from multiple sources.

```python
# my_plugin/__init__.py
from dataclasses import dataclass
from typing import Annotated

from oxitest import CliExtension, Both, Cli, Conf, Plugin
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:cli-extension"
```

```python
def oxitest_plugin(*, config: MyConfig | None = None) -> Plugin:
    # config is a fully typed MyConfig instance (not a dict)
    return Plugin()
```

This generates CLI flags automatically:

```
plugin: myplugin:
  -H, --myplugin-host HOST  Target host [env: MY_HOST]
      --myplugin-verbose     Verbose output
```

#### Source markers

Every field must be annotated with exactly one source marker:

| Marker | CLI flag | pyproject.toml | env var |
|--------|----------|----------------|---------|
| `Cli`  | Yes      | No             | Optional |
| `Conf` | No       | Yes            | No      |
| `Both` | Yes      | Yes            | Optional |

Precedence: **CLI > env > pyproject.toml > default**.

#### Prefix customization

All plugin CLI flags are namespaced by prefix (`--{prefix}-{field}`). Users can
override the prefix in `pyproject.toml`:

```toml
[tool.oxitest.plugin_settings.my_plugin]
cli_prefix = "mp"
```

This changes `--myplugin-host` to `--mp-host`. Prefix uniqueness is validated
at startup -- two plugins cannot share the same prefix.

## When to use plugins

### Plugin vs. declaration file

Both plugins and declaration files extend oxitest, but they serve different
purposes:

| | `__fixtures__.py` | Plugin |
|---|---|---|
| **Scope** | One project | Any project that installs it |
| **Distribution** | Not distributed | Published to PyPI or a private index |
| **Packaging** | None required | Standard Python package |
| **Best for** | Project-specific fixtures | Reusable infrastructure across many projects |

Use a declaration file when the extension is specific to your test suite.
Use a plugin when you want to share the behaviour across multiple projects or
distribute it to other teams.

### Decision matrix

| I want to... | Use this protocol |
|---|---|
| Provide reusable fixtures across projects | `FixtureProvider` |
| Add retry, profiling, or tracing around tests | `ExecutionWrapper` |
| Send results to a dashboard or custom format | `Reporter` |
| Use an alternative coverage tool | `CoverageProvider` |
| Replace or extend the default test collector | `Collector` |
| Customize log capture (e.g. structured logging) | `LogBackend` |
| Run async tests with a custom event loop | `AsyncBackend` |
| Integrate a custom debugger | `DebuggerBackend` |

### Composability

A single `Plugin` dataclass can implement multiple protocols at once. There is
no limit on how many protocols one plugin registers — just populate the
relevant fields:

```python
from oxitest.plugin import Plugin


def oxitest_plugin(config=None):
    return Plugin(
        reporters=(MyReporter(),),
        fixture_providers=(MyFixtureProvider(),),
    )
```

This is useful when a plugin naturally owns both a fixture and the reporting
that accompanies it (for example, a coverage plugin that also provides a
`coverage_session` fixture).

## Ship fixtures from a `__fixtures__.py`

A plugin that is a **package** can declare fixtures with `@oxi.fixture`, exactly
as a user does in their own test tree. This is the recommended route for
ordinary fixtures; `FixtureProvider` remains supported and is the only option
when a fixture needs values computed inside your plugin's entry point.

```
oxi_pg/
├── __init__.py        # your oxitest_plugin() entry point
└── __fixtures__.py    # your fixtures
```

```python title="oxi_pg/__fixtures__.py"
import oxitest as oxi


class Conn:
    def __init__(self) -> None:
        self.dsn = "postgres://localhost/test"


@oxi.fixture(lifetime="module")
def conn() -> Conn:
    return Conn()
```

The user activates the plugin the usual way, and the fixtures come with it:

```toml
[tool.oxitest]
plugins = ["oxi_pg"]
```

```python
from oxitest import Fixtures


def test_query(fx: Fixtures) -> None:
    assert fx.oxi_pg.conn.dsn.startswith("postgres://")
```

Plugin fixtures are **ambient**: they are reachable from every test in the run,
at any directory depth, and are not subject to the
[B1 boundary](use-fixtures.md#understand-fixture-visibility-the-b1-boundary)
that anchors a user's declarations to their own subtree.

Only the package's top-level `__fixtures__.py` is scanned. `__init__.py` is not
a declaration home for a plugin — that is where your entry point lives.

### Namespace

The namespace defaults to your **module name**, so `fx.oxi_pg.conn` works with
no configuration. A user can shorten it:

```toml
[tool.oxitest.plugin_settings.oxi_pg]
namespace = "pg"        # now fx.pg.conn
```

The shortcut `fx.conn` also works, as it does for any fixture.

Three namespaces are refused at activation, each because the fixtures would
otherwise be unreachable or ambiguous: `oxi` (reserved for oxitest's built-ins),
any Python keyword or builtin, and a namespace already claimed by another
activated plugin.

If a user declares a fixture of the same name in their own tree, **theirs
wins** — the same way a nearer declaration outranks a more distant one. The run
stays green and a notice names both.

### Lifetimes

`function`, `module` and `process` all work. **`package` is refused**: it binds
a fixture to a directory in the user's test tree, and your plugin has none. Use
`process` for one instance per worker, or `module` for one per test module.

### Autouse

You may declare `autouse=True`, but it does not fire until the **user** enables
it in their own configuration:

```toml
[tool.oxitest.plugin_settings.oxi_pg]
autouse = ["tx"]
```

Until then the fixture registers normally — requestable, but not automatic —
and oxitest emits a notice naming the fixture and the key that turns it on.
Installing a plugin is not consent to add setup to every test in a suite, so
the decision belongs to the person whose suite it is.

## Protocols

The `Plugin` dataclass has eight fields — five tuple-based protocol fields and
three singleton fields (`async_backend`, `debugger_backend`, and
`coverage_provider`). Each tuple field allows a single plugin to provide
multiple implementations of the same protocol. The three singleton fields
default to null-object instances — omit the field entirely if your plugin
does not provide the backend; do not pass `None` explicitly.

```python
@dataclass(frozen=True)
class Plugin:
    log_backends: tuple[LogBackend, ...] = ()
    fixture_providers: tuple[FixtureProvider, ...] = ()
    execution_wrappers: tuple[ExecutionWrapper, ...] = ()
    collectors: tuple[Collector, ...] = ()
    reporters: tuple[Reporter, ...] = ()
    async_backend: AsyncBackend = _NULL_ASYNC_BACKEND
    debugger_backend: DebuggerBackend = _NULL_DEBUGGER
    coverage_provider: CoverageProvider = _NULL_COVERAGE
```

### Reporter

Reporters receive events during the test run: when a test starts, when it
completes, and when the entire run finishes.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:reporter-protocol"
```

**Example** -- write test events to a JSON file:

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:json-reporter"
```

### LogBackend

Log backends control how log records are captured during test execution.
`install()` is called before each test, `uninstall()` after. The `records`
property returns captured log entries.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:logbackend-protocol"
```

**Example** -- custom log handler that captures records with timestamps:

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:timestamp-backend"
```

### FixtureProvider

Fixture providers inject custom fixtures into tests. The `name` property is a
diagnostic name for error messages. The `fixture_type` property is the binding
type — tests request the fixture via `Fixture[T]` where `T` matches
`fixture_type`. `create()` builds the fixture value and `teardown()` cleans it up.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:fixture-provider-protocol"
```

`scope` controls fixture lifetime: `"each"` (per test, the default) or
`"session"` (once per **task group** — a single module, unless a
`lifetime="package"` declaration merges a subtree). Neither value is once per
run, and neither is once per worker process. `autouse`
makes the fixture run for every test without explicit `Fixture[T]` annotation.
Both are optional — existing plugins without these properties work unchanged.

**Example** -- database connection pool:

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:connection-pool"
```

Tests inject the fixture using the provider's `fixture_type` (the parameter name
is just for readability — resolution is type-based):

```python
from my_plugin import ConnectionPool


def test_database(pool: ConnectionPool):
    conn = pool.acquire()
    assert conn is not None
```

!!! tip
    `@injectable` makes `Fixture[ConnectionPool]` wrapping optional. Both
    `pool: ConnectionPool` and `pool: Fixture[ConnectionPool]` work — the
    decorator simply lets users skip the `Fixture[T]` boilerplate when the
    type is unambiguously a fixture.

### Collector

Collectors discover additional test items from modules. They run alongside
oxitest's built-in collector (which finds `test_*` functions). Return a list
of `CollectedItem` objects.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:collector-protocol"
```

**Example** -- discover `check_*` functions as tests:

```python
import inspect
from oxitest import CollectedItem
from oxitest.plugin import Plugin
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:check-collector"
```

```python
def oxitest_plugin(config=None):
    return Plugin(collectors=(CheckCollector(),))
```

### ExecutionWrapper

Execution wrappers intercept test execution for tests decorated with a specific
marker. The `marker` property names the marker that triggers the wrapper.
`wrap()` receives the test function and the marker's keyword arguments, and must
return a test result.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:execution-wrapper-protocol"
```

**Example** -- retry on failure:

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:retry-wrapper"
```

Register the marker in `pyproject.toml` and use it in tests:

```toml
[tool.oxitest]
markers = ["retry: retry a test multiple times"]
```

```python
import oxitest


@oxitest.mark.retry(count=3)
def test_flaky_service():
    response = call_external_api()
    assert response.status == 200
```

#### Synthesizing result outcomes

An `ExecutionWrapper.wrap()` implementation returns a `TestResult`. When
your wrapper decides on an outcome without running the test — or wants to
mark a run with a non-default status — use one of the factory functions on
`oxitest.plugin`:

- `skipped(message="...")` — the test cannot be run in this context.
- `xfailed(message="...")` — the test's failure was expected (known-broken).
- `warned(message="...")` — the wrapper flags a warning-level outcome
  (e.g. degraded or partial execution).

Example — a wrapper that skips tests when a platform prerequisite is missing.
`is_nixos()` is illustrative; substitute your own platform check:

```python
from oxitest.plugin import ExecutionWrapper, TestResult, skipped


class NixosOnlyWrapper:
    @property
    def marker(self) -> str:
        return "nixos-only"

    def wrap(self, *, test_fn, marker_args) -> TestResult:
        if not is_nixos():
            return skipped(message="requires NixOS")
        return test_fn()
```

**Prefer factories over direct class construction.** `skipped(message="...")`
is the canonical way to produce a Skipped outcome. The `SkippedResult` class
is re-exported on `oxitest.plugin` for type-annotation and `isinstance`
purposes, but direct construction (`SkippedResult(message="...")`) bypasses
the factory contract — future oxitest versions may add validation, telemetry,
or default derivation at the factory boundary that class construction
wouldn't get.

> **Not yet available: `errored()` factory.**
> The `ErrorResult` variant carries traceback fields (`file`, `lineno`,
> `frames`) that are captured from a real exception's traceback — no wrapper
> case has yet demanded author-synthesis of these. If your plugin has one,
> please [file an issue](https://github.com/kalonji-tools/oxitest/issues/new).

## Complete example

This section shows a full Reporter plugin that writes test events to a JSON
file. The pattern is taken from the oxitest test suite and is known to work
end-to-end.

### Plugin code

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:file-reporter"
```

### Project configuration

```toml
# pyproject.toml
[tool.oxitest]
testpaths = ["tests"]
plugins = ["reporter_plugin"]

[tool.oxitest.plugin_settings.reporter_plugin]
output = "build/test-events.json"
```

### Test file

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:test-math-example"
```

### Output

After running `oxitest`, the file `build/test-events.json` contains:

```json
[
  {"event": "started", "item": "test_math.py::test_addition"},
  {"event": "completed", "item": "test_math.py::test_addition", "outcome": "passed", "duration_ms": 0.42},
  {"event": "started", "item": "test_math.py::test_multiplication"},
  {"event": "completed", "item": "test_math.py::test_multiplication", "outcome": "passed", "duration_ms": 0.31},
  {"event": "finish", "errors": 0, "interrupted": false}
]
```

## Troubleshooting

Common plugin errors -- missing entry points, import failures, protocol
mismatches -- are documented in the [error reference](../reference/errors.md).

## Async backend

Plugins can provide an alternative async runtime backend by implementing the
`AsyncBackend` and `AsyncSession` protocols.

The seam is scoped around an `AsyncSession` context manager. The framework
calls `backend.acquire_session()` to obtain a session, drives work through
`session.run(coro)`, and lets the session's `__exit__` finalize the runtime
(shutting down async generators, closing loops or nurseries). Session
lifetime is a caller decision — short-lived usage inlines a `with` block;
long-lived usage (e.g., the shared async fixture manager) holds the session
via a `contextlib.ExitStack`.

```python
from contextlib import contextmanager

from oxitest import Plugin, AsyncBackend, AsyncSession
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:trio-session"
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:trio-backend"
```

```python
def oxitest_plugin(config=None) -> Plugin:
    return Plugin(async_backend=TrioBackend())
```

Users select the backend explicitly in `pyproject.toml`:

```toml
[tool.oxitest]
plugins = ["oxitest_trio"]
async_backend = "trio"
```

**Rules:**

- Only one backend can be active. If multiple plugins provide a backend with the
  same `name`, oxitest raises `ConflictingBackendError`.
- The `async_backend` config value must match a backend `name`. If no match is
  found, oxitest raises `BackendNotFoundError`.
- The built-in `"asyncio"` backend is always available. Plugins must not use the
  name `"asyncio"`.

### `supports_nested_acquire`

`AsyncBackend` carries a `supports_nested_acquire: bool = False` class
attribute. The framework's internal call sites acquire sessions through a
guard that rejects nesting unless the backend opts in. Every runtime the
framework knows about treats nested acquire as an antipattern:

- **trio** forbids nested `trio.run` by design — a single nursery/runtime
  per call is the whole model.
- **asyncio** raises on the common nested case — `asyncio.run` while another
  loop is already running fails at runtime.
- Cross-loop bugs (a resource bound to loop A used from loop B) surface as
  runtime errors far from the acquire site.

Backend authors know their runtime best. If your backend genuinely tolerates
nested acquires, set `supports_nested_acquire = True` on the class. The
default (`False`) is safe.

## Debugger backend

Plugins can provide an alternative debugger backend by implementing the
`DebuggerBackend` protocol.

```python
from oxitest.plugin import Plugin
from oxitest import DebuggerBackend
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:ipdb-backend"
```

```python
def oxitest_plugin(config=None) -> Plugin:
    return Plugin(debugger_backend=IpdbBackend())
```

oxitest owns capture management and banners. The backend only provides the
debugger interaction — `trace()` is called before test execution in
`oxitest debug --always` mode, and `post_mortem()` is called on test failure in
any `oxitest debug` mode.

**Rules:**

- Only one debugger backend can be active. If multiple plugins provide a
  backend, oxitest raises `ConflictingDebuggerError` at startup.
- The default backend wraps stdlib `pdb`. It is used when no plugin provides
  a backend.

## Coverage provider

### CoverageProvider

Override the built-in coverage.py integration with an alternative coverage
tool.

```python
from oxitest.plugin import Plugin, CoverageProvider
from oxitest import CovReportFormat
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:slipcover-provider"
```

```python
def oxitest_plugin(config=None):
    return Plugin(coverage_provider=SlipCoverProvider())
```

At most one plugin may provide a `CoverageProvider`. If two plugins both
declare one, oxitest raises `ConflictingCoverageError` at startup.

## See also

- [Use async tests](use-async-tests.md) — how async backends are used from the test author's perspective
- [Configuration reference](../reference/configuration.md) — `plugins` and `plugin_settings` keys
- [Error reference](../reference/errors.md#plugin-errors) — plugin error messages
