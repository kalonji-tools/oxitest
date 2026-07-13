"""Tested examples for the write-plugins how-to guide.

Plugin classes are tested by instantiating and calling protocol methods
directly — no plugin loader needed.
"""

import inspect
import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Annotated, Any, Protocol

from oxitest import Both, Cli, CliExtension, CollectedItem, Conf, injectable
from oxitest.plugin import Plugin


# fmt: off
# --8<-- [start:quick-start]
def oxitest_plugin(config=None):
    return Plugin()
# --8<-- [end:quick-start]

# --8<-- [start:config-entry]
def oxitest_plugin_with_config(config=None):
    # config == {"output": "report.json", "retries": 3}
    return Plugin()
# --8<-- [end:config-entry]
# fmt: on


def test_quick_start_returns_plugin():
    result = oxitest_plugin()
    assert isinstance(result, Plugin), "entry point should return a Plugin"


def test_config_entry_returns_plugin():
    result = oxitest_plugin_with_config({"output": "report.json", "retries": 3})
    assert isinstance(result, Plugin), "config entry point should return a Plugin"


# fmt: off
# --8<-- [start:json-reporter]
class JsonReporter:
    def __init__(self, output_path):
        self._path = Path(output_path)
        self._events = []

    def test_started(self, *, item):
        self._events.append({"event": "started", "item": str(item)})

    def test_completed(self, *, item, outcome, duration_ms):
        self._events.append({
            "event": "completed",
            "item": str(item),
            "outcome": str(outcome),
            "duration_ms": duration_ms,
        })

    def finish(self, *, collect_errors, interrupted):
        self._events.append({"event": "finish", "interrupted": interrupted})
        self._path.write_text(json.dumps(self._events, indent=2))
# --8<-- [end:json-reporter]
# fmt: on


def test_json_reporter_collects_events():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    reporter = JsonReporter(str(path))
    reporter.test_started(item="test_a")
    reporter.test_completed(item="test_a", outcome="passed", duration_ms=1.0)
    reporter.finish(collect_errors=[], interrupted=False)
    data = json.loads(path.read_text())
    assert len(data) == 3, "should have 3 events"
    assert data[0]["event"] == "started", "first event should be started"
    path.unlink()


# fmt: off
# --8<-- [start:timestamp-backend]
class _TimestampHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self._records = records

    def emit(self, record):
        self._records.append({
            "time": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
        })


class TimestampBackend:
    def __init__(self):
        self._handler = None
        self._records = []

    def install(self):
        self._handler = _TimestampHandler(self._records)
        logging.root.addHandler(self._handler)

    def uninstall(self):
        if self._handler:
            logging.root.removeHandler(self._handler)

    @property
    def records(self):
        return self._records
# --8<-- [end:timestamp-backend]
# fmt: on


def test_timestamp_backend_captures():
    backend = TimestampBackend()
    backend.install()
    logging.warning("test message")
    backend.uninstall()
    assert len(backend.records) >= 1, "should capture at least one record"
    assert backend.records[-1]["message"] == "test message", (
        "should capture message text"
    )


# fmt: off
# --8<-- [start:connection-pool]
@injectable
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

    def create(self, *, ctx):
        return ConnectionPool(self._dsn)

    def teardown(self, *, value):
        value.release_all()

    @property
    def scope(self):
        return "each"

    @property
    def autouse(self):
        return False
# --8<-- [end:connection-pool]
# fmt: on


def test_pool_provider_lifecycle():
    provider = PoolProvider("localhost:5432/test")
    pool = provider.create(ctx=None)
    conn = pool.acquire()
    assert "localhost:5432/test" in conn, "connection should reference DSN"
    provider.teardown(value=pool)
    assert len(pool._connections) == 0, "teardown should release all connections"


# fmt: off
# --8<-- [start:retry-wrapper]
class RetryWrapper:
    @property
    def marker(self):
        return "retry"

    def wrap(self, *, test_fn, marker_args):
        count = marker_args.get("count", 1)
        last_result = None
        for _ in range(count):
            last_result = test_fn()
            if last_result.status == "passed":
                return last_result
        return last_result
# --8<-- [end:retry-wrapper]
# fmt: on


def test_retry_wrapper_retries():
    import types

    call_count = 0

    def flaky_test():
        nonlocal call_count
        call_count += 1
        status = "passed" if call_count >= 2 else "failed"
        return types.SimpleNamespace(status=status)

    wrapper = RetryWrapper()
    result = wrapper.wrap(test_fn=flaky_test, marker_args={"count": 3})
    assert result.status == "passed", "should pass after retry"
    assert call_count == 2, "should have retried once"


# fmt: off
# --8<-- [start:file-reporter]
class FileReporter:
    """Collects test events and writes them to a JSON file on finish."""

    def __init__(self, output_path: str):
        self._path = Path(output_path)
        self._events: list[dict] = []

    def test_started(self, *, item):
        self._events.append({"event": "started", "item": str(item)})

    def test_completed(self, *, item, outcome, duration_ms):
        self._events.append({
            "event": "completed",
            "item": str(item),
            "outcome": str(outcome),
            "duration_ms": duration_ms,
        })

    def finish(self, *, collect_errors, interrupted):
        self._events.append({
            "event": "finish",
            "errors": len(collect_errors),
            "interrupted": interrupted,
        })
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._events, indent=2))
# --8<-- [end:file-reporter]
# fmt: on


def test_file_reporter_writes_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "events.json"
        reporter = FileReporter(str(path))
        reporter.test_started(item="test_x")
        reporter.test_completed(item="test_x", outcome="passed", duration_ms=0.5)
        reporter.finish(collect_errors=[], interrupted=False)
        data = json.loads(path.read_text())
        assert data[-1]["event"] == "finish", "last event should be finish"


# === Protocol signatures — verified against actual Protocols ===
# These classes exist for documentation snippets. They shadow Protocol
# names at module level but don't conflict because the actual Protocols
# are only imported inside verification test functions.


# fmt: off
# --8<-- [start:reporter-protocol]
class Reporter(Protocol):
    def test_started(self, *, item: Any) -> None: ...
    def test_completed(self, *, item: Any, outcome: Any, duration_ms: float) -> None: ...
    def finish(self, *, collect_errors: list[Any], interrupted: bool) -> None: ...
# --8<-- [end:reporter-protocol]

# --8<-- [start:logbackend-protocol]
class LogBackend(Protocol):
    def install(self) -> None: ...
    def uninstall(self) -> None: ...

    @property
    def records(self) -> list[Any]: ...
# --8<-- [end:logbackend-protocol]

# --8<-- [start:fixture-provider-protocol]
class FixtureProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def fixture_type(self) -> type: ...

    def create(self, *, ctx: Any) -> object: ...
    def teardown(self, *, value: object) -> None: ...

    # Optional — defaults via getattr if not implemented:
    @property
    def scope(self) -> str: ...    # "each" (default), "shared", or "session"

    @property
    def autouse(self) -> bool: ... # False (default)
# --8<-- [end:fixture-provider-protocol]

# --8<-- [start:collector-protocol]
class Collector(Protocol):
    def collect(self, *, path: str, module: object) -> list[Any]: ...
# --8<-- [end:collector-protocol]

# --8<-- [start:execution-wrapper-protocol]
class ExecutionWrapper(Protocol):
    @property
    def marker(self) -> str: ...

    def wrap(self, *, test_fn: Any, marker_args: dict[str, Any]) -> Any: ...
# --8<-- [end:execution-wrapper-protocol]

# --8<-- [start:helper-provider-protocol]
class HelperProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def helper(self) -> Callable[..., Any]: ...
# --8<-- [end:helper-provider-protocol]
# fmt: on


def _protocol_methods(cls: type) -> set[str]:
    """Get method/property names defined directly on a Protocol class."""
    return {name for name in vars(cls) if not name.startswith("_")}


def test_reporter_protocol_matches_doc():
    from oxitest.plugin import Reporter as Actual

    assert _protocol_methods(Actual) == {"test_started", "test_completed", "finish"}, (
        "Reporter protocol changed — update write-plugins.md signatures"
    )


def test_logbackend_protocol_matches_doc():
    from oxitest.plugin import LogBackend as Actual

    assert _protocol_methods(Actual) == {"install", "uninstall", "records"}, (
        "LogBackend protocol changed — update write-plugins.md signatures"
    )


def test_fixture_provider_protocol_matches_doc():
    from oxitest.plugin import FixtureProvider as Actual

    assert _protocol_methods(Actual) == {
        "name",
        "fixture_type",
        "create",
        "teardown",
        "scope",
        "autouse",
    }, "FixtureProvider protocol changed — update write-plugins.md signatures"


def test_collector_protocol_matches_doc():
    from oxitest.plugin import Collector as Actual

    assert _protocol_methods(Actual) == {"collect"}, (
        "Collector protocol changed — update write-plugins.md signatures"
    )


def test_execution_wrapper_protocol_matches_doc():
    from oxitest.plugin import ExecutionWrapper as Actual

    assert _protocol_methods(Actual) == {"marker", "wrap"}, (
        "ExecutionWrapper protocol changed — update write-plugins.md signatures"
    )


def test_helper_provider_protocol_matches_doc():
    from oxitest.plugin import HelperProvider as Actual

    assert _protocol_methods(Actual) == {"name", "helper"}, (
        "HelperProvider protocol changed — update write-plugins.md signatures"
    )


def test_coverage_provider_protocol_matches_doc():
    from oxitest.plugin import CoverageProvider as Actual

    assert _protocol_methods(Actual) == {"start", "stop", "report"}, (
        "CoverageProvider protocol changed — update write-plugins.md signatures"
    )


def test_plugin_fields_match_doc():
    actual = {f.name for f in fields(Plugin)}
    documented = {
        "log_backends",
        "fixture_providers",
        "helper_providers",
        "execution_wrappers",
        "collectors",
        "reporters",
        "async_backend",
        "debugger_backend",
        "coverage_provider",
    }
    assert documented == actual, (
        "Plugin dataclass fields changed — update write-plugins.md Plugin definition"
    )


# === Runnable examples ===


# fmt: off
# --8<-- [start:cli-extension]
@dataclass(frozen=True)
class MyConfig:
    host: Annotated[str, Both(help="Target host", short="H", env="MY_HOST")]
    verbose: Annotated[bool, Cli(help="Verbose output")] = False
    retries: Annotated[int, Conf(help="Retry count (config only)")] = 3


# Declares CLI flags under the "myplugin" prefix
oxitest_cli_extension = CliExtension(prefix="myplugin", config_type=MyConfig)
# --8<-- [end:cli-extension]


def oxitest_plugin_typed(*, config: MyConfig | None = None) -> Plugin:
    # config is a fully typed MyConfig instance (not a dict)
    return Plugin()
# fmt: on


def test_cli_extension_wires_config():
    assert oxitest_cli_extension.prefix == "myplugin", (
        "CLI extension should declare its prefix"
    )
    assert oxitest_cli_extension.config_type is MyConfig, (
        "CLI extension should reference the config dataclass"
    )
    result = oxitest_plugin_typed(config=MyConfig(host="localhost"))
    assert isinstance(result, Plugin), "typed entry point should return a Plugin"


def oxitest_plugin_multi(config=None):
    return Plugin(
        reporters=(JsonReporter("/dev/null"),),
        fixture_providers=(PoolProvider("localhost:5432/test"),),
    )


def test_composability_multiple_protocols():
    plugin = oxitest_plugin_multi()
    assert len(plugin.reporters) == 1, "should wire one reporter"
    assert len(plugin.fixture_providers) == 1, "should wire one fixture provider"


# fmt: off
# --8<-- [start:check-collector]
class CheckCollector:
    def collect(self, *, path, module):
        items = []
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("check_"):
                lineno = inspect.getsourcelines(obj)[1]
                items.append(CollectedItem(
                    fn_name=name,
                    lineno=lineno,
                    markers=(),
                    param_id="",
                    param_values=(),
                ))
        return items
# --8<-- [end:check-collector]
# fmt: on


def oxitest_plugin_collector(config=None):
    return Plugin(collectors=(CheckCollector(),))


def test_check_collector_discovers_check_functions():
    import types

    mod = types.ModuleType("fake_module")
    mod.check_health = lambda: None
    mod.check_status = lambda: None
    mod.test_other = lambda: None

    collector = CheckCollector()
    items = collector.collect(path="/fake.py", module=mod)
    names = {item.fn_name for item in items}
    assert names == {"check_health", "check_status"}, (
        "should discover check_ functions and skip test_ functions"
    )


# === External-dep examples — test structure only, never call methods
#     that trigger lazy imports (trio, ipdb, slipcover) ===


# fmt: off
# --8<-- [start:trio-shared-session]
class TrioSharedSession:
    """Long-lived trio session for shared fixture resolution."""

    def run(self, coro):
        import trio
        return trio.from_thread.run(coro)

    def close(self):
        pass  # trio manages its own cleanup
# --8<-- [end:trio-shared-session]

# --8<-- [start:trio-backend]
class TrioBackend:
    """Trio async backend for oxitest."""

    @property
    def name(self) -> str:
        return "trio"

    def run(self, coro):
        import trio
        return trio.run(coro)

    def create_shared_session(self):
        return TrioSharedSession()
# --8<-- [end:trio-backend]

# fmt: on


def oxitest_plugin_trio(config=None) -> Plugin:
    return Plugin(async_backend=TrioBackend())


def test_trio_backend_wires_into_plugin():
    backend = TrioBackend()
    plugin = Plugin(async_backend=backend)
    assert plugin.async_backend is backend, "should wire into Plugin"
    assert backend.name == "trio", "should expose backend name"


def test_trio_shared_session_has_required_methods():
    session = TrioSharedSession()
    assert hasattr(session, "run"), "should have run method"
    assert hasattr(session, "close"), "should have close method"


# fmt: off
# --8<-- [start:ipdb-backend]
class IpdbBackend:
    """ipdb debugger backend for oxitest."""

    def trace(self) -> None:
        import ipdb
        ipdb.set_trace()

    def post_mortem(self, tb) -> None:
        import ipdb
        ipdb.post_mortem(tb)
# --8<-- [end:ipdb-backend]

# fmt: on


def oxitest_plugin_ipdb(config=None) -> Plugin:
    return Plugin(debugger_backend=IpdbBackend())


def test_ipdb_backend_wires_into_plugin():
    backend = IpdbBackend()
    plugin = Plugin(debugger_backend=backend)
    assert plugin.debugger_backend is backend, "should wire into Plugin"
    assert hasattr(backend, "trace"), "should have trace method"
    assert hasattr(backend, "post_mortem"), "should have post_mortem method"


# fmt: off
# --8<-- [start:slipcover-provider]
class SlipCoverProvider:
    def start(self) -> None:
        # Initialize alternative coverage tool
        ...

    def stop(self) -> None:
        # Stop collection, save data
        ...

    def report(self, *, fmt) -> int:
        # Generate report in requested format
        return 0
# --8<-- [end:slipcover-provider]

# fmt: on


def oxitest_plugin_slipcover(config=None):
    return Plugin(coverage_provider=SlipCoverProvider())


def test_slipcover_provider_wires_into_plugin():
    provider = SlipCoverProvider()
    plugin = Plugin(coverage_provider=provider)
    assert plugin.coverage_provider is provider, "should wire into Plugin"
    assert hasattr(provider, "start"), "should have start method"
    assert hasattr(provider, "stop"), "should have stop method"
    assert hasattr(provider, "report"), "should have report method"


# === Usage fragments — illustrative doc examples ===


# fmt: off
# --8<-- [start:test-math-example]
# tests/test_math.py
def test_addition():
    assert 1 + 1 == 2, "basic addition"


def test_multiplication():
    assert 3 * 4 == 12, "basic multiplication"
# --8<-- [end:test-math-example]
# fmt: on
