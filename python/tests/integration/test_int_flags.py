"""Integration tests: flag interactions (--list, -k, --serial, --json, etc.)."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import run_oxitest, run_oxitest_full
from oxitest import TempDir


def test_list_prints_node_ids_and_exits_zero(tmp: TempDir):
    """--list prints node IDs and exits 0 without running tests."""
    (tmp / "test_nodes.py").write_text(
        "def test_alpha(): assert True\n"
        "def test_beta(): assert True\n"
        "def test_gamma(): assert True\n"
    )
    out, rc = run_oxitest(tmp, "--list")
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
    out, rc = run_oxitest(tmp, "--list", "-v")
    assert rc == 0, f"--list -v should exit 0, got {rc}"
    assert "module" in out, "verbose list should show 'module' column header"
    assert "function" in out, "verbose list should show 'function' column header"


def test_keyword_filter(tmp: TempDir):
    """Only tests matching the -k keyword should run."""
    (tmp / "test_kw.py").write_text(
        "def test_alpha(): assert True\ndef test_beta(): assert False\n"
    )
    out, rc = run_oxitest(tmp, "-k", "alpha")
    assert rc == 0, f"-k alpha should exit 0 (only matching test runs), got {rc}"
    assert "1 passed" in out, "-k alpha should run exactly 1 test"


def test_serial_flag(tmp: TempDir):
    """--serial flag runs tests without parallel workers."""
    (tmp / "test_serial.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\n"
    )
    out, rc = run_oxitest(tmp, "--serial")
    assert rc == 0, f"--serial should exit 0, got {rc}"
    assert "passed" in out, "--serial run should report passed tests"


def test_json_output(tmp: TempDir):
    """--json writes a valid CTRF JSON file with summary.passed >= 2."""
    (tmp / "test_js.py").write_text(
        "def test_one(): assert True\ndef test_two(): assert True\n"
    )
    json_path = Path(tmp) / "results.json"
    out, rc = run_oxitest(tmp, "--json", str(json_path))
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
    out, rc = run_oxitest(tmp, "--junit-xml", str(xml_path))
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
    out, rc = run_oxitest(tmp, "-m", "slow")
    assert rc == 0, f"-m slow should exit 0, got {rc}"
    assert "1 passed" in out, "-m slow should run exactly 1 test"


def test_exitfirst_conflicts_with_maxfail(tmp: TempDir):
    """Flag conflict: -x and --maxfail are mutually exclusive."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = run_oxitest_full(tmp, "-x", "--maxfail", "5")
    assert rc == 4, f"-x/--maxfail conflict should exit 4, got {rc}"
    assert "-x" in stderr, f"stderr: {stderr!r}"
    assert "--maxfail" in stderr, f"stderr: {stderr!r}"


def test_verbose_conflicts_with_quiet(tmp: TempDir):
    """Flag conflict: --verbose and --quiet are opposite modes."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = run_oxitest_full(tmp, "-v", "-q")
    assert rc == 4, f"-v/-q conflict should exit 4, got {rc}"
    assert "--verbose" in stderr, f"stderr: {stderr!r}"
    assert "--quiet" in stderr, f"stderr: {stderr!r}"


def test_schedule_conflicts_with_serial(tmp: TempDir):
    """Flag conflict: --schedule has no effect in serial mode."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = run_oxitest_full(tmp, "--schedule", "random", "--serial")
    assert rc == 4, f"--schedule/--serial conflict should exit 4, got {rc}"
    assert "--schedule" in stderr, f"stderr: {stderr!r}"
    assert "--serial" in stderr, f"stderr: {stderr!r}"


def test_retries_delay_requires_retries(tmp: TempDir):
    """Flag conflict: --retries-delay needs --retries."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = run_oxitest_full(tmp, "--retries-delay", "5")
    assert rc == 4, f"--retries-delay without --retries should exit 4, got {rc}"
    assert "--retries-delay" in stderr, f"stderr: {stderr!r}"
    assert "--retries" in stderr, f"stderr: {stderr!r}"


def test_debug_drops_into_pdb_and_quit_exits_2(tmp: TempDir):
    """--debug on a failing test + 'q' input produces exit code 2 with banner."""
    (tmp / "test_fail.py").write_text(
        "def test_bad():\n    assert 1 == 2, 'one is not two'\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
            "--color",
            "never",
            "--debug",
        ],
        input="q\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, (
        f"expected exit code 2 (interrupted), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "DEBUG" in result.stderr, f"banner missing from stderr: {result.stderr!r}"
    assert "test_bad" in result.stderr, (
        f"node_id missing from stderr: {result.stderr!r}"
    )


def test_debug_conflicts_are_rejected(tmp: TempDir):
    """--debug with --workers should produce exit code 4."""
    (tmp / "test_a.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = run_oxitest_full(tmp, "--debug", "--workers", "2")
    assert rc == 4, f"expected exit code 4, got {rc}\nstderr: {stderr!r}"
    assert "--debug" in stderr, f"error should mention --debug: {stderr!r}"
    assert "--workers" in stderr, f"error should mention --workers: {stderr!r}"
