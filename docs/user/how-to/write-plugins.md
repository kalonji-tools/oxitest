# Write plugins

!!! abstract "How-to"
    Extend oxitest with custom reporters, fixtures, collectors, log backends, and
    execution wrappers using the plugin API.

!!! info "Deep dive"
    See [Extending oxitest](../../../internals/book/extending.html) for the Rust-side extension points, plugin protocols, and how reporters are multiplexed.

## Overview

Plugins extend oxitest through nine protocols: **Reporter**, **LogBackend**,
**FixtureProvider**, **HelperProvider**, **Collector**, **ExecutionWrapper**, **AsyncBackend**, **DebuggerBackend**, and **CoverageProvider**. Each plugin is a
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

### Plugin vs. conftest.py

Both plugins and `conftest.py` extend oxitest, but they serve different
purposes:

| | `conftest.py` | Plugin |
|---|---|---|
| **Scope** | One project | Any project that installs it |
| **Distribution** | Not distributed | Published to PyPI or a private index |
| **Packaging** | None required | Standard Python package |
| **Best for** | Project-specific fixtures and helpers | Reusable infrastructure across many projects |

Use `conftest.py` when the extension is specific to your test suite.
Use a plugin when you want to share the behaviour across multiple projects or
distribute it to other teams.

### Decision matrix

| I want to... | Use this protocol |
|---|---|
| Provide reusable fixtures across projects | `FixtureProvider` |
| Provide reusable helper functions across projects | `HelperProvider` |
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

## Protocols

The `Plugin` dataclass has nine fields — six tuple-based protocol fields and
three singleton fields (`async_backend`, `debugger_backend`, and
`coverage_provider`). Each tuple field allows a single plugin to provide
multiple implementations of the same protocol.

```python
@dataclass(frozen=True)
class Plugin:
    log_backends: tuple[LogBackend, ...] = ()
    fixture_providers: tuple[FixtureProvider, ...] = ()
    helper_providers: tuple[HelperProvider, ...] = ()
    execution_wrappers: tuple[ExecutionWrapper, ...] = ()
    collectors: tuple[Collector, ...] = ()
    reporters: tuple[Reporter, ...] = ()
    async_backend: AsyncBackend | None = None
    debugger_backend: DebuggerBackend | None = None
    coverage_provider: CoverageProvider | None = None
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

`scope` controls fixture lifetime: `"each"` (per-test, default), `"shared"`
(per-module, FrozenProxy-wrapped), or `"session"` (per-process). `autouse`
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

### HelperProvider

Helper providers contribute named callables to the helper registry. Tests
access them via `helpers.<namespace>.<name>()`.

**Signatures:**

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:helper-provider-protocol"
```

**Example** -- provide a URL builder helper:

```python
from oxitest.plugin import Plugin


class UrlHelper:
    @property
    def name(self):
        return "build_url"

    @property
    def helper(self):
        return lambda base, path: f"{base.rstrip('/')}/{path.lstrip('/')}"


def oxitest_plugin(config=None):
    return Plugin(helper_providers=(UrlHelper(),))
```

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
`AsyncBackend` and `SharedAsyncSession` protocols.

```python
from oxitest import Plugin, AsyncBackend, SharedAsyncSession
```

```python
--8<-- "python/tests/docs/how-to/test_write_plugins.py:trio-shared-session"
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
from oxitest._bridge._coverage import CovReportFormat
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
