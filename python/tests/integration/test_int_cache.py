"""Integration tests: cache behavior with --failed flag."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_cache_created_on_firstrun_oxitest(tmp: TempDir):
    """A .oxitest_cache directory is created after the first run."""
    (tmp / "test_cached.py").write_text("def test_a(): assert True\n")
    run_oxitest(tmp)
    cache_dir = Path(tmp) / ".oxitest_cache"
    assert cache_dir.exists(), ".oxitest_cache/ should be created after first run"


def test_failed_only_runs_subset(tmp: TempDir):
    """--failed=only reruns only the previously-failed test after fixing it."""
    (tmp / "test_mixed.py").write_text(
        "def test_pass(): assert True\ndef test_fail(): assert False\n"
    )
    # First run: populate the cache with one failure.
    run_oxitest(tmp)

    # Fix the failing test.
    (tmp / "test_mixed.py").write_text(
        "def test_pass(): assert True\ndef test_fail(): assert True\n"
    )
    out, rc = run_oxitest(tmp, "--failed=only")
    assert rc == 0, f"--failed=only after fix should exit 0, got {rc}"
    assert "1 passed" in out, f"--failed=only should run exactly 1 test, got: {out!r}"


def test_failed_first_runs_all(tmp: TempDir):
    """--failed=first runs all tests (failed ones first) and exits 0 when clean."""
    (tmp / "test_ff.py").write_text(
        "def test_ok(): assert True\ndef test_bad(): assert False\n"
    )
    # First run: populate the cache with one failure.
    run_oxitest(tmp)

    # Fix the failing test.
    (tmp / "test_ff.py").write_text(
        "def test_ok(): assert True\ndef test_bad(): assert True\n"
    )
    out, rc = run_oxitest(tmp, "--failed=first")
    assert rc == 0, f"--failed=first after fix should exit 0, got {rc}"
    assert "2 passed" in out, f"--failed=first should run both tests, got: {out!r}"
