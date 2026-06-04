"""Integration test: plugin loading end-to-end."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
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


def _scaffold_plugin_project(
    tmp: TempDir,
    plugin_name: str,
    plugin_code: str,
    config: str = "",
    test_code: str = "def test_ok(): pass\n",
) -> Path:
    """Create plugin dir, pyproject.toml, and test file. Return project root."""
    project = Path(str(tmp))

    # Plugin package
    plugin_dir = project / plugin_name
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(plugin_code)

    # pyproject.toml — always declares the plugin; caller appends extra config
    toml = f'[tool.oxitest]\ntestpaths = ["tests"]\nplugins = ["{plugin_name}"]\n'
    if config:
        toml += config
    (project / "pyproject.toml").write_text(toml)

    # Test file
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pass.py").write_text(test_code)

    return project


def test_plugin_loads_and_entry_called(tmp: TempDir):
    """A declared plugin's oxitest_plugin() is called at startup."""
    marker_file = Path(str(tmp)) / "plugin_loaded.txt"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="my_plugin",
        plugin_code=textwrap.dedent("""\
            from oxitest.plugin import Plugin
            from pathlib import Path

            def oxitest_plugin(config=None):
                Path(config['marker_file']).write_text('loaded')
                return Plugin()
        """),
        config=(
            f"\n[tool.oxitest.plugin_settings.my_plugin]\n"
            f'marker_file = "{marker_file}"\n'
        ),
    )

    # Run oxitest from the project directory
    tests_dir = project / "tests"
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
    output_file = Path(str(tmp)) / "config_received.json"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="cfg_checker",
        plugin_code=textwrap.dedent("""\
            import json
            from pathlib import Path
            from oxitest.plugin import Plugin

            def oxitest_plugin(config=None):
                Path(config['output']).write_text(json.dumps(config))
                return Plugin()
        """),
        config=(
            f"\n[tool.oxitest.plugin_settings.cfg_checker]\n"
            f'output = "{output_file}"\n'
            f'level = "DEBUG"\n'
            f"retries = 3\n"
        ),
    )

    tests_dir = project / "tests"
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
    marker_file = Path(str(tmp)) / "backend_state.txt"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="log_plugin",
        plugin_code=textwrap.dedent("""\
            import logging
            from pathlib import Path
            from oxitest.plugin import Plugin

            class MarkerBackend:
                def __init__(self, marker_file):
                    self._marker = marker_file
                    self._records = []
                def install(self):
                    Path(self._marker).write_text('installed')
                def uninstall(self):
                    Path(self._marker).write_text('uninstalled')
                @property
                def records(self):
                    return self._records

            def oxitest_plugin(config=None):
                return Plugin(log_backends=[MarkerBackend(config['marker'])])
        """),
        config=(
            f'\n[tool.oxitest.plugin_settings.log_plugin]\nmarker = "{marker_file}"\n'
        ),
        test_code=textwrap.dedent("""\
            from oxitest import Fixture
            from oxitest._bridge._builtins._logcapture import _LogCapture

            def test_with_log(log: Fixture[_LogCapture]):
                assert log is not None
        """),
    )

    # Run oxitest — the test uses LogCapture which triggers backend install
    tests_dir = project / "tests"
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
    marker_file = Path(str(tmp)) / "test_result.txt"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="db_plugin",
        plugin_code=textwrap.dedent("""\
            from pathlib import Path
            from oxitest.plugin import Plugin

            class Database:
                def __init__(self):
                    self.connected = True

            class DatabaseProvider:
                @property
                def name(self):
                    return 'db'
                @property
                def fixture_type(self):
                    return Database
                def create(self, ctx):
                    return Database()
                def teardown(self, value):
                    value.connected = False

            def oxitest_plugin(config=None):
                return Plugin(fixture_providers=[DatabaseProvider()])
        """),
        test_code=textwrap.dedent(f"""\
            from pathlib import Path
            from oxitest import Fixture
            from db_plugin import Database

            MARKER = Path('{marker_file}')

            def test_uses_db(db: Fixture[Database]):
                assert db.connected, 'database should be connected'
                MARKER.write_text('injected')
        """),
    )

    tests_dir = project / "tests"
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
    output_file = Path(str(tmp)) / "reporter_events.json"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="reporter_plugin",
        plugin_code=textwrap.dedent("""\
            import json
            from pathlib import Path
            from oxitest.plugin import Plugin

            class FileReporter:
                def __init__(self, output_path):
                    self._path = Path(output_path)
                    self._events = []
                def test_started(self, item):
                    self._events.append({'event': 'started', 'item': str(item)})
                def test_completed(self, item, outcome, duration_ms):
                    self._events.append({
                        'event': 'completed',
                        'item': str(item),
                        'outcome': str(outcome),
                        'duration_ms': duration_ms,
                    })
                def finish(self, collect_errors, interrupted):
                    self._events.append({'event': 'finish'})
                    self._path.write_text(json.dumps(self._events))

            def oxitest_plugin(config=None):
                return Plugin(reporters=[FileReporter(config['output'])])
        """),
        config=(
            f"\n[tool.oxitest.plugin_settings.reporter_plugin]\n"
            f'output = "{output_file}"\n'
        ),
        test_code=textwrap.dedent("""\
            def test_one(): pass
            def test_two(): assert True
        """),
    )

    tests_dir = project / "tests"
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
    marker_file = Path(str(tmp)) / "collector_result.txt"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="check_collector",
        plugin_code=textwrap.dedent("""\
            import inspect
            from oxitest.plugin import Plugin
            from oxitest._bridge.result import CollectedItem

            class CheckCollector:
                def collect(self, path, module):
                    items = []
                    for name, obj in inspect.getmembers(module, inspect.isfunction):
                        if name.startswith('check_'):
                            lineno = inspect.getsourcelines(obj)[1]
                            items.append(CollectedItem(
                                fn_name=name,
                                lineno=lineno,
                                markers=(),
                                param_id=None,
                                param_values=(),
                            ))
                    return items

            def oxitest_plugin(config=None):
                return Plugin(collectors=[CheckCollector()])
        """),
        test_code=textwrap.dedent(f"""\
            from pathlib import Path

            MARKER = Path('{marker_file}')

            def test_normal():
                pass

            def check_extra():
                MARKER.write_text('collected')
        """),
    )

    tests_dir = project / "tests"
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
    marker_file = Path(str(tmp)) / "attempt_count.txt"

    project = _scaffold_plugin_project(
        tmp,
        plugin_name="retry_plugin",
        plugin_code=textwrap.dedent("""\
            from pathlib import Path
            from oxitest.plugin import Plugin

            class RetryWrapper:
                @property
                def marker(self):
                    return 'retry'
                def wrap(self, test_fn, marker_args):
                    count = marker_args.get('count', 1)
                    last_result = None
                    for _ in range(count):
                        last_result = test_fn()
                        if last_result.status == 'passed':
                            return last_result
                    return last_result

            def oxitest_plugin(config=None):
                return Plugin(execution_wrappers=[RetryWrapper()])
        """),
        config='markers = ["retry: retry a test multiple times"]\n',
        test_code=textwrap.dedent(f"""\
            from pathlib import Path
            import oxitest

            COUNTER = Path('{marker_file}')

            @oxitest.mark.retry(count=3)
            def test_flaky():
                n = int(COUNTER.read_text()) if COUNTER.exists() else 0
                n += 1
                COUNTER.write_text(str(n))
                assert n >= 2, f'attempt {{n}} failed'
        """),
    )

    tests_dir = project / "tests"
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
