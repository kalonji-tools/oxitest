"""Integration tests: flag interactions (--list, -k, --serial, --json, etc.)."""

import json
import subprocess
import sys
from pathlib import Path

import oxitest
from conftest import helpers
from oxitest import TempDir


def test_list_prints_node_ids_and_exits_zero(tmp: TempDir):
    """--list prints node IDs and exits 0 without running tests."""
    (tmp / "test_nodes.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--list")
    assert rc == 0, f"--list should exit 0, got {rc}"
    assert "test_alpha" in out, "node ID test_alpha should appear in --list output"
    assert "test_beta" in out, "node ID test_beta should appear in --list output"
    assert "test_gamma" in out, "node ID test_gamma should appear in --list output"
    assert "passed" not in out, "--list should not run tests (no 'passed' in output)"


def test_list_verbose_shows_table(tmp: TempDir):
    """--list -v shows a table with module and function columns."""
    (tmp / "test_table.py").write_text(
        "def test_one(): assert True\ndef test_two(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--list", "-v")
    assert rc == 0, f"--list -v should exit 0, got {rc}"
    assert "module" in out, "verbose list should show 'module' column header"
    assert "function" in out, "verbose list should show 'function' column header"


def test_keyword_filter(tmp: TempDir):
    """Only tests matching the -k keyword should run."""
    (tmp / "test_kw.py").write_text(
        "def test_alpha(): assert True\ndef test_beta(): assert False\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "-k", "alpha")
    assert rc == 0, f"-k alpha should exit 0 (only matching test runs), got {rc}"
    assert "1 passed" in out, "-k alpha should run exactly 1 test"


def test_serial_flag(tmp: TempDir):
    """--serial flag runs tests without parallel workers."""
    (tmp / "test_serial.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
    assert rc == 0, f"--serial should exit 0, got {rc}"
    assert "passed" in out, "--serial run should report passed tests"


def test_json_output(tmp: TempDir):
    """--json writes a valid CTRF JSON file with summary.passed >= 2."""
    (tmp / "test_js.py").write_text(
        "def test_one(): assert True\ndef test_two(): assert True\n"
    )
    json_path = Path(tmp) / "results.json"
    out, _, rc = helpers.common.run_oxitest(tmp, "--json", str(json_path))
    assert rc == 0, f"--json run should exit 0, got {rc}"
    assert json_path.exists(), "--json should create the output file"
    data = json.loads(json_path.read_text())
    passed = data["results"]["summary"]["passed"]
    assert passed >= 2, f"summary.passed should be >= 2, got {passed}"


def test_junit_xml_output(tmp: TempDir):
    """--junit-xml writes a valid JUnit XML file with expected structure."""
    (tmp / "test_jx.py").write_text(
        "def test_first(): assert True\ndef test_second(): assert True\n"
    )
    xml_path = Path(tmp) / "results.xml"
    out, _, rc = helpers.common.run_oxitest(tmp, "--junit-xml", str(xml_path))
    assert rc == 0, f"--junit-xml run should exit 0, got {rc}"
    assert xml_path.exists(), "--junit-xml should create the output file"
    xml_content = xml_path.read_text()
    assert "<testsuites" in xml_content, "JUnit XML should contain <testsuites element"
    assert "test_first" in xml_content, "JUnit XML should contain test_first name"
    assert "test_second" in xml_content, "JUnit XML should contain test_second name"


def test_marker_filter(tmp: TempDir):
    """Only tests matching the -m marker expression should run."""
    (tmp / "test_marked.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.slow\n"
        "def test_slow_one(): assert True\n\n"
        "def test_fast_one(): assert True\n"
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nmarkers = ["slow: slow tests"]\n')
    out, _, rc = helpers.common.run_oxitest(tmp, "-m", "slow")
    assert rc == 0, f"-m slow should exit 0, got {rc}"
    assert "1 passed" in out, "-m slow should run exactly 1 test"


def test_exitfirst_conflicts_with_maxfail(tmp: TempDir):
    """Flag conflict: -x and --maxfail are mutually exclusive."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "-x", "--maxfail", "5")
    assert rc == 4, f"-x/--maxfail conflict should exit 4, got {rc}"
    assert "-x" in stderr, f"stderr: {stderr!r}"
    assert "--maxfail" in stderr, f"stderr: {stderr!r}"


def test_verbose_conflicts_with_quiet(tmp: TempDir):
    """Flag conflict: --verbose and --quiet are opposite modes."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "-v", "-q")
    assert rc == 4, f"-v/-q conflict should exit 4, got {rc}"
    assert "--verbose" in stderr, f"stderr: {stderr!r}"
    assert "--quiet" in stderr, f"stderr: {stderr!r}"


def test_schedule_conflicts_with_serial(tmp: TempDir):
    """Flag conflict: --schedule has no effect in serial mode."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--schedule", "random", "--serial")
    assert rc == 4, f"--schedule/--serial conflict should exit 4, got {rc}"
    assert "--schedule" in stderr, f"stderr: {stderr!r}"
    assert "--serial" in stderr, f"stderr: {stderr!r}"


def test_retries_delay_requires_retries(tmp: TempDir):
    """Flag conflict: --retries-delay needs --retries."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--retries-delay", "5")
    assert rc == 4, f"--retries-delay without --retries should exit 4, got {rc}"
    assert "--retries-delay" in stderr, f"stderr: {stderr!r}"
    assert "--retries" in stderr, f"stderr: {stderr!r}"


def test_debug_with_passing_test_exits_0(tmp: TempDir):
    """--debug on a passing test exits 0 (no pdb triggered)."""
    (tmp / "test_ok.py").write_text("def test_pass():\n    assert True\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--debug")
    assert rc == 0, f"expected exit code 0, got {rc}\nstderr: {stderr!r}"


def test_debug_conflicts_are_rejected(tmp: TempDir):
    """--debug with --workers should produce exit code 4."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--debug", "--workers", "2")
    assert rc == 4, f"expected exit code 4, got {rc}\nstderr: {stderr!r}"
    assert "--debug" in stderr, f"error should mention --debug: {stderr!r}"
    assert "--workers" in stderr, f"error should mention --workers: {stderr!r}"


def test_debug_always_is_accepted(tmp: TempDir):
    """--debug=always is a valid flag (no parse error)."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--debug=always", "--list")
    assert rc == 0, f"expected exit code 0, got {rc}\nstderr: {stderr!r}"


def test_debug_always_allows_exitfirst(tmp: TempDir):
    """--debug=always -x should be accepted (not a conflict)."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--debug=always", "-x", "--list")
    assert rc == 0, (
        f"--debug=always -x --list should exit 0, got rc={rc}\nstderr: {stderr!r}"
    )


@oxitest.mark.timeout(120)
def test_debug_always_with_plugin_backend(tmp: TempDir):
    """A plugin-provided debugger backend should be invoked instead of pdb."""
    plugin_dir = Path(tmp) / "marker_debugger"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        "from oxitest.plugin import Plugin\n\n"
        "class MarkerDebugger:\n"
        "    def __init__(self, marker_path):\n"
        "        self._path = Path(marker_path)\n"
        "    def trace(self):\n"
        '        self._path.write_text("traced")\n'
        "    def post_mortem(self, tb):\n"
        "        pass\n\n"
        "def oxitest_plugin(config=None):\n"
        '    return Plugin(debugger_backend=MarkerDebugger(config["marker_path"]))\n'
    )

    (tmp / "test_ok.py").write_text("def test_pass():\n    assert True\n")

    marker_file = Path(tmp) / "debug_marker.txt"
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'plugins = ["marker_debugger"]\n\n'
        "[tool.oxitest.plugin_settings.marker_debugger]\n"
        f'marker_path = "{marker_file}"\n'
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--color",
            "never",
            "--debug=always",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(tmp)
            + __import__("os").pathsep
            + __import__("os").environ.get("PYTHONPATH", ""),
        },
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert marker_file.exists(), (
        f"marker file should exist (plugin trace() was called)\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert marker_file.read_text() == "traced", (
        f"marker file content wrong: {marker_file.read_text()!r}"
    )


def test_keep_tmp_preserves_on_failure(tmp: TempDir):
    """--keep-tmp preserves TempDir when test fails."""
    helpers.common.write_test_file(
        tmp,
        """\
from oxitest import Fixture
from oxitest._bridge._builtins import TempDir

def test_uses_tmp(t: Fixture[TempDir]) -> None:
    (t / "artifact.txt").write_text("data")
    assert False, "deliberate failure"
""",
        "test_fail_tmp.py",
    )
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--keep-tmp")
    assert rc != 0, "test should fail"
    assert "KEPT" in stderr, f"stderr should contain KEPT message, got: {stderr!r}"
    assert "--keep-tmp" in stderr, f"stderr should mention --keep-tmp, got: {stderr!r}"


def test_keep_tmp_cleans_on_pass(tmp: TempDir):
    """--keep-tmp=failed cleans up when test passes."""
    helpers.common.write_test_file(
        tmp,
        """\
from oxitest import Fixture
from oxitest._bridge._builtins import TempDir

def test_uses_tmp(t: Fixture[TempDir]) -> None:
    (t / "artifact.txt").write_text("data")
    assert True
""",
        "test_pass_tmp.py",
    )
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--keep-tmp")
    assert rc == 0, "test should pass"
    assert "KEPT" not in stderr, (
        f"stderr should NOT contain KEPT for passing tests, got: {stderr!r}"
    )
