# Write plugins

!!! abstract "How-to"
    Extend oxitest with custom reporters, fixtures, collectors, log backends, and
    execution wrappers using the plugin API.

## Overview

Plugins extend oxitest through six protocols: **Reporter**, **LogBackend**,
**FixtureProvider**, **Collector**, **ExecutionWrapper**, and **DebuggerBackend**. Each plugin is a
Python package declared in `pyproject.toml` and loaded at startup. Per-plugin
configuration is passed via `plugin_settings` as a dictionary to the plugin's
entry point function.

## Quick start

A plugin is any Python package that exports an `oxitest_plugin` function
returning a `Plugin` dataclass.

```python
# my_plugin/__init__.py
from oxitest.plugin import Plugin


def oxitest_plugin(config=None):
    return Plugin()
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
def oxitest_plugin(config=None):
    # config == {"output": "report.json", "retries": 3}
    return Plugin()
```

If no `plugin_settings` table exists for the plugin, `config` is `None`.

## Protocols

The `Plugin` dataclass has five fields, one per protocol. Each field is a list,
so a single plugin can provide multiple implementations of the same protocol.

```python
@dataclass
class Plugin:
    log_backends: list[LogBackend] = field(default_factory=list)
    fixture_providers: list[FixtureProvider] = field(default_factory=list)
    execution_wrappers: list[ExecutionWrapper] = field(default_factory=list)
    collectors: list[Collector] = field(default_factory=list)
    reporters: list[Reporter] = field(default_factory=list)
    async_backend: AsyncBackend | None = None
    debugger_backend: DebuggerBackend | None = None
```

### Reporter

Reporters receive events during the test run: when a test starts, when it
completes, and when the entire run finishes.

**Signatures:**

```python
class Reporter(Protocol):
    def test_started(self, item: Any) -> None: ...
    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None: ...
    def finish(self, collect_errors: list[Any], interrupted: bool) -> None: ...
```

**Example** -- write test events to a JSON file:

```python
import json
from pathlib import Path
from oxitest.plugin import Plugin


class JsonReporter:
    def __init__(self, output_path):
        self._path = Path(output_path)
        self._events = []

    def test_started(self, item):
        self._events.append({"event": "started", "item": str(item)})

    def test_completed(self, item, outcome, duration_ms):
        self._events.append({
            "event": "completed",
            "item": str(item),
            "outcome": str(outcome),
            "duration_ms": duration_ms,
        })

    def finish(self, collect_errors, interrupted):
        self._events.append({"event": "finish", "interrupted": interrupted})
        self._path.write_text(json.dumps(self._events, indent=2))


def oxitest_plugin(config=None):
    return Plugin(reporters=[JsonReporter(config["output"])])
```

### LogBackend

Log backends control how log records are captured during test execution.
`install()` is called before each test, `uninstall()` after. The `records`
property returns captured log entries.

**Signatures:**

```python
class LogBackend(Protocol):
    def install(self) -> None: ...
    def uninstall(self) -> None: ...

    @property
    def records(self) -> list[Any]: ...
```

**Example** -- custom log handler that captures records with timestamps:

```python
import logging
from oxitest.plugin import Plugin


class TimestampBackend:
    def __init__(self):
        self._handler = None
        self._records = []

    def install(self):
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self._records.append({
            "time": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
        })
        logging.root.addHandler(self._handler)

    def uninstall(self):
        if self._handler:
            logging.root.removeHandler(self._handler)

    @property
    def records(self):
        return self._records


def oxitest_plugin(config=None):
    return Plugin(log_backends=[TimestampBackend()])
```

### FixtureProvider

Fixture providers inject custom fixtures into tests. The `name` property is the
parameter name used in test functions. The `fixture_type` property is the type
that `Fixture[T]` must match. `create()` builds the fixture value and
`teardown()` cleans it up.

**Signatures:**

```python
class FixtureProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def fixture_type(self) -> type: ...

    def create(self, ctx: Any) -> object: ...
    def teardown(self, value: object) -> None: ...
```

**Example** -- database connection pool:

```python
from oxitest.plugin import Plugin


class ConnectionPool:
    """The fixture type that tests receive."""
    def __init__(self, dsn):
        self._dsn = dsn
        self._connections = []

    def acquire(self):
        conn = f"connection-to-{self._dsn}"
        self._connections.append(conn)
        return conn

    def release_all(self):
        self._connections.clear()


class PoolProvider:
    def __init__(self, dsn):
        self._dsn = dsn

    @property
    def name(self):
        return "pool"

    @property
    def fixture_type(self):
        return ConnectionPool

    def create(self, ctx):
        return ConnectionPool(self._dsn)

    def teardown(self, value):
        value.release_all()


def oxitest_plugin(config=None):
    dsn = config["dsn"] if config else "localhost:5432/test"
    return Plugin(fixture_providers=[PoolProvider(dsn)])
```

Tests inject the fixture using the provider's `name` and `fixture_type`:

```python
from oxitest import Fixture
from my_plugin import ConnectionPool


def test_database(pool: Fixture[ConnectionPool]):
    conn = pool.acquire()
    assert conn is not None
```

### Collector

Collectors discover additional test items from modules. They run alongside
oxitest's built-in collector (which finds `test_*` functions). Return a list
of `CollectedItem` objects.

**Signatures:**

```python
class Collector(Protocol):
    def collect(self, path: str, module: object) -> list[Any]: ...
```

**Example** -- discover `check_*` functions as tests:

```python
import inspect
from oxitest.plugin import Plugin
from oxitest._bridge.result import CollectedItem


class CheckCollector:
    def collect(self, path, module):
        items = []
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("check_"):
                lineno = inspect.getsourcelines(obj)[1]
                items.append(CollectedItem(
                    fn_name=name,
                    lineno=lineno,
                    markers=[],
                    param_id=None,
                    param_values=[],
                ))
        return items


def oxitest_plugin(config=None):
    return Plugin(collectors=[CheckCollector()])
```

### ExecutionWrapper

Execution wrappers intercept test execution for tests decorated with a specific
marker. The `marker` property names the marker that triggers the wrapper.
`wrap()` receives the test function and the marker's keyword arguments, and must
return a test result.

**Signatures:**

```python
class ExecutionWrapper(Protocol):
    @property
    def marker(self) -> str: ...

    def wrap(self, test_fn: Any, marker_args: dict[str, Any]) -> Any: ...
```

**Example** -- retry on failure:

```python
from oxitest.plugin import Plugin


class RetryWrapper:
    @property
    def marker(self):
        return "retry"

    def wrap(self, test_fn, marker_args):
        count = marker_args.get("count", 1)
        last_result = None
        for _ in range(count):
            last_result = test_fn()
            if last_result.status == "passed":
                return last_result
        return last_result


def oxitest_plugin(config=None):
    return Plugin(execution_wrappers=[RetryWrapper()])
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

## Complete example

This section shows a full Reporter plugin that writes test events to a JSON
file. The pattern is taken from the oxitest test suite and is known to work
end-to-end.

### Plugin code

```python
# reporter_plugin/__init__.py
"""oxitest plugin: write test events to a JSON file."""

import json
from pathlib import Path

from oxitest.plugin import Plugin


class FileReporter:
    """Collects test events and writes them to a JSON file on finish."""

    def __init__(self, output_path: str):
        self._path = Path(output_path)
        self._events: list[dict] = []

    def test_started(self, item):
        self._events.append({"event": "started", "item": str(item)})

    def test_completed(self, item, outcome, duration_ms):
        self._events.append({
            "event": "completed",
            "item": str(item),
            "outcome": str(outcome),
            "duration_ms": duration_ms,
        })

    def finish(self, collect_errors, interrupted):
        self._events.append({
            "event": "finish",
            "errors": len(collect_errors),
            "interrupted": interrupted,
        })
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._events, indent=2))


def oxitest_plugin(config=None):
    """Entry point called by oxitest at startup."""
    output = config["output"] if config else "test-events.json"
    return Plugin(reporters=[FileReporter(output)])
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
# tests/test_math.py
def test_addition():
    assert 1 + 1 == 2


def test_multiplication():
    assert 3 * 4 == 12
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


class TrioSharedSession:
    """Long-lived trio session for shared fixture resolution."""

    def run(self, coro):
        import trio
        return trio.from_thread.run(coro)

    def close(self):
        pass  # trio manages its own cleanup


class TrioBackend:
    """Trio async backend for oxitest."""

    @property
    def name(self) -> str:
        return "trio"

    def run(self, coro):
        import trio
        return trio.run(coro)

    def create_shared_session(self) -> SharedAsyncSession:
        return TrioSharedSession()


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


class IpdbBackend:
    """ipdb debugger backend for oxitest."""

    def trace(self) -> None:
        import ipdb
        ipdb.set_trace()

    def post_mortem(self, tb) -> None:
        import ipdb
        ipdb.post_mortem(tb)


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

## See also

- [Use async tests](use-async-tests.md) — how async backends are used from the test author's perspective
- [Configuration reference](../reference/configuration.md) — `plugins` and `plugin_settings` keys
- [Error reference](../reference/errors.md#plugin-errors) — plugin error messages
