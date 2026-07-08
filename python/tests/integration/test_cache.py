"""Integration tests: cache behavior with --failed flag."""

from dataclasses import dataclass
from pathlib import Path

import oxitest
from oxitest import TempDir, helpers


def test_cache_created_on_firstrun_oxitest(tmp: TempDir) -> None:
    """A .oxitest_cache directory is created after the first run."""
    (tmp / "test_cached.py").write_text("def test_a(): assert True\n")
    helpers.common.run_oxitest(tmp)
    cache_dir = Path(tmp) / ".oxitest_cache"
    assert cache_dir.exists(), ".oxitest_cache/ should be created after first run"


def test_failed_only_runs_subset(tmp: TempDir) -> None:
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


def test_failed_first_runs_all(tmp: TempDir) -> None:
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


@dataclass(frozen=True)
class FailedStrategyCase:
    """Parametrize case for --failed strategy parallel tests."""

    flag: str
    expected_count: int


@oxitest.parametrize(
    failed_only=FailedStrategyCase("--failed=only", expected_count=1),
    failed_first=FailedStrategyCase("--failed=first", expected_count=6),
)
def test_failed_strategy_parallel(
    tmp: TempDir,
    flag: str,
    expected_count: int,
) -> None:
    """--failed=only/first with --workers 2 reruns correctly in parallel."""
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

    # Re-run with strategy in parallel.
    out, _, rc = helpers.common.run_oxitest(tmp, flag, "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=expected_count)
