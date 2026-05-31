"""Integration tests: --affected flag with parallel execution."""

from conftest import helpers
from oxitest import TempDir


def test_affected_parallel_runs_subcommands_correctly(tmp: TempDir):
    """Nested oxitest subprocesses work in parallel workers.

    Regression test for #642: workers used PYO3_PYTHON (build-time Python)
    instead of sys.executable (runtime Python), causing nested subprocesses
    to load a stale extension or wrong Python version.
    """
    (tmp / "test_nested.py").write_text(
        "import subprocess, sys\n"
        "\n"
        "def test_nested_list():\n"
        "    result = subprocess.run(\n"
        "        [sys.executable, '-m', 'oxitest', 'list', '.', '--color', 'never'],\n"
        "        capture_output=True, text=True, timeout=30,\n"
        "    )\n"
        "    assert result.returncode == 0, (\n"
        "        f'nested oxitest list failed with rc={result.returncode}\\n'\n"
        "        f'stderr: {result.stderr}'\n"
        "    )\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)
