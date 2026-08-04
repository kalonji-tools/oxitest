"""Integration tests: --cov flag and coverage collection."""

from __future__ import annotations

import oxitest
from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def _has_coverage() -> bool:
    try:
        __import__("coverage")
    except ImportError:
        return False
    return True


oxi_mark = oxitest.mark.skip(
    when=not _has_coverage(), reason="coverage package not installed"
)

# Every run below sets cwd to its own TempDir (#1648): coverage.py resolves
# `.coverage` and `htmlcov/` relative to the process CWD, so no two runs can
# share -- or wipe -- a path, and the artifacts die with the TempDir.


def test_cov_serial_collects_coverage(tmp: TempDir) -> None:
    """--cov --serial collects coverage and prints a term summary."""
    (tmp / "test_ok.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    out, _, rc = helpers.run_oxitest(tmp, "--cov", "--serial", cwd=str(tmp))
    integ.assert_passed(out, rc, count=1)
    # Coverage summary table should be present
    assert "Stmts" in out, f"expected coverage table header in:\n{out}"
    assert "Cover" in out, f"expected 'Cover' column in:\n{out}"


def test_cov_parallel_collects_coverage(tmp: TempDir) -> None:
    """--cov with parallel workers collects coverage from all workers."""
    (tmp / "test_a.py").write_text("def test_a(): assert 1 + 1 == 2\n")
    (tmp / "test_b.py").write_text("def test_b(): assert 2 + 2 == 4\n")
    out, _, rc = helpers.run_oxitest(tmp, "--cov", "--workers", "2", cwd=str(tmp))
    integ.assert_passed(out, rc, count=2)
    assert "Stmts" in out, f"expected coverage table header in:\n{out}"
    assert "Cover" in out, f"expected 'Cover' column in:\n{out}"
    # The header alone survives a report that measured the wrong tree: with the
    # project root as cwd, coverage.py picks up `source = ["python/oxitest"]`
    # and the two files above are filtered out while the header still prints.
    # Their rows are what pins the report to this run's sandbox. They do NOT
    # discriminate parallel from serial -- a `--serial` mutant keeps them,
    # because the parent then executes the files itself. Nothing in this test
    # discriminates the two; the docstring's "from all workers" is still
    # unasserted.
    integ.assert_contains(out, "test_a.py", "test_b.py")


def test_cov_report_html_generates_directory(tmp: TempDir) -> None:
    """--cov --cov-report html generates htmlcov/ directory."""
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, _, rc = helpers.run_oxitest(
        tmp, "--cov", "--cov-report", "html", "--serial", cwd=str(tmp)
    )
    integ.assert_passed(out, rc, count=1)
    htmlcov = tmp / "htmlcov"
    assert htmlcov.is_dir(), f"expected htmlcov/ directory at {htmlcov}"
    assert (htmlcov / "index.html").is_file(), "expected htmlcov/index.html"


def test_cov_report_none_suppresses_terminal(tmp: TempDir) -> None:
    """--cov --cov-report none suppresses coverage table in stdout."""
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, _, rc = helpers.run_oxitest(
        tmp, "--cov", "--cov-report", "none", "--serial", cwd=str(tmp)
    )
    integ.assert_passed(out, rc, count=1)
    # With report=none the terminal table should not appear
    assert "Stmts" not in out, (
        f"expected no coverage table with --cov-report none:\n{out}"
    )


def test_cov_report_without_cov_fails(tmp: TempDir) -> None:
    """--cov-report without --cov exits with usage error."""
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, err, rc = helpers.run_oxitest(tmp, "--cov-report", "html", cwd=str(tmp))
    assert rc != 0, f"expected non-zero exit for --cov-report without --cov, got {rc}"
    combined = out + err
    assert "requires" in combined.lower() or "cov" in combined.lower(), (
        f"expected error about --cov requirement: {combined!r}"
    )
