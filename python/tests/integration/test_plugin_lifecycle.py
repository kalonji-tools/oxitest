"""Integration tests: plugin lifecycle, CLI extensions, multi-plugin interaction.

Tests exercise the plugin system end-to-end via subprocess invocation.
Plugins with CLI extensions are fully activated after session init via
two-phase loading: Phase 1 discovers the plugin and defers entry-point
construction; Phase 2 builds the typed config from CLI/env/pyproject
and calls ``oxitest_plugin(config=...)``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import oxitest
from oxitest import TempDir, helpers

# -- Helpers -------------------------------------------------------------------

FIXTURES_DIR = str(Path(__file__).resolve().parent.parent / "fixtures")


def _run(
    tmp: TempDir, *args: str, env_extra: dict[str, str] | None = None
) -> tuple[str, str, int]:
    """Run oxitest with sample plugins on PYTHONPATH.

    Uses ``cwd=str(tmp)`` so that any marker files written by plugins
    via ``Path.cwd()`` land inside the temp directory.  Passes
    ``tmp_path=None`` to avoid the path being treated as a subcommand.
    """
    base_env = {**os.environ, "PYTHONPATH": FIXTURES_DIR}
    if env_extra:
        base_env.update(env_extra)
    return helpers.common.run_oxitest(None, *args, env=base_env, cwd=str(tmp))


def _run_top_flag(
    tmp: TempDir, flag: str, env_extra: dict[str, str] | None = None
) -> tuple[str, str, int]:
    """Run oxitest with a top-level flag (e.g. ``--help``) that must precede all others.

    ``run_oxitest`` inserts ``--color never`` before extra args, which
    causes clap to reject top-level flags like ``--help`` that exit
    immediately.  This helper builds the subprocess directly so that
    ``flag`` is the first argument, matching what a user would type.
    """
    import subprocess
    import sys

    base_env = {**os.environ, "PYTHONPATH": FIXTURES_DIR}
    if env_extra:
        base_env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", flag],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
        env=base_env,
    )
    return result.stdout, result.stderr, result.returncode


def _run_sub(
    tmp: TempDir, *args: str, env_extra: dict[str, str] | None = None
) -> tuple[str, str, int]:
    """Run oxitest subcommand with sample plugins on PYTHONPATH."""
    base_env = {**os.environ, "PYTHONPATH": FIXTURES_DIR}
    if env_extra:
        base_env.update(env_extra)
    return helpers.common.run_oxitest_subcmd(tmp, *args, cwd=".", env=base_env)


def _write_infra_project(tmp: TempDir, test_code: str, extra_toml: str = "") -> None:
    """Scaffold a project using oxitest_sample_infra plugin."""
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["oxitest_sample_infra"]\n'
        'markers = ["remote: run on remote host"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.oxitest_sample_infra]\n"
        'host = "default-host"\n'
        f"{extra_toml}"
    )
    (tmp / "test_example.py").write_text(test_code)


def _write_metrics_project(tmp: TempDir, test_code: str, extra_toml: str = "") -> None:
    """Scaffold a project using oxitest_sample_metrics plugin."""
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["oxitest_sample_metrics"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.oxitest_sample_metrics]\n"
        'output = "metrics.json"\n'
        f"{extra_toml}"
    )
    (tmp / "test_example.py").write_text(test_code)


def _write_both_plugins_project(tmp: TempDir, test_code: str) -> None:
    """Scaffold a project using both sample plugins."""
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["oxitest_sample_infra", "oxitest_sample_metrics"]\n'
        'markers = ["remote: run on remote host"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.oxitest_sample_infra]\n"
        'host = "default-host"\n'
        "\n"
        "[tool.oxitest.plugin_settings.oxitest_sample_metrics]\n"
        'output = "metrics.json"\n'
    )
    (tmp / "test_example.py").write_text(test_code)


def _write_inline_fixture_plugin(tmp: TempDir) -> None:
    """Write an inline plugin (no CLI extension) that provides a fixture."""
    (tmp / "fixture_plugin.py").write_text(
        "from pathlib import Path\n"
        "from typing import Any\n"
        "from oxitest import Plugin\n\n"
        "class SimpleValue:\n"
        "    def __init__(self, data: str) -> None:\n"
        "        self.data = data\n"
        "    def __repr__(self) -> str:\n"
        "        return f'SimpleValue({self.data!r})'\n\n"
        "class SimpleProvider:\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        "        return 'simple_value'\n"
        "    @property\n"
        "    def fixture_type(self) -> type:\n"
        "        return SimpleValue\n"
        "    def create(self, ctx: Any) -> SimpleValue:\n"
        "        return SimpleValue('hello')\n"
        "    def teardown(self, value: object) -> None:\n"
        "        pass\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path.cwd().joinpath('plugin_activated.txt').write_text('yes')\n"
        "    return Plugin(fixture_providers=(SimpleProvider(),))\n"
    )


def _write_inline_reporter_plugin(tmp: TempDir) -> None:
    """Write an inline plugin (no CLI extension) that provides a reporter."""
    (tmp / "reporter_plugin.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from typing import Any\n"
        "from oxitest import Plugin\n\n"
        "class SimpleReporter:\n"
        "    def __init__(self) -> None:\n"
        "        self._events: list[dict[str, Any]] = []\n"
        "    def test_started(self, item: Any) -> None:\n"
        "        self._events.append({'event': 'started', 'item': str(item)})\n"
        "    def test_completed(self, item: Any, outcome: Any, "
        "duration_ms: float) -> None:\n"
        "        self._events.append({'event': 'completed', "
        "'outcome': str(outcome)})\n"
        "    def finish(self, collect_errors: list[Any], "
        "*, interrupted: bool) -> None:\n"
        "        self._events.append({'event': 'finish'})\n"
        "        Path.cwd().joinpath('reporter_output.json').write_text(\n"
        "            json.dumps(self._events, indent=2))\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(reporters=(SimpleReporter(),))\n"
    )


def _write_inline_wrapper_plugin(tmp: TempDir) -> None:
    """Write an inline plugin (no CLI extension) with an ExecutionWrapper."""
    (tmp / "wrapper_plugin.py").write_text(
        "from pathlib import Path\n"
        "from typing import Any\n"
        "from oxitest import Plugin\n\n"
        "class SimpleWrapper:\n"
        "    @property\n"
        "    def marker(self) -> str:\n"
        "        return 'remote'\n"
        "    def wrap(self, test_fn: Any, marker_args: dict[str, Any]) -> Any:\n"
        "        Path.cwd().joinpath('wrapper_intercepted.txt').write_text('yes')\n"
        "        return test_fn()\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(execution_wrappers=(SimpleWrapper(),))\n"
    )


# -- CLI extension lifecycle ---------------------------------------------------


def test_cli_extension_discovered_via_query_plugins(tmp: TempDir) -> None:
    """``query plugins`` shows the infra plugin and its CLI extension prefix."""
    _write_infra_project(tmp, "def test_one(): pass\n")
    out, _, rc = _run_sub(tmp, "query", "plugins")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "oxitest_sample_infra")


def test_prefix_collision_produces_error(tmp: TempDir) -> None:
    """Two inline plugins with same prefix; oxitest exits with error."""
    (tmp / "dup_plugin_a.py").write_text(
        "from dataclasses import dataclass\n"
        "from typing import Annotated\n"
        "from oxitest import CliExtension, Cli, Plugin\n\n"
        "@dataclass(frozen=True)\n"
        "class CfgA:\n"
        "    flag_a: Annotated[bool, Cli(help='A')] = False\n\n"
        "oxitest_cli_extension = CliExtension(prefix='dup', config_type=CfgA)\n"
        "def oxitest_plugin(*, config=None): return Plugin()\n"
    )
    (tmp / "dup_plugin_b.py").write_text(
        "from dataclasses import dataclass\n"
        "from typing import Annotated\n"
        "from oxitest import CliExtension, Cli, Plugin\n\n"
        "@dataclass(frozen=True)\n"
        "class CfgB:\n"
        "    flag_b: Annotated[bool, Cli(help='B')] = False\n\n"
        "oxitest_cli_extension = CliExtension(prefix='dup', config_type=CfgB)\n"
        "def oxitest_plugin(*, config=None): return Plugin()\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["dup_plugin_a", "dup_plugin_b"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = _run(tmp)
    combined = out + err
    assert rc != 0, f"prefix collision should produce non-zero exit, got {rc}"
    assert "dup" in combined.lower(), (
        f"error should mention the conflicting prefix 'dup':\n{combined}"
    )


def test_plugin_without_extension_still_works(tmp: TempDir) -> None:
    """Plugin with no oxitest_cli_extension loads and activates normally."""
    _write_inline_fixture_plugin(tmp)
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["."]\nplugins = ["fixture_plugin"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    assert (tmp / "plugin_activated.txt").exists(), (
        "plugin without CLI extension should be activated during loading"
    )


def test_plugin_receives_pyproject_settings(tmp: TempDir) -> None:
    """Plugin without CLI extension receives plugin_settings dict as config."""
    (tmp / "config_echo_plugin.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from oxitest import Plugin\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path.cwd().joinpath('config_echo.json').write_text(\n"
        "        json.dumps(config))\n"
        "    return Plugin()\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["config_echo_plugin"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.config_echo_plugin]\n"
        'host = "my-server"\n'
        "timeout = 60\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    config = json.loads((tmp / "config_echo.json").read_text())
    assert config["host"] == "my-server", (
        f"plugin_settings should reach plugin, got {config['host']!r}"
    )
    assert config["timeout"] == 60, (
        f"plugin_settings int should reach plugin, got {config['timeout']!r}"
    )


def test_two_cli_extension_plugins_coexist(tmp: TempDir) -> None:
    """Two plugins with different CLI prefixes both discovered without error."""
    _write_both_plugins_project(tmp, "def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)


def test_plugin_with_cli_extension_activated_after_session(tmp: TempDir) -> None:
    """Plugin with CLI extension is activated after session init.

    Two-phase loading defers plugin entry-point during ``load_plugins()``,
    then ``activate_deferred_plugins()`` constructs the typed config and
    calls ``oxitest_plugin(config=...)``.  The marker file proves activation
    happened.
    """
    _write_infra_project(tmp, "def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    assert (tmp / "infra_config_received.json").exists(), (
        "plugin with CLI extension should be activated after session init"
    )


# -- Protocol lifecycle --------------------------------------------------------


def test_fixture_provider_injects_into_tests(tmp: TempDir) -> None:
    """Test function receives simple_value fixture from inline plugin."""
    _write_inline_fixture_plugin(tmp)
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["."]\nplugins = ["fixture_plugin"]\n'
    )
    (tmp / "test_example.py").write_text(
        "from oxitest import Fixture\n"
        "from fixture_plugin import SimpleValue\n\n"
        "def test_has_value(simple_value: Fixture[SimpleValue]) -> None:\n"
        "    print(f'got data={simple_value.data}')\n"
        "    assert simple_value.data == 'hello', (\n"
        "        f'expected hello, got {simple_value.data!r}'\n"
        "    )\n"
    )
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "got data=hello")


def test_reporter_receives_events_and_writes_output(tmp: TempDir) -> None:
    """Inline reporter plugin writes reporter_output.json with events."""
    _write_inline_reporter_plugin(tmp)
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["."]\nplugins = ["reporter_plugin"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    reporter_path = tmp / "reporter_output.json"
    assert reporter_path.exists(), "Reporter plugin should write reporter_output.json"
    events = json.loads(reporter_path.read_text())
    event_types = [e["event"] for e in events]
    assert "started" in event_types, (
        f"reporter should contain 'started' event, got {event_types}"
    )
    assert "completed" in event_types, (
        f"reporter should contain 'completed' event, got {event_types}"
    )
    assert "finish" in event_types, (
        f"reporter should contain 'finish' event, got {event_types}"
    )


def test_execution_wrapper_intercepts_marked_tests(tmp: TempDir) -> None:
    """@mark.remote test is intercepted by inline wrapper plugin."""
    _write_inline_wrapper_plugin(tmp)
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["wrapper_plugin"]\n'
        'markers = ["remote: run on remote host"]\n'
    )
    (tmp / "test_example.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.remote\n"
        "def test_remote_action() -> None:\n"
        "    print('remote executed')\n"
        "    assert True, 'remote test should pass'\n"
    )
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "remote executed")


# -- Multi-plugin interaction --------------------------------------------------


def test_two_plugins_active_simultaneously(tmp: TempDir) -> None:
    """Two inline plugins (no CLI extensions) both activate and run."""
    _write_inline_fixture_plugin(tmp)
    _write_inline_reporter_plugin(tmp)
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["fixture_plugin", "reporter_plugin"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    assert (tmp / "plugin_activated.txt").exists(), "fixture plugin should be activated"
    assert (tmp / "reporter_output.json").exists(), (
        "reporter plugin should write output"
    )


def test_fixtures_from_both_plugins_in_same_test(tmp: TempDir) -> None:
    """Single test receives fixtures from two different plugins."""
    _write_inline_fixture_plugin(tmp)
    # Second fixture plugin
    (tmp / "fixture_plugin_b.py").write_text(
        "from typing import Any\n"
        "from oxitest import Plugin\n\n"
        "class CounterValue:\n"
        "    def __init__(self) -> None:\n"
        "        self.count = 0\n"
        "    def increment(self) -> None:\n"
        "        self.count += 1\n\n"
        "class CounterProvider:\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        "        return 'counter'\n"
        "    @property\n"
        "    def fixture_type(self) -> type:\n"
        "        return CounterValue\n"
        "    def create(self, ctx: Any) -> CounterValue:\n"
        "        return CounterValue()\n"
        "    def teardown(self, value: object) -> None:\n"
        "        pass\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(fixture_providers=(CounterProvider(),))\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["fixture_plugin", "fixture_plugin_b"]\n'
    )
    (tmp / "test_example.py").write_text(
        "from oxitest import Fixture\n"
        "from fixture_plugin import SimpleValue\n"
        "from fixture_plugin_b import CounterValue\n\n"
        "def test_both(\n"
        "    simple_value: Fixture[SimpleValue],\n"
        "    counter: Fixture[CounterValue],\n"
        ") -> None:\n"
        "    print(f'data={simple_value.data}')\n"
        "    counter.increment()\n"
        "    assert counter.count == 1, (\n"
        "        f'expected count 1, got {counter.count}'\n"
        "    )\n"
    )
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "data=hello")


def test_query_plugins_shows_both_sample_plugins(tmp: TempDir) -> None:
    """``query plugins`` lists both sample plugins."""
    _write_both_plugins_project(tmp, "def test_one(): pass\n")
    out, _, rc = _run_sub(tmp, "query", "plugins")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "oxitest_sample_infra")
    helpers.integ.assert_contains(out, "oxitest_sample_metrics")


def test_plugin_configs_independent(tmp: TempDir) -> None:
    """Each plugin receives its own config without cross-contamination."""
    (tmp / "plugin_x.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from oxitest import Plugin\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path.cwd().joinpath('config_x.json').write_text(\n"
        "        json.dumps(config))\n"
        "    return Plugin()\n"
    )
    (tmp / "plugin_y.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from oxitest import Plugin\n\n"
        "def oxitest_plugin(config=None):\n"
        "    Path.cwd().joinpath('config_y.json').write_text(\n"
        "        json.dumps(config))\n"
        "    return Plugin()\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["."]\n'
        'plugins = ["plugin_x", "plugin_y"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.plugin_x]\n"
        'host = "x-host"\n'
        "\n"
        "[tool.oxitest.plugin_settings.plugin_y]\n"
        'host = "y-host"\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run(tmp)
    helpers.integ.assert_passed(out, rc)
    cfg_x = json.loads((tmp / "config_x.json").read_text())
    cfg_y = json.loads((tmp / "config_y.json").read_text())
    assert cfg_x["host"] == "x-host", (
        f"plugin_x should see x-host, got {cfg_x['host']!r}"
    )
    assert cfg_y["host"] == "y-host", (
        f"plugin_y should see y-host, got {cfg_y['host']!r}"
    )


# -- Error paths ---------------------------------------------------------------


def test_missing_plugin_module_error(tmp: TempDir) -> None:
    """plugins = ['nonexistent']; clear error message with module name."""
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["."]\nplugins = ["nonexistent_plugin_xyz"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = _run(tmp)
    combined = out + err
    assert rc != 0, f"missing plugin should produce non-zero exit, got {rc}"
    assert "nonexistent_plugin_xyz" in combined, (
        f"error should mention the missing module name:\n{combined}"
    )


def test_invalid_entrypoint_return_type_error(tmp: TempDir) -> None:
    """oxitest_plugin returns a string instead of Plugin; clear error."""
    (tmp / "bad_plugin.py").write_text(
        "def oxitest_plugin(config=None): return 'not a Plugin'\n"
    )
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["."]\nplugins = ["bad_plugin"]\n'
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    *_, rc = _run(tmp)
    assert rc != 0, f"invalid entrypoint should produce non-zero exit, got {rc}"


# -- Lazy loading --------------------------------------------------------------


def test_help_shows_plugin_flags(tmp: TempDir) -> None:
    """``--help`` includes plugin CLI flags when plugins are configured.

    Phase 1 defers the --help to Phase 2, which builds an extended parser
    with plugin flags, so plugin options appear in the help output.
    """
    _write_infra_project(tmp, "def test_one(): pass\n")
    out, err, rc = _run_top_flag(tmp, "--help")
    combined = out + err
    assert rc == 0, f"--help should exit 0, got {rc}\n{combined}"
    assert "--infra-host" in combined, (
        f"--help should show plugin flag --infra-host:\n{combined}"
    )
    assert "--infra-timeout" in combined, (
        f"--help should show plugin flag --infra-timeout:\n{combined}"
    )
    assert not (tmp / "infra_config_received.json").exists(), (
        "plugin should not be activated during --help"
    )


@dataclass(frozen=True)
class CliPrecedenceCase:
    cli_args: tuple[str, ...]
    env_extra: dict[str, str]
    expected_host: str


@oxitest.parametrize(
    default=CliPrecedenceCase(
        cli_args=(),
        env_extra={},
        expected_host="default-host",
    ),
    cli_overrides_pyproject=CliPrecedenceCase(
        cli_args=("--infra-host", "cli-host"),
        env_extra={},
        expected_host="cli-host",
    ),
    env_overrides_pyproject=CliPrecedenceCase(
        cli_args=(),
        env_extra={"OXITEST_INFRA_HOST": "env-host"},
        expected_host="env-host",
    ),
    cli_overrides_env=CliPrecedenceCase(
        cli_args=("--infra-host", "cli-host"),
        env_extra={"OXITEST_INFRA_HOST": "env-host"},
        expected_host="cli-host",
    ),
)
def test_plugin_config_precedence(tmp: TempDir, case: CliPrecedenceCase) -> None:
    """CLI > env > pyproject precedence for plugin config values."""
    _write_infra_project(tmp, "def test_one(): pass\n")
    out, err, rc = _run(tmp, *case.cli_args, env_extra=case.env_extra or None)
    combined = out + err
    helpers.integ.assert_passed(combined, rc)
    config = json.loads((tmp / "infra_config_received.json").read_text())
    assert config["host"] == case.expected_host, (
        f"expected host={case.expected_host!r} with cli={case.cli_args} "
        f"env={case.env_extra}, got {config['host']!r}"
    )


def test_unknown_flag_rejected_without_plugins(tmp: TempDir) -> None:
    """Unknown flags still produce errors when no plugins are configured."""
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\ntestpaths = ["."]\n')
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = _run(tmp, "--bogus-flag")
    assert rc != 0, f"unknown flag should produce non-zero exit, got {rc}"
    combined = (out + err).lower()
    assert "bogus" in combined or "unexpected" in combined, (
        f"error should mention the unknown flag:\n{out + err}"
    )
