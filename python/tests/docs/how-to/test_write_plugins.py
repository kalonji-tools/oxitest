"""Tested examples for the write-plugins how-to guide.

Plugin classes are tested by instantiating and calling protocol methods
directly — no plugin loader needed.
"""

import json
import logging
import tempfile
from pathlib import Path

from oxitest import injectable
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
# --8<-- [end:json-reporter]
# fmt: on


def test_json_reporter_collects_events():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    reporter = JsonReporter(str(path))
    reporter.test_started("test_a")
    reporter.test_completed("test_a", "passed", 1.0)
    reporter.finish([], False)
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

    def create(self, ctx):
        return ConnectionPool(self._dsn)

    def teardown(self, value):
        value.release_all()
# --8<-- [end:connection-pool]
# fmt: on


def test_pool_provider_lifecycle():
    provider = PoolProvider("localhost:5432/test")
    pool = provider.create(None)
    conn = pool.acquire()
    assert "localhost:5432/test" in conn, "connection should reference DSN"
    provider.teardown(pool)
    assert len(pool._connections) == 0, "teardown should release all connections"


# fmt: off
# --8<-- [start:retry-wrapper]
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
    result = wrapper.wrap(flaky_test, {"count": 3})
    assert result.status == "passed", "should pass after retry"
    assert call_count == 2, "should have retried once"


# fmt: off
# --8<-- [start:file-reporter]
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
# --8<-- [end:file-reporter]
# fmt: on


def test_file_reporter_writes_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "events.json"
        reporter = FileReporter(str(path))
        reporter.test_started("test_x")
        reporter.test_completed("test_x", "passed", 0.5)
        reporter.finish([], False)
        data = json.loads(path.read_text())
        assert data[-1]["event"] == "finish", "last event should be finish"
