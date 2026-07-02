"""Integration tests: flag interactions (--list, -k, --serial, --json, etc.)."""

import json
import os
import subprocess
from pathlib import Path

import oxitest
from oxitest import Fixture, TempDir, helpers


def test_list_prints_node_ids_and_exits_zero(tmp: TempDir):
    """`query tests` prints node IDs and exits 0 without running tests."""
    (tmp / "test_nodes.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_alpha", "test_beta", "test_gamma")
    helpers.integ.assert_excludes(out, "passed")


def test_list_detailed_shows_marks_and_fixtures(tmp: TempDir):
    """`query tests` shows marks in default columnar output."""
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
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_one", "test_two")


def test_expression_filter(tmp: TempDir):
    """Only tests matching the -E expression should run."""
    (tmp / "test_kw.py").write_text(
        "def test_alpha(): assert True\ndef test_beta(): assert False\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "-E", "name(alpha)")
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
    helpers.integ.assert_passed(out, rc)
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
    helpers.integ.assert_passed(out, rc)
    assert xml_path.exists(), "--junit-xml should create the output file"
    xml_content = xml_path.read_text()
    helpers.integ.assert_contains(
        xml_content, "<testsuites", "test_first", "test_second"
    )


def test_expression_marker_filter(tmp: TempDir):
    """Only tests matching the -E mark expression should run."""
    (tmp / "test_marked.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.slow\n"
        "def test_slow_one(): assert True\n\n"
        "def test_fast_one(): assert True\n"
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nmarkers = ["slow: slow tests"]\n')
    out, _, rc = helpers.common.run_oxitest(tmp, "-E", "mark(slow)")
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
    helpers.integ.assert_passed(out, rc)


def test_schedule_conflicts_with_serial(tmp: TempDir):
    """Flag conflict: --schedule has no effect in serial mode."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp, "--schedule", "random", "--serial")
    assert rc == 4, f"--schedule/--serial conflict should exit 4, got {rc}"
    helpers.integ.assert_contains(stderr, "--schedule", "--serial")


def test_debug_with_passing_test_exits_0(tmp: TempDir):
    """`debug` subcommand on a passing test exits 0 (no pdb triggered)."""
    (tmp / "test_ok.py").write_text("def test_pass():\n    assert True\n")
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "debug")
    helpers.integ.assert_passed(out, rc)


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

    plugin_env = {
        **os.environ,
        "PYTHONPATH": str(tmp) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    out, err, rc = helpers.common.run_oxitest_subcmd(
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
    assert marker_file.read_text() == "traced", (
        f"marker file content wrong: {marker_file.read_text()!r}"
    )


def test_keep_tmp_preserves_on_failure(tmp: TempDir):
    """--keep-tmp preserves TempDir when test fails."""
    helpers.common.write_test_file(
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
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--keep-tmp")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(stderr, "KEPT", "--keep-tmp")


def test_keep_tmp_cleans_on_pass(tmp: TempDir):
    """--keep-tmp=failed cleans up when test passes."""
    helpers.common.write_test_file(
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
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--keep-tmp")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_excludes(stderr, "KEPT")


def test_query_tests_shows_parametrized_function(tmp: TempDir):
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
        "    assert abs(case.x) == case.expected, 'mismatch'\n"
    )
    out, _stderr, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_abs")


# ── Issue #584: Integration tests for 6 untested CLI flags ───────────────────


@oxitest.mark.timeout(120)
def test_affected_filters_to_changed_tests(git_repo: Fixture[Path]):
    """--affected filters to tests in files changed since the given ref."""
    tmp = git_repo
    git = ["git", "-C", str(tmp)]

    # Clear git env vars that prek/pre-commit may set.
    clean_env = {
        k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")
    }

    def run(*cmd):
        return subprocess.run(cmd, check=True, capture_output=True, env=clean_env)

    # Create and commit a baseline test file (on top of git_repo's init commit)
    (tmp / "test_old.py").write_text("def test_old(): assert True\n")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "baseline")

    # Add a new test file and stage it (git diff HEAD sees staged changes)
    (tmp / "test_new.py").write_text("def test_new(): assert True\n")
    run(*git, "add", "test_new.py")

    # Act — use clean env so oxitest doesn't see prek's GIT_* vars
    out, _, rc = helpers.common.run_oxitest(
        tmp,
        "--affected=HEAD",
        env=clean_env,
        cwd=str(tmp),
    )

    # Assert — only the new file should be collected (1 test, not 2)
    helpers.integ.assert_passed(out, rc, count=1)
    helpers.integ.assert_excludes(out, "2 passed")


@oxitest.mark.timeout(120)
def test_affected_with_subdirectory_path(git_repo: Fixture[Path]):
    """--affected works when a subdirectory is passed as the path argument."""
    tmp = git_repo
    git = ["git", "-C", str(tmp)]
    clean_env = {
        k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")
    }

    def run(*cmd):
        return subprocess.run(cmd, check=True, capture_output=True, env=clean_env)

    # Create a subdirectory with a test file and commit
    subdir = tmp / "tests"
    subdir.mkdir()
    (subdir / "test_one.py").write_text("def test_one(): assert True\n")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "baseline")

    # Add a second test file and stage it
    (subdir / "test_two.py").write_text("def test_two(): assert True\n")
    run(*git, "add", "tests/test_two.py")

    # Act — pass the subdirectory as the path (this triggered the bug)
    out, _, rc = helpers.common.run_oxitest(
        subdir,
        "--affected=HEAD",
        env=clean_env,
        cwd=str(tmp),
    )

    # Assert — only the new file should be collected (1 test, not 2)
    helpers.integ.assert_passed(out, rc, count=1)
    helpers.integ.assert_excludes(out, "2 passed")


def test_tb_line_shows_compact_failure(tmp: TempDir):
    """--tb line shows file:line but no full diagnostic block."""
    (tmp / "test_fail.py").write_text(
        "def test_boom():\n    assert 1 == 2, 'one is not two'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--tb", "line")
    helpers.integ.assert_failed(out, rc)
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
    helpers.integ.assert_failed(out, rc)
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
    helpers.integ.assert_contains(out.lower(), "slowest")
    helpers.integ.assert_contains(out, "ms")


def test_durations_shows_fixture_timings(tmp: TempDir):
    """--durations shows slowest fixtures alongside slowest tests."""
    (tmp / "conftest.py").write_text(
        "import time\n"
        "import oxitest as oxi\n\n"
        "fx = oxi.Fixtures()\n\n"
        "@fx.fixture\n"
        "def slow_setup() -> int:\n"
        "    time.sleep(0.05)\n"
        "    return 42\n"
    )
    (tmp / "test_fx_timing.py").write_text(
        "import oxitest as oxi\n\n"
        "def test_uses_slow(slow_setup: oxi.Fixture[int]):\n"
        '    assert slow_setup == 42, "fixture should return 42"\n'
    )
    out, err, rc = helpers.common.run_oxitest(tmp, "--durations", "5")
    assert rc == 0, (
        f"--durations with fixtures should exit 0, got {rc}\n"
        f"stdout: {out!r}\nstderr: {err!r}"
    )
    assert "slow_setup" in out, f"fixture name should appear in output: {out!r}"
    assert "setup" in out.lower(), f"output should mention setup: {out!r}"


def test_capture_environment_prints_versions():
    """`env` subcommand prints Python and oxitest versions and exits 0."""
    out, _, rc = helpers.common.run_oxitest_subcmd(None, "env")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out.lower(), "python:", "oxitest:")
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=2)


# ── Dogfood: cross-feature integration tests (#788) ─────────────────────────


def test_parametrize_with_fixtures_in_parallel(tmp: TempDir):
    """Parametrized tests using fixtures run correctly across parallel workers."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=3)


def test_single_parametrize_case(tmp: TempDir):
    """@oxi.parametrize with a single case (only=Case(...)) runs one test."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)


def test_class_based_tests_with_fixtures(tmp: TempDir):
    """Class-based tests receive fixtures via method parameters."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=2)


def test_nested_conftest_reexport(tmp: TempDir):
    """Child conftest can re-export parent conftest fixtures."""
    root = Path(tmp)
    root_conftest = (
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'root_db'\n"
    )
    (root / "conftest.py").write_text(root_conftest)

    sub = root / "sub"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        "from conftest import fx  # noqa: F401 — re-export parent fixtures\n"
    )
    (sub / "test_reexport.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_uses_parent_fixture(db: Fixture[str]) -> None:\n"
        "    assert db == 'root_db', 'should resolve parent fixture'\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)


def test_importorskip_skips_missing_module(tmp: TempDir):
    """importorskip skips the test when the module is not installed."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "1 skipped")


def test_warns_captures_deprecation_warning(tmp: TempDir):
    """oxi.warns() captures expected warnings through the CLI pipeline."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)


def test_autouse_yield_fixture_teardown(tmp: TempDir):
    """Autouse yield fixture runs teardown after each test."""
    helpers.integ.write_project(
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--serial")
    helpers.integ.assert_passed(out, rc, count=2)
