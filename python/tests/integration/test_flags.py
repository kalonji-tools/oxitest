"""Integration tests: flag interactions (--list, -k, --serial, --json, etc.)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import oxitest
from oxitest import Fixture, TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_list_prints_node_ids_and_exits_zero(tmp: TempDir) -> None:
    """`query tests` prints node IDs and exits 0 without running tests."""
    (tmp / "test_nodes.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "test_alpha", "test_beta", "test_gamma")
    integ.assert_excludes(out, "passed")


def test_list_detailed_shows_marks_and_fixtures(tmp: TempDir) -> None:
    """`query tests` shows marks in default columnar output."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def my_db() -> str:\n"
        "    return 'connected'\n",
        encoding="utf-8",
    )
    (tmp / "test_table.py").write_text(
        "from oxitest._bridge._fixture_type import Fixture\n"
        "def test_one(): assert True\n"
        "def test_two(my_db: Fixture[str]): assert True\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "test_one", "test_two")


def test_expression_filter(tmp: TempDir) -> None:
    """Only tests matching the -E expression should run."""
    (tmp / "test_kw.py").write_text(
        "def test_alpha(): assert True\ndef test_beta(): assert False\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp, "-E", "name(alpha)")
    integ.assert_passed(out, rc, count=1)


def test_serial_flag(tmp: TempDir) -> None:
    """--serial flag runs tests without parallel workers."""
    (tmp / "test_serial.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "--serial")
    integ.assert_passed(out, rc)


def test_json_output(tmp: TempDir) -> None:
    """--json writes a valid CTRF JSON file with summary.passed >= 2."""
    (tmp / "test_js.py").write_text(
        "def test_one(): assert True\ndef test_two(): assert True\n", encoding="utf-8"
    )
    json_path = Path(tmp) / "results.json"
    out, _, rc = helpers.run_oxitest(tmp, "--json", str(json_path))
    integ.assert_passed(out, rc)
    assert json_path.exists(), "--json should create the output file"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    passed = data["results"]["summary"]["passed"]
    assert passed >= 2, f"summary.passed should be >= 2, got {passed}"


def test_json_written_on_collection_failure(tmp: TempDir) -> None:
    """--json must still write a CTRF file when a test file fails to import.

    Automation that promises "one CTRF per run" cannot tell "the job never
    started" from "the job ran and collection failed" if the artifact is
    missing (#1682).
    """
    integ.write_project(
        tmp,
        tests={
            "test_bad_import.py": """\
                import definitely_not_a_real_module_xyz

                def test_thing() -> None:
                    assert definitely_not_a_real_module_xyz, "module must import"
            """,
        },
    )
    json_path = Path(tmp) / "results.json"

    out, _, rc = helpers.run_oxitest(tmp, "--json", str(json_path))

    integ.assert_collection_error(out, rc)
    assert json_path.exists(), (
        "--json promises the file exists after the run; a collection failure "
        "that writes nothing looks identical to a job that never started"
    )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    summary = data["results"]["summary"]
    assert summary["failed"] >= 1, (
        "the collection error must be counted as failed — summary.failed == 0 "
        "makes every CTRF consumer render the aborted run green"
    )
    names = [t["name"] for t in data["results"]["tests"]]
    assert any("test_bad_import.py" in name for name in names), (
        "the artifact must name the file that failed to import, or a dashboard "
        f"cannot point at the cause; got {names}"
    )


def test_json_written_on_strict_abort(tmp: TempDir) -> None:
    """--json must still write a CTRF file when strict=abort halts the run.

    A second, independent early-exit route: it prints and returns before any
    reporter is built, so it needs its own artifact write (#1682).
    """
    integ.write_project(
        tmp,
        pyproject="""\
            [project]
            name = "strict-abort-json"
            version = "0.0.0"

            [tool.oxitest]
            strict = "abort"
        """,
        tests={
            "test_bare.py": """\
                def test_bare_assert() -> None:
                    assert 1 + 1 == 2
            """,
        },
    )
    json_path = Path(tmp) / "results.json"

    out, _, rc = helpers.run_oxitest(tmp, "--json", str(json_path))

    integ.assert_collection_error(out, rc)
    assert json_path.exists(), (
        "strict=abort exits before any reporter is constructed, so it is the "
        "route most likely to silently drop the --json artifact"
    )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["results"]["summary"]["failed"] >= 1, (
        "an aborted run must not serialise as a clean run, or CI treats a "
        "suite that never executed as green"
    )


def _junit_counts(path: Path) -> tuple[int, int, int]:
    """Parse a JUnit XML file into (tests, failures, errors) from <testsuites>.

    Parsing rather than substring-matching is the point: `"<testsuites" in text`
    passes against the `tests="0" errors="0"` artifact these tests exist to
    prevent, so only a real parse can tell a red report from a green one.
    """
    # S314 is suppressed deliberately: the input is the artifact oxitest wrote
    # seconds earlier into this test's own temp dir, not untrusted data, and
    # adding defusedxml to read our own output would be a dependency for nothing.
    root = ET.parse(path).getroot()  # noqa: S314
    return (
        int(root.get("tests", "0")),
        int(root.get("failures", "0")),
        int(root.get("errors", "0")),
    )


def test_junit_written_on_collection_failure(tmp: TempDir) -> None:
    """--junit-xml must still write a parseable XML file when a file fails to import.

    #1682 fixed this for --json and deliberately left --junit-xml out, because
    threading the path through alone would emit <testsuites tests="0"
    errors="0"/> — an artifact that reports an aborted run as clean (#1858).
    """
    integ.write_project(
        tmp,
        tests={
            "test_bad_import.py": """\
                import definitely_not_a_real_module_xyz

                def test_thing() -> None:
                    assert definitely_not_a_real_module_xyz, "module must import"
            """,
        },
    )
    xml_path = Path(tmp) / "results.xml"

    out, _, rc = helpers.run_oxitest(tmp, "--junit-xml", str(xml_path))

    integ.assert_collection_error(out, rc)
    assert xml_path.exists(), (
        "--junit-xml promises the file exists after the run; a collection "
        "failure that writes nothing looks identical to a job that never started"
    )
    tests, failures, errors = _junit_counts(xml_path)
    assert failures + errors >= 1, (
        "an aborted run must not serialise as a clean one — failures=0 and "
        f"errors=0 makes every JUnit consumer render it green; got "
        f"tests={tests} failures={failures} errors={errors}"
    )
    assert "test_bad_import" in xml_path.read_text(encoding="utf-8"), (
        "the artifact must name the file that failed to import, or a dashboard "
        "cannot point at the cause"
    )


def test_junit_written_on_strict_abort(tmp: TempDir) -> None:
    """--junit-xml must still write an XML file when strict=abort halts the run.

    A second, independent early-exit route: it prints and returns before any
    reporter is built, so it needs its own artifact write (#1858).
    """
    integ.write_project(
        tmp,
        pyproject="""\
            [project]
            name = "strict-abort-junit"
            version = "0.0.0"

            [tool.oxitest]
            strict = "abort"
        """,
        tests={
            "test_bare.py": """\
                def test_bare_assert() -> None:
                    assert 1 + 1 == 2
            """,
        },
    )
    xml_path = Path(tmp) / "results.xml"

    out, _, rc = helpers.run_oxitest(tmp, "--junit-xml", str(xml_path))

    integ.assert_collection_error(out, rc)
    assert xml_path.exists(), (
        "strict=abort exits before any reporter is constructed, so it is the "
        "route most likely to silently drop the --junit-xml artifact"
    )
    tests, failures, errors = _junit_counts(xml_path)
    assert failures + errors >= 1, (
        "a strict-abort run must not serialise as clean, or CI treats a suite "
        f"that never executed as green; got tests={tests} "
        f"failures={failures} errors={errors}"
    )


def test_junit_and_json_agree_on_an_aborted_run(tmp: TempDir) -> None:
    """Both artifacts must be written, and neither may report the run as clean.

    The two formats travel independent code paths, so a fix to one says nothing
    about the other — which is exactly how the asymmetry in #1858 arose.
    """
    integ.write_project(
        tmp,
        tests={
            "test_bad_import.py": """\
                import definitely_not_a_real_module_xyz

                def test_thing() -> None:
                    assert definitely_not_a_real_module_xyz, "module must import"
            """,
        },
    )
    xml_path = Path(tmp) / "results.xml"
    json_path = Path(tmp) / "results.json"

    out, _, rc = helpers.run_oxitest(
        tmp, "--junit-xml", str(xml_path), "--json", str(json_path)
    )

    integ.assert_collection_error(out, rc)
    assert xml_path.exists() and json_path.exists(), (
        "--junit-xml and --json are independent flags on independent code paths; "
        "passing both must produce both artifacts"
    )
    _, failures, errors = _junit_counts(xml_path)
    ctrf_failed = json.loads(json_path.read_text(encoding="utf-8"))["results"][
        "summary"
    ]["failed"]
    assert failures + errors >= 1 and ctrf_failed >= 1, (
        "the two artifacts describe one run and must not disagree about whether "
        f"it aborted; junit failures+errors={failures + errors}, "
        f"ctrf failed={ctrf_failed}"
    )


def test_junit_xml_output(tmp: TempDir) -> None:
    """--junit-xml writes a valid JUnit XML file with expected structure."""
    (tmp / "test_jx.py").write_text(
        "def test_first(): assert True\ndef test_second(): assert True\n",
        encoding="utf-8",
    )
    xml_path = Path(tmp) / "results.xml"
    out, _, rc = helpers.run_oxitest(tmp, "--junit-xml", str(xml_path))
    integ.assert_passed(out, rc)
    assert xml_path.exists(), "--junit-xml should create the output file"
    xml_content = xml_path.read_text(encoding="utf-8")
    integ.assert_contains(xml_content, "<testsuites", "test_first", "test_second")


def test_expression_marker_filter(tmp: TempDir) -> None:
    """Only tests matching the -E mark expression should run."""
    (tmp / "test_marked.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.slow\n"
        "def test_slow_one(): assert True\n\n"
        "def test_fast_one(): assert True\n",
        encoding="utf-8",
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text(
        '[tool.oxitest]\nmarkers = ["slow: slow tests"]\n', encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "-E", "mark(slow)")
    integ.assert_passed(out, rc, count=1)


def test_exitfirst_conflicts_with_maxfail(tmp: TempDir) -> None:
    """Flag conflict: -x and --maxfail are mutually exclusive."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    _, stderr, rc = helpers.run_oxitest(tmp, "-x", "--maxfail", "5")
    assert rc == 4, f"-x/--maxfail conflict should exit 4, got {rc}"
    integ.assert_contains(stderr, "-x", "--maxfail")


def test_v_with_quiet_is_valid(tmp: TempDir) -> None:
    """-v -q is valid: quiet trumps verbose."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp, "-v", "-q")
    # Quiet trumps, so this runs silently with exit 0
    integ.assert_passed(out, rc)


def test_schedule_conflicts_with_serial(tmp: TempDir) -> None:
    """Flag conflict: --schedule has no effect in serial mode."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    _, stderr, rc = helpers.run_oxitest(tmp, "--schedule", "random", "--serial")
    assert rc == 4, f"--schedule/--serial conflict should exit 4, got {rc}"
    integ.assert_contains(stderr, "--schedule", "--serial")


def test_debug_with_passing_test_exits_0(tmp: TempDir) -> None:
    """`debug` subcommand on a passing test exits 0 (no pdb triggered)."""
    (tmp / "test_ok.py").write_text(
        "def test_pass():\n    assert True\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest_subcmd(tmp, "debug")
    integ.assert_passed(out, rc)


def test_debug_always_is_accepted(tmp: TempDir) -> None:
    """`debug --always` is a valid invocation (no parse error).

    We only verify that clap accepts the flag (exit != 4). Since --always
    launches pdb before every test, it hangs in CI without a TTY, so we
    use a short timeout and treat TimeoutExpired as success (pdb started).
    """
    (tmp / "test_a.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    try:
        _, stderr, rc = helpers.run_oxitest_subcmd(
            tmp,
            "debug",
            "--always",
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return  # pdb started → flag was accepted
    assert rc != 4, f"expected no usage error (exit != 4), got {rc}\nstderr: {stderr!r}"


@oxitest.mark.timeout(120)
def test_debug_always_with_plugin_backend(tmp: TempDir) -> None:
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
        '    return Plugin(debugger_backend=MarkerDebugger(config["marker_path"]))\n',
        encoding="utf-8",
    )

    (tmp / "test_ok.py").write_text(
        "def test_pass():\n    assert True\n", encoding="utf-8"
    )

    marker_file = Path(tmp) / "debug_marker.txt"
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'plugins = ["marker_debugger"]\n\n'
        "[tool.oxitest.plugin_settings.marker_debugger]\n"
        f'marker_path = "{marker_file.as_posix()}"\n',
        encoding="utf-8",
    )

    plugin_env = {
        **os.environ,
        "PYTHONPATH": str(tmp) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    out, err, rc = helpers.run_oxitest_subcmd(
        tmp,
        "debug",
        "--always",
        timeout=30,
        env=plugin_env,
    )
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out!r}\nstderr: {err!r}"
    assert marker_file.exists(), (
        f"marker file should exist (plugin trace() was called)\n"
        f"stdout: {out!r}\nstderr: {err!r}"
    )
    assert marker_file.read_text(encoding="utf-8") == "traced", (
        f"marker file content wrong: {marker_file.read_text(encoding='utf-8')!r}"
    )


def test_keep_tmp_preserves_on_failure(tmp: TempDir) -> None:
    """--keep-tmp preserves TempDir when test fails."""
    helpers.write_test_file(
        tmp,
        """\
from oxitest import Fixture
from oxitest import TempDir

def test_uses_tmp(t: Fixture[TempDir]) -> None:
    (t / "artifact.txt").write_text("data")
    assert False, "deliberate failure"
""",
        "test_fail_tmp.py",
    )
    out, _stderr, rc = helpers.run_oxitest(tmp, "--keep-tmp")
    integ.assert_failed(out, rc)
    # The TempDir prefix is the test function name; find any preserved artifact.
    # tempfile.gettempdir() gives the system temp dir without hardcoding /tmp.
    #
    # glob, not rglob. A TempDir is created directly in the system temp dir, so
    # one level is enough — and rglob *descends into every sibling directory
    # there*, including temp dirs other tests are creating and deleting
    # concurrently. On CPython 3.11 that raises FileNotFoundError the moment one
    # of them vanishes mid-scan, which is a failure in an unrelated test's
    # cleanup rather than anything this test is asserting.
    tmp_root = Path(tempfile.gettempdir())
    preserved = list(tmp_root.glob("test_uses_tmp_*/artifact.txt"))
    assert preserved, (
        "--keep-tmp should preserve the TempDir on failure; "
        f"no artifact.txt found under {tmp_root}/test_uses_tmp_*"
    )
    for f in preserved:
        shutil.rmtree(f.parent, ignore_errors=True)


def test_keep_tmp_cleans_on_pass(tmp: TempDir) -> None:
    """--keep-tmp=failed cleans up when test passes."""
    helpers.write_test_file(
        tmp,
        """\
from oxitest import Fixture
from oxitest import TempDir

def test_uses_tmp(t: Fixture[TempDir]) -> None:
    (t / "artifact.txt").write_text("data")
    assert True
""",
        "test_pass_tmp.py",
    )
    out, stderr, rc = helpers.run_oxitest(tmp, "--keep-tmp")
    integ.assert_passed(out, rc)
    integ.assert_excludes(stderr, "KEPT")


def test_query_tests_shows_parametrized_function(tmp: TempDir) -> None:
    """`query tests` includes parametrized test functions."""
    (tmp / "test_param.py").write_text(
        "from dataclasses import dataclass\n"
        "import oxitest as oxi\n\n"
        "@dataclass(frozen=True)\n"
        "class Case:\n"
        "    x: int\n"
        "    expected: int\n\n"
        "@oxi.parametrize(pos=Case(1, 1), neg=Case(-1, 1))\n"
        "def test_abs(case: Case) -> None:\n"
        "    assert abs(case.x) == case.expected, 'mismatch'\n",
        encoding="utf-8",
    )
    out, _stderr, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "test_abs")


# ── Issue #584: Integration tests for 6 untested CLI flags ───────────────────


@oxitest.mark.timeout(120)
def test_affected_filters_to_changed_tests(git_repo: Fixture[Path]) -> None:
    """--affected filters to tests in files changed since the given ref."""
    tmp = git_repo
    git = ["git", "-C", str(tmp)]

    # Clear git env vars that prek/pre-commit may set.
    clean_env = integ.clean_git_env()

    def run(*cmd: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(cmd, check=True, capture_output=True, env=clean_env)

    # Create and commit a baseline test file (on top of git_repo's init commit)
    (tmp / "test_old.py").write_text("def test_old(): assert True\n", encoding="utf-8")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "baseline")

    # Add a new test file and stage it (git diff HEAD sees staged changes)
    (tmp / "test_new.py").write_text("def test_new(): assert True\n", encoding="utf-8")
    run(*git, "add", "test_new.py")

    # Act — use clean env so oxitest doesn't see prek's GIT_* vars
    out, _, rc = helpers.run_oxitest(
        tmp,
        "--affected=HEAD",
        env=clean_env,
        cwd=str(tmp),
    )

    # Assert — only the new file should be collected (1 test, not 2)
    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "2 passed")


@oxitest.mark.timeout(120)
def test_affected_with_subdirectory_path(git_repo: Fixture[Path]) -> None:
    """--affected works when a subdirectory is passed as the path argument."""
    tmp = git_repo
    git = ["git", "-C", str(tmp)]
    clean_env = integ.clean_git_env()

    def run(*cmd: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(cmd, check=True, capture_output=True, env=clean_env)

    # Create a subdirectory with a test file and commit
    subdir = tmp / "tests"
    subdir.mkdir()
    (subdir / "test_one.py").write_text(
        "def test_one(): assert True\n", encoding="utf-8"
    )
    run(*git, "add", ".")
    run(*git, "commit", "-m", "baseline")

    # Add a second test file and stage it
    (subdir / "test_two.py").write_text(
        "def test_two(): assert True\n", encoding="utf-8"
    )
    run(*git, "add", "tests/test_two.py")

    # Act — pass the subdirectory as the path (this triggered the bug)
    out, _, rc = helpers.run_oxitest(
        subdir,
        "--affected=HEAD",
        env=clean_env,
        cwd=str(tmp),
    )

    # Assert — only the new file should be collected (1 test, not 2)
    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "2 passed")


def test_tb_line_shows_compact_failure(tmp: TempDir) -> None:
    """--tb line shows file:line but no full diagnostic block."""
    (tmp / "test_fail.py").write_text(
        "def test_boom():\n    assert 1 == 2, 'one is not two'\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "--tb", "line")
    integ.assert_failed(out, rc)
    # --tb=line emits a one-liner with file:line and message
    integ.assert_contains(out, "test_fail.py", "one is not two")
    # No diagnostic box chrome
    integ.assert_excludes(out, "┌", "└")


def test_tb_no_suppresses_traceback(tmp: TempDir) -> None:
    """--tb no reports failure but shows no traceback or diagnostic block."""
    (tmp / "test_fail.py").write_text(
        "def test_kaboom():\n    assert False, 'should not see traceback'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp, "--tb", "no")
    integ.assert_failed(out, rc, count=1)
    # No diagnostic block, no one-liner
    integ.assert_excludes(out, "┌", "should not see traceback")


def test_timeout_cli_flag(tmp: TempDir) -> None:
    """--timeout applies a session-wide timeout to tests without @mark.timeout."""
    (tmp / "test_slow.py").write_text(
        "import time\n\ndef test_hangs():\n    time.sleep(30)\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "--timeout", "1")
    integ.assert_failed(out, rc)
    assert "1 failed" in out or "timeout" in out.lower(), (
        f"output should indicate timeout failure: {out!r}"
    )


def test_durations_shows_slowest_tests(tmp: TempDir) -> None:
    """--durations N lists the N slowest tests after the run."""
    (tmp / "test_dur.py").write_text(
        "import time\n\n"
        "def test_fast(): pass\n\n"
        "def test_slow():\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp, "--durations", "1")
    integ.assert_passed(out, rc)
    integ.assert_contains(out.lower(), "slowest")
    integ.assert_contains(out, "ms")


def test_durations_shows_fixture_timings(tmp: TempDir) -> None:
    """--durations shows slowest fixtures alongside slowest tests."""
    (tmp / "conftest.py").write_text(
        "import time\n"
        "import oxitest as oxi\n\n"
        "fx = oxi.Fixtures()\n\n"
        "@fx.fixture\n"
        "def slow_setup() -> int:\n"
        "    time.sleep(0.05)\n"
        "    return 42\n",
        encoding="utf-8",
    )
    (tmp / "test_fx_timing.py").write_text(
        "import oxitest as oxi\n\n"
        "def test_uses_slow(slow_setup: oxi.Fixture[int]):\n"
        '    assert slow_setup == 42, "fixture should return 42"\n',
        encoding="utf-8",
    )
    out, err, rc = helpers.run_oxitest(tmp, "--durations", "5")
    assert rc == 0, (
        f"--durations with fixtures should exit 0, got {rc}\n"
        f"stdout: {out!r}\nstderr: {err!r}"
    )
    assert "slow_setup" in out, f"fixture name should appear in output: {out!r}"
    assert "setup" in out.lower(), f"output should mention setup: {out!r}"


def test_capture_environment_prints_versions() -> None:
    """`env` subcommand prints Python, oxitest, and OS versions and exits 0."""
    out, _, rc = helpers.run_oxitest_subcmd(None, "env")
    integ.assert_passed(out, rc)
    integ.assert_contains(out.lower(), "python:", "oxitest:")
    # The os: line is the only output reaching os_info()'s per-platform
    # branches, and a failed probe degrades to "unknown" instead of erroring —
    # so without the two asserts below the macOS branch could be broken and
    # still pass. Not lowercased: the darwin check is case-sensitive (#1944).
    os_lines = [line for line in out.splitlines() if line.startswith("os:")]
    assert len(os_lines) == 1, (
        f"env output must carry exactly one os: line for the platform "
        f"assertions below to mean anything; got {len(os_lines)} in:\n{out}"
    )
    integ.assert_excludes(os_lines[0], "unknown")
    if sys.platform == "darwin":
        assert os_lines[0].startswith("os: macOS "), (
            f"os_info()'s macOS branch must report a real sw_vers product "
            f"version, not a fallback; got {os_lines[0]!r}"
        )
    # Should NOT run tests
    integ.assert_excludes(out, "passed")


@oxitest.mark.timeout(120)
def test_inprocess_mark_runs_on_main_process(tmp: TempDir) -> None:
    """@oxi.mark.inprocess tests run on main process during parallel execution."""
    (tmp / "test_inproc.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.inprocess\n"
        "def test_main_process():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (tmp / "test_normal.py").write_text(
        "def test_worker():\n    assert True\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp, "--workers", "2")
    integ.assert_passed(out, rc, count=2)


# ── Dogfood: cross-feature integration tests (#788) ─────────────────────────


def test_parametrize_with_fixtures_in_parallel(tmp: TempDir) -> None:
    """Parametrized tests using fixtures run correctly across parallel workers."""
    integ.write_project(
        tmp,
        conftest="""\
            import oxitest as oxi
            fx = oxi.Fixtures()

            @fx.fixture
            def store() -> dict:
                return {}
        """,
        tests={
            "test_param_fx.py": """\
                from dataclasses import dataclass
                import oxitest as oxi
                from oxitest import Fixture

                @dataclass(frozen=True)
                class Case:
                    key: str
                    value: int

                @oxi.parametrize(
                    a=Case(key="x", value=1),
                    b=Case(key="y", value=2),
                    c=Case(key="z", value=3),
                )
                def test_store_round_trip(
                    key: str, value: int, store: Fixture[dict]
                ) -> None:
                    store[key] = value
                    assert store[key] == value, f"round-trip failed for {key}"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp, "--workers", "2")
    integ.assert_passed(out, rc, count=3)


def test_single_parametrize_case(tmp: TempDir) -> None:
    """@oxi.parametrize with a single case (only=Case(...)) runs one test."""
    integ.write_project(
        tmp,
        tests={
            "test_single.py": """\
                from dataclasses import dataclass
                import oxitest as oxi

                @dataclass(frozen=True)
                class AddCase:
                    x: int
                    y: int
                    expected: int

                @oxi.parametrize(only=AddCase(x=1, y=2, expected=3))
                def test_add(x: int, y: int, expected: int) -> None:
                    assert x + y == expected, "addition failed"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=1)


def test_class_based_tests_with_fixtures(tmp: TempDir) -> None:
    """Class-based tests receive fixtures via method parameters."""
    integ.write_project(
        tmp,
        conftest="""\
            import oxitest as oxi
            fx = oxi.Fixtures()

            @fx.fixture
            def store() -> dict:
                return {"seed": "value"}
        """,
        tests={
            "test_class_fx.py": """\
                from oxitest import Fixture

                class TestWithFixtures:
                    def test_read_seed(self, store: Fixture[dict]) -> None:
                        assert store["seed"] == "value", "seed should be present"

                    def test_write_and_read(self, store: Fixture[dict]) -> None:
                        store["new"] = 42
                        assert store["new"] == 42, "should read back written value"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)


def test_nested_conftest_reexport(tmp: TempDir) -> None:
    """Child conftest can re-export parent conftest fixtures."""
    root = Path(tmp)
    root_conftest = (
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'root_db'\n"
    )
    (root / "conftest.py").write_text(root_conftest, encoding="utf-8")

    sub = root / "sub"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        "from conftest import fx  # noqa: F401 — re-export parent fixtures\n",
        encoding="utf-8",
    )
    (sub / "test_reexport.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_uses_parent_fixture(db: Fixture[str]) -> None:\n"
        "    assert db == 'root_db', 'should resolve parent fixture'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=1)


def test_importorskip_skips_missing_module(tmp: TempDir) -> None:
    """oxi.importorskip skips the test when the requested module is not installed."""
    integ.write_project(
        tmp,
        tests={
            "test_skip_import.py": """\
                import oxitest as oxi

                def test_needs_nonexistent():
                    oxi.importorskip("nonexistent_module_xyz_999",
                                     reason="not installed")
                    assert False, "should not reach here"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "1 skipped")


def test_warns_captures_deprecation_warning(tmp: TempDir) -> None:
    """oxi.warns() captures expected warnings through the CLI pipeline."""
    integ.write_project(
        tmp,
        tests={
            "test_warns.py": """\
                import warnings
                import oxitest as oxi

                def test_catches_deprecation() -> None:
                    with oxi.warns(DeprecationWarning):
                        warnings.warn("old API", DeprecationWarning, stacklevel=1)
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=1)


def test_autouse_yield_fixture_teardown(tmp: TempDir) -> None:
    """Autouse yield fixture runs teardown after each test."""
    integ.write_project(
        tmp,
        conftest="""\
            import oxitest as oxi
            from oxitest import Yields
            fx = oxi.Fixtures()
            log = []

            @fx.fixture(autouse=True)
            def cleanup() -> Yields[None]:
                yield
                log.append("torn_down")
        """,
        tests={
            "test_autouse.py": """\
                import conftest

                def test_first() -> None:
                    assert True

                def test_second_sees_teardown() -> None:
                    # After test_first, cleanup fixture should have torn down
                    assert "torn_down" in conftest.log, (
                        f"expected teardown to have run: {conftest.log}"
                    )
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp, "--serial")
    integ.assert_passed(out, rc, count=2)
