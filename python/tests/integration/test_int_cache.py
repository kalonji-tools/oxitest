"""Integration tests: cache behavior with --failed flag."""

from pathlib import Path

from conftest import helpers
from oxitest import TempDir


def test_cache_created_on_firstrun_oxitest(tmp: TempDir):
    """A .oxitest_cache directory is created after the first run."""
    (tmp / "test_cached.py").write_text("def test_a(): assert True\n")
    helpers.common.run_oxitest(tmp)
    cache_dir = Path(tmp) / ".oxitest_cache"
    assert cache_dir.exists(), ".oxitest_cache/ should be created after first run"


def test_failed_only_runs_subset(tmp: TempDir):
    """--failed=only reruns only the previously-failed test after fixing it."""
    (tmp / "test_mixed.py").write_text(
        "def test_pass(): assert True\ndef test_fail(): assert False\n"
    )
    # First run: populate the cache with one failure.
    helpers.common.run_oxitest(tmp)

    # Fix the failing test.
    (tmp / "test_mixed.py").write_text(
        "def test_pass(): assert True\ndef test_fail(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--failed=only")
    helpers.integ.assert_passed(out, rc, count=1)


def test_failed_first_runs_all(tmp: TempDir):
    """--failed=first runs all tests (failed ones first) and exits 0 when clean."""
    (tmp / "test_ff.py").write_text(
        "def test_ok(): assert True\ndef test_bad(): assert False\n"
    )
    # First run: populate the cache with one failure.
    helpers.common.run_oxitest(tmp)

    # Fix the failing test.
    (tmp / "test_ff.py").write_text(
        "def test_ok(): assert True\ndef test_bad(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--failed=first")
    helpers.integ.assert_passed(out, rc, count=2)


def test_failed_only_parallel(tmp: TempDir):
    """--failed=only with --workers 2 reruns only previously-failed tests."""
    # Three test files ensure work is distributed across workers.
    (tmp / "test_a.py").write_text(
        "def test_a1(): assert True\ndef test_a2(): assert True\n"
    )
    (tmp / "test_b.py").write_text(
        "def test_b1(): assert True\ndef test_b2(): assert False\n"
    )
    (tmp / "test_c.py").write_text(
        "def test_c1(): assert True\ndef test_c2(): assert True\n"
    )

    # First run: populate cache with one failure.
    out, _, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    helpers.integ.assert_failed(out, rc, count=1)

    # Fix the failing test.
    (tmp / "test_b.py").write_text(
        "def test_b1(): assert True\ndef test_b2(): assert True\n"
    )

    # Re-run with --failed=only in parallel.
    out, _, rc = helpers.common.run_oxitest(tmp, "--failed=only", "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=1)


def test_failed_first_parallel(tmp: TempDir):
    """--failed=first with --workers 2 runs all tests, failed ones first."""
    (tmp / "test_a.py").write_text(
        "def test_a1(): assert True\ndef test_a2(): assert True\n"
    )
    (tmp / "test_b.py").write_text(
        "def test_b1(): assert True\ndef test_b2(): assert False\n"
    )
    (tmp / "test_c.py").write_text(
        "def test_c1(): assert True\ndef test_c2(): assert True\n"
    )

    # First run: populate cache with one failure.
    out, _, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    helpers.integ.assert_failed(out, rc, count=1)

    # Fix the failing test.
    (tmp / "test_b.py").write_text(
        "def test_b1(): assert True\ndef test_b2(): assert True\n"
    )

    # Re-run with --failed=first in parallel — all 6 tests should run and pass.
    out, _, rc = helpers.common.run_oxitest(tmp, "--failed=first", "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=6)
