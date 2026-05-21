"""Integration test: plugin loading end-to-end."""

from __future__ import annotations

import os
import subprocess
import sys
from functools import partial
from pathlib import Path

from oxitest import TempDir

_run = partial(subprocess.run, capture_output=True, text=True, timeout=30)
_PYTHON_SRC = str(Path(__file__).resolve().parents[2] / "python")


def _env(*extra_paths: Path | str) -> dict[str, str]:
    parts = [str(p) for p in extra_paths]
    parts.append(_PYTHON_SRC)
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    return {**os.environ, "PYTHONPATH": ":".join(parts)}


def test_plugin_loads_and_entry_called(tmp: TempDir):
    """A declared plugin's oxitest_plugin() is called at startup."""
    project = Path(str(tmp))

    # Write a minimal plugin that writes a marker file when loaded
    plugin_dir = project / "my_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "from oxitest.plugin import Plugin\n"
        "from pathlib import Path\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path(config['marker_file']).write_text('loaded')\n"
        "    return Plugin()\n"
    )

    marker_file = project / "plugin_loaded.txt"

    # Write pyproject.toml declaring the plugin
    (project / "pyproject.toml").write_text(
        f"[tool.oxitest]\n"
        f'testpaths = ["tests"]\n'
        f'plugins = ["my_plugin"]\n\n'
        f"[tool.oxitest.plugin_settings.my_plugin]\n"
        f'marker_file = "{marker_file}"\n'
    )

    # Write a passing test
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pass.py").write_text("def test_ok(): pass\n")

    # Run oxitest from the project directory
    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert marker_file.exists(), (
        f"Plugin was not loaded — oxitest_plugin() was never called.\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert marker_file.read_text() == "loaded", (
        f"Plugin marker file has wrong content: {marker_file.read_text()!r}"
    )


def test_missing_plugin_exits_with_error(tmp: TempDir):
    """A missing plugin produces a clear error message."""
    project = Path(str(tmp))

    (project / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["nonexistent_plugin_xyz_12345"]\n'
    )
    (project / "test_pass.py").write_text("def test_ok(): pass\n")

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(project), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert result.returncode != 0, (
        f"Expected non-zero exit for missing plugin, got {result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "not found" in combined.lower(), (
        f"Expected 'not found' in error output.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_plugin_receives_config(tmp: TempDir):
    """Plugin config from [tool.oxitest.plugin_settings.*] is passed correctly."""
    project = Path(str(tmp))

    # Plugin writes received config to a file
    plugin_dir = project / "cfg_checker"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path(config['output']).write_text(json.dumps(config))\n"
        "    return Plugin()\n"
    )

    output_file = project / "config_received.json"

    (project / "pyproject.toml").write_text(
        f"[tool.oxitest]\n"
        f'testpaths = ["tests"]\n'
        f'plugins = ["cfg_checker"]\n\n'
        f"[tool.oxitest.plugin_settings.cfg_checker]\n"
        f'output = "{output_file}"\n'
        f'level = "DEBUG"\n'
        f"retries = 3\n"
    )

    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pass.py").write_text("def test_ok(): pass\n")

    env = _env(project)
    _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert output_file.exists(), (
        "Plugin did not write config file — oxitest_plugin() was not called"
    )
    import json

    received = json.loads(output_file.read_text())
    assert received["level"] == "DEBUG", (
        f"expected level='DEBUG', got {received.get('level')!r}"
    )
    assert received["retries"] == 3, (
        f"expected retries=3, got {received.get('retries')!r}"
    )


def test_plugin_log_backend_captures_records(tmp: TempDir):
    """A plugin-provided LogBackend is installed and captures log records."""
    project = Path(str(tmp))

    # Write a plugin that provides a LogBackend writing a marker on install
    plugin_dir = project / "log_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "import logging\n"
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "class MarkerBackend:\n"
        "    def __init__(self, marker_file):\n"
        "        self._marker = marker_file\n"
        "        self._records = []\n"
        "    def install(self):\n"
        "        Path(self._marker).write_text('installed')\n"
        "    def uninstall(self):\n"
        "        Path(self._marker).write_text('uninstalled')\n"
        "    @property\n"
        "    def records(self):\n"
        "        return self._records\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(log_backends=[MarkerBackend(config['marker'])])\n"
    )

    marker_file = project / "backend_state.txt"

    (project / "pyproject.toml").write_text(
        f"[tool.oxitest]\n"
        f'testpaths = ["tests"]\n'
        f'plugins = ["log_plugin"]\n\n'
        f"[tool.oxitest.plugin_settings.log_plugin]\n"
        f'marker = "{marker_file}"\n'
    )

    # Write a test that uses LogCapture (triggers backend install)
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_log.py").write_text(
        "from oxitest import Fixture\n"
        "from oxitest._bridge._builtins._logcapture import _LogCapture\n\n"
        "def test_with_log(log: Fixture[_LogCapture]):\n"
        "    assert log is not None\n"
    )

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert marker_file.exists(), (
        f"Plugin log backend was not installed.\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # After test completes, teardown should have called uninstall
    assert marker_file.read_text() == "uninstalled", (
        f"Expected backend to be uninstalled after test, "
        f"got state: {marker_file.read_text()!r}"
    )


def test_plugin_fixture_provider_injected_in_test(tmp: TempDir):
    """A plugin-provided fixture is injectable via Fixture[T] in a test."""
    project = Path(str(tmp))

    # Write a plugin that provides a Database fixture
    plugin_dir = project / "db_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "class Database:\n"
        "    def __init__(self):\n"
        "        self.connected = True\n\n"
        "class DatabaseProvider:\n"
        "    @property\n"
        "    def name(self):\n"
        "        return 'db'\n"
        "    @property\n"
        "    def fixture_type(self):\n"
        "        return Database\n"
        "    def create(self, ctx):\n"
        "        return Database()\n"
        "    def teardown(self, value):\n"
        "        value.connected = False\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(fixture_providers=[DatabaseProvider()])\n"
    )

    marker_file = project / "test_result.txt"

    (project / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["tests"]\nplugins = ["db_plugin"]\n'
    )

    # Write a test that injects the plugin fixture
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_db.py").write_text(
        "from pathlib import Path\n"
        "from oxitest import Fixture\n"
        "from db_plugin import Database\n\n"
        f"MARKER = Path('{marker_file}')\n\n"
        "def test_uses_db(db: Fixture[Database]):\n"
        "    assert db.connected, 'database should be connected'\n"
        "    MARKER.write_text('injected')\n"
    )

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert marker_file.exists(), (
        f"Plugin fixture was not injected — test didn't run.\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert marker_file.read_text() == "injected", (
        f"Expected 'injected', got {marker_file.read_text()!r}"
    )
    assert result.returncode == 0, (
        f"Test should pass, got exit code {result.returncode}\nstdout:\n{result.stdout}"
    )


def test_plugin_reporter_receives_events(tmp: TempDir):
    """A plugin-provided reporter receives test_started/test_completed/finish events."""
    project = Path(str(tmp))

    # Write a plugin that provides a reporter writing events to a file
    plugin_dir = project / "reporter_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "class FileReporter:\n"
        "    def __init__(self, output_path):\n"
        "        self._path = Path(output_path)\n"
        "        self._events = []\n"
        "    def test_started(self, item):\n"
        "        self._events.append({'event': 'started', 'item': str(item)})\n"
        "    def test_completed(self, item, outcome, duration_ms):\n"
        "        self._events.append({\n"
        "            'event': 'completed',\n"
        "            'item': str(item),\n"
        "            'outcome': str(outcome),\n"
        "            'duration_ms': duration_ms,\n"
        "        })\n"
        "    def finish(self, collect_errors, interrupted):\n"
        "        self._events.append({'event': 'finish'})\n"
        "        self._path.write_text(json.dumps(self._events))\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(reporters=[FileReporter(config['output'])])\n"
    )

    output_file = project / "reporter_events.json"

    (project / "pyproject.toml").write_text(
        f"[tool.oxitest]\n"
        f'testpaths = ["tests"]\n'
        f'plugins = ["reporter_plugin"]\n\n'
        f"[tool.oxitest.plugin_settings.reporter_plugin]\n"
        f'output = "{output_file}"\n'
    )

    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_one(): pass\ndef test_two(): assert True\n"
    )

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert output_file.exists(), (
        f"Plugin reporter did not write events file.\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    import json

    events = json.loads(output_file.read_text())

    started = [e for e in events if e["event"] == "started"]
    completed = [e for e in events if e["event"] == "completed"]
    finished = [e for e in events if e["event"] == "finish"]

    assert len(started) == 2, f"Expected 2 started events, got {len(started)}"
    assert len(completed) == 2, f"Expected 2 completed events, got {len(completed)}"
    assert len(finished) == 1, f"Expected 1 finish event, got {len(finished)}"


def test_plugin_collector_discovers_extra_items(tmp: TempDir):
    """A plugin collector adds items that appear in the test run."""
    project = Path(str(tmp))

    # Write a plugin that provides a collector discovering functions named check_*
    plugin_dir = project / "check_collector"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "import inspect\n"
        "from oxitest.plugin import Plugin\n"
        "from oxitest._bridge.result import CollectedItem\n\n"
        "class CheckCollector:\n"
        "    def collect(self, path, module):\n"
        "        items = []\n"
        "        for name, obj in inspect.getmembers(module, inspect.isfunction):\n"
        "            if name.startswith('check_'):\n"
        "                lineno = inspect.getsourcelines(obj)[1]\n"
        "                items.append(CollectedItem(\n"
        "                    fn_name=name,\n"
        "                    lineno=lineno,\n"
        "                    markers=[],\n"
        "                    param_id=None,\n"
        "                    param_values=[],\n"
        "                ))\n"
        "        return items\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(collectors=[CheckCollector()])\n"
    )

    marker_file = project / "collector_result.txt"

    (project / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["tests"]\nplugins = ["check_collector"]\n'
    )

    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_checks.py").write_text(
        "from pathlib import Path\n\n"
        f"MARKER = Path('{marker_file}')\n\n"
        "def test_normal():\n"
        "    pass\n\n"
        "def check_extra():\n"
        "    MARKER.write_text('collected')\n"
    )

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert marker_file.exists(), (
        f"Plugin collector item was not executed.\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert marker_file.read_text() == "collected", (
        f"Expected 'collected', got {marker_file.read_text()!r}"
    )
    # Should have collected 2 items: test_normal + check_extra
    assert "2" in result.stdout or "collected 2" in result.stdout, (
        f"Expected 2 collected items in output.\nstdout:\n{result.stdout}"
    )


def test_plugin_execution_wrapper_retries(tmp: TempDir):
    """A plugin ExecutionWrapper wraps test execution based on a marker."""
    project = Path(str(tmp))

    # Write a plugin that provides a retry wrapper
    plugin_dir = project / "retry_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "class RetryWrapper:\n"
        "    @property\n"
        "    def marker(self):\n"
        "        return 'retry'\n"
        "    def wrap(self, test_fn, marker_args):\n"
        "        count = marker_args.get('count', 1)\n"
        "        last_result = None\n"
        "        for _ in range(count):\n"
        "            last_result = test_fn()\n"
        "            if last_result.status == 'passed':\n"
        "                return last_result\n"
        "        return last_result\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(execution_wrappers=[RetryWrapper()])\n"
    )

    marker_file = project / "attempt_count.txt"

    (project / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["tests"]\n'
        'plugins = ["retry_plugin"]\n'
        'markers = ["retry: retry a test multiple times"]\n'
    )

    tests_dir = project / "tests"
    tests_dir.mkdir()
    # Test that fails on first attempt, passes on second
    (tests_dir / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        "import oxitest\n\n"
        f"COUNTER = Path('{marker_file}')\n\n"
        "@oxitest.mark.retry(count=3)\n"
        "def test_flaky():\n"
        "    n = int(COUNTER.read_text()) if COUNTER.exists() else 0\n"
        "    n += 1\n"
        "    COUNTER.write_text(str(n))\n"
        "    assert n >= 2, f'attempt {n} failed'\n"
    )

    env = _env(project)
    result = _run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        env=env,
    )

    assert result.returncode == 0, (
        f"Test should pass after retry, got exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert marker_file.exists(), "Counter file should exist after test execution"
    attempts = int(marker_file.read_text())
    assert attempts == 2, f"Expected 2 attempts (fail then pass), got {attempts}"
