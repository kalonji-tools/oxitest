"""Integration test: plugin loading end-to-end."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from oxitest import Fixture, TempDir


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
        f'[tool.oxitest]\n'
        f'testpaths = ["tests"]\n'
        f'plugins = ["my_plugin"]\n\n'
        f'[tool.oxitest.plugin_settings.my_plugin]\n'
        f'marker_file = "{marker_file}"\n'
    )

    # Write a passing test
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pass.py").write_text("def test_ok(): pass\n")

    # Run oxitest from the project directory
    env = {**os.environ, "PYTHONPATH": f"{project}:{os.environ.get('PYTHONPATH', '')}"}
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        capture_output=True,
        text=True,
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
        '[tool.oxitest]\n'
        'testpaths = ["."]\n'
        'plugins = ["nonexistent_plugin_xyz_12345"]\n'
    )
    (project / "test_pass.py").write_text("def test_ok(): pass\n")

    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(project), "--color=never"],
        cwd=str(project),
        capture_output=True,
        text=True,
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
        f'[tool.oxitest]\n'
        f'testpaths = ["tests"]\n'
        f'plugins = ["cfg_checker"]\n\n'
        f'[tool.oxitest.plugin_settings.cfg_checker]\n'
        f'output = "{output_file}"\n'
        f'level = "DEBUG"\n'
        f'retries = 3\n'
    )

    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pass.py").write_text("def test_ok(): pass\n")

    env = {**os.environ, "PYTHONPATH": f"{project}:{os.environ.get('PYTHONPATH', '')}"}
    subprocess.run(
        [sys.executable, "-m", "oxitest", str(tests_dir), "--color=never"],
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
    )

    assert output_file.exists(), (
        f"Plugin did not write config file — oxitest_plugin() was not called"
    )
    import json
    received = json.loads(output_file.read_text())
    assert received["level"] == "DEBUG", (
        f"expected level='DEBUG', got {received.get('level')!r}"
    )
    assert received["retries"] == 3, (
        f"expected retries=3, got {received.get('retries')!r}"
    )
