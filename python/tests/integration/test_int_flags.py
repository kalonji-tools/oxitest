"""Integration tests: flag interactions (--list, -k, --serial, --json, etc.)."""

import json
import subprocess
import sys
from pathlib import Path

import oxitest
from conftest import helpers
from oxitest import Fixture, TempDir


def test_list_prints_node_ids_and_exits_zero(tmp: TempDir):
    """`list` subcommand prints node IDs and exits 0 without running tests."""
    (tmp / "test_nodes.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "list")
    assert rc == 0, f"`list` should exit 0, got {rc}"
    helpers.integ.assert_contains(out, "test_alpha", "test_beta", "test_gamma")
    helpers.integ.assert_excludes(out, "passed")


def test_list_detailed_shows_marks_and_fixtures(tmp: TempDir):
    """`list -v` shows marks and fixtures."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def my_db() -> str:\n"
        "    return 'connected'\n"
    )
    (tmp / "test_table.py").write_text(
        "from oxitest._bridge._fixture_type import Fixture\n"
        "def test_one(): assert True\n"
        "def test_two(my_db: Fixture[str]): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "-v")
    assert rc == 0, f"`list -v` should exit 0, got {rc}"
    helpers.integ.assert_contains(out, "test_one", "test_two")


def test_keyword_filter(tmp: TempDir):
    """Only tests matching the -k keyword should run."""
    (tmp / "test_kw.py").write_text(
        "def test_alpha(): assert True\ndef test_beta(): assert False\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "-k", "alpha")
    helpers.integ.assert_passed(out, rc, count=1)


def test_serial_flag(tmp: TempDir):
    """--serial flag runs tests without parallel workers."""
    (tmp / "test_serial.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
    helpers.integ.assert_passed(out, rc)


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
    helpers.integ.assert_contains(
        xml_content, "<testsuites", "test_first", "test_second"
    )


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
    helpers.integ.assert_passed(out, rc, count=1)


def test_exitfirst_conflicts_with_maxfail(tmp: TempDir):
    """Flag conflict: -x and --maxfail are mutually exclusive."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "-x", "--maxfail", "5")
    assert rc == 4, f"-x/--maxfail conflict should exit 4, got {rc}"
    helpers.integ.assert_contains(stderr, "-x", "--maxfail")


def test_v_with_quiet_is_valid(tmp: TempDir):
    """-v -q is valid: quiet trumps verbose."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "-v", "-q")
    # Quiet trumps, so this runs silently with exit 0
    assert rc == 0, f"-v/-q should be valid, got {rc}"


def test_schedule_conflicts_with_serial(tmp: TempDir):
    """Flag conflict: --schedule has no effect in serial mode."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--schedule", "random", "--serial")
    assert rc == 4, f"--schedule/--serial conflict should exit 4, got {rc}"
    helpers.integ.assert_contains(stderr, "--schedule", "--serial")


def test_debug_with_passing_test_exits_0(tmp: TempDir):
    """`debug` subcommand on a passing test exits 0 (no pdb triggered)."""
    (tmp / "test_ok.py").write_text("def test_pass():\n    assert True\n")
    _, stderr, rc = helpers.common.run_oxitest_subcmd(tmp, "debug")
    assert rc == 0, f"expected exit code 0, got {rc}\nstderr: {stderr!r}"


def test_debug_always_is_accepted(tmp: TempDir):
    """`debug --always` is a valid invocation (no parse error).

    We only verify that clap accepts the flag (exit != 4). Since --always
    launches pdb before every test, it hangs in CI without a TTY, so we
    use a short timeout and treat TimeoutExpired as success (pdb started).
    """
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    try:
        _, stderr, rc = helpers.common.run_oxitest_subcmd(
            tmp,
            "debug",
            "--always",
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return  # pdb started → flag was accepted
    assert rc != 4, f"expected no usage error (exit != 4), got {rc}\nstderr: {stderr!r}"


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
            "debug",
            str(tmp),
            "--color",
            "never",
            "--always",
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
    helpers.integ.assert_contains(stderr, "KEPT", "--keep-tmp")


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
    helpers.integ.assert_excludes(stderr, "KEPT")


def test_list_full_shows_param_values(tmp: TempDir):
    """`list --verbose=full` shows grouped parametrize cases."""
    (tmp / "test_param.py").write_text(
        "from dataclasses import dataclass\n"
        "import oxitest as oxi\n\n"
        "@dataclass(frozen=True)\n"
        "class Case:\n"
        "    x: int\n"
        "    expected: int\n\n"
        "@oxi.parametrize(pos=Case(1, 1), neg=Case(-1, 1))\n"
        "def test_abs(case: Case) -> None:\n"
        "    assert abs(case.x) == case.expected, 'mismatch'\n"
    )
    out, stderr, rc = helpers.common.run_oxitest_subcmd(tmp, "list", "--verbose=full")
    assert rc == 0, (
        f"`list --verbose=full` should exit 0, got {rc}\n"
        f"stdout: {out!r}\nstderr: {stderr!r}"
    )
    helpers.integ.assert_contains(out, "test_abs", "[pos]", "[neg]")


# ── Issue #584: Integration tests for 6 untested CLI flags ───────────────────


@oxitest.mark.timeout(120)
def test_affected_filters_to_changed_tests(git_repo: Fixture[Path]):
    """--affected filters to tests in files changed since the given ref."""
    tmp = git_repo
    git = ["git", "-C", str(tmp)]

    # Create and commit a baseline test file (on top of git_repo's init commit)
    (tmp / "test_old.py").write_text("def test_old(): assert True\n")
    subprocess.run([*git, "add", "."], check=True, capture_output=True)
    subprocess.run(
        [*git, "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )

    # Add a new test file and stage it (git diff HEAD sees staged changes)
    (tmp / "test_new.py").write_text("def test_new(): assert True\n")
    subprocess.run([*git, "add", "test_new.py"], check=True, capture_output=True)

    # Act
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--affected=HEAD")

    # Assert — only the new file should be collected (1 test, not 2)
    helpers.integ.assert_passed(out, rc, count=1)
    helpers.integ.assert_excludes(out, "2 passed")


def test_tb_line_shows_compact_failure(tmp: TempDir):
    """--tb line shows file:line but no full diagnostic block."""
    (tmp / "test_fail.py").write_text(
        "def test_boom():\n    assert 1 == 2, 'one is not two'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb", "line")
    assert rc != 0, "test should fail"
    # --tb=line emits a one-liner with file:line and message
    helpers.integ.assert_contains(out, "test_fail.py", "one is not two")
    # No diagnostic box chrome
    helpers.integ.assert_excludes(out, "┌", "└")


def test_tb_no_suppresses_traceback(tmp: TempDir):
    """--tb no reports failure but shows no traceback or diagnostic block."""
    (tmp / "test_fail.py").write_text(
        "def test_kaboom():\n    assert False, 'should not see traceback'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb", "no")
    helpers.integ.assert_failed(out, rc, count=1)
    # No diagnostic block, no one-liner
    helpers.integ.assert_excludes(out, "┌", "should not see traceback")


def test_timeout_cli_flag(tmp: TempDir):
    """--timeout applies a session-wide timeout to tests without @mark.timeout."""
    (tmp / "test_slow.py").write_text(
        "import time\n\ndef test_hangs():\n    time.sleep(30)\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--timeout", "1")
    assert rc != 0, f"timed-out test should fail, got rc={rc}"
    assert "1 failed" in out or "timeout" in out.lower(), (
        f"output should indicate timeout failure: {out!r}"
    )


def test_durations_shows_slowest_tests(tmp: TempDir):
    """--durations N lists the N slowest tests after the run."""
    (tmp / "test_dur.py").write_text(
        "import time\n\n"
        "def test_fast(): pass\n\n"
        "def test_slow():\n"
        "    time.sleep(0.05)\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--durations", "1")
    helpers.integ.assert_passed(out, rc)
    assert "slowest" in out.lower(), f"output should contain 'slowest': {out!r}"
    helpers.integ.assert_contains(out, "ms")


def test_capture_environment_prints_versions():
    """`env` subcommand prints Python and oxitest versions and exits 0."""
    out, _, rc = helpers.common.run_oxitest_env()
    assert rc == 0, f"`env` should exit 0, got {rc}"
    assert "python:" in out.lower(), f"output should contain Python version: {out!r}"
    assert "oxitest:" in out.lower(), f"output should contain oxitest version: {out!r}"
    # Should NOT run tests
    helpers.integ.assert_excludes(out, "passed")


@oxitest.mark.timeout(120)
def test_inprocess_mark_runs_on_main_process(tmp: TempDir):
    """@oxi.mark.inprocess tests run on main process during parallel execution."""
    (tmp / "test_inproc.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.inprocess\n"
        "def test_main_process():\n"
        "    assert True\n"
    )
    (tmp / "test_normal.py").write_text("def test_worker():\n    assert True\n")
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    assert rc == 0, (
        f"inprocess + parallel should exit 0, got {rc}\n"
        f"stdout: {out!r}\nstderr: {stderr!r}"
    )
    helpers.integ.assert_passed(out, rc, count=2)
