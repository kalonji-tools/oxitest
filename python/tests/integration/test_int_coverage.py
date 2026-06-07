"""Integration tests: --cov flag and coverage collection."""

from __future__ import annotations

from pathlib import Path

from conftest import helpers
from oxitest import TempDir

# Coverage artifacts land in the process CWD (project root), not in the
# tmp directory passed as a positional argument.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _cleanup_coverage() -> None:
    """Remove coverage artifacts from the project root."""
    for f in _PROJECT_ROOT.glob(".coverage*"):
        f.unlink(missing_ok=True)
    htmlcov = _PROJECT_ROOT / "htmlcov"
    if htmlcov.is_dir():
        import shutil

        shutil.rmtree(htmlcov)


def test_cov_serial_collects_coverage(tmp: TempDir) -> None:
    """--cov --serial collects coverage and prints a term summary."""
    _cleanup_coverage()
    (tmp / "test_ok.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--cov", "--serial")
    helpers.integ.assert_passed(out, rc, count=1)
    # Coverage summary table should be present
    assert "Stmts" in out, f"expected coverage table header in:\n{out}"
    assert "Cover" in out, f"expected 'Cover' column in:\n{out}"


def test_cov_parallel_collects_coverage(tmp: TempDir) -> None:
    """--cov with parallel workers collects coverage from all workers."""
    _cleanup_coverage()
    (tmp / "test_a.py").write_text("def test_a(): assert 1 + 1 == 2\n")
    (tmp / "test_b.py").write_text("def test_b(): assert 2 + 2 == 4\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--cov", "--workers", "2")
    helpers.integ.assert_passed(out, rc, count=2)
    assert "Stmts" in out, f"expected coverage table header in:\n{out}"
    assert "Cover" in out, f"expected 'Cover' column in:\n{out}"


def test_cov_report_html_generates_directory(tmp: TempDir) -> None:
    """--cov --cov-report html generates htmlcov/ directory."""
    _cleanup_coverage()
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, _, rc = helpers.common.run_oxitest(
        tmp, "--cov", "--cov-report", "html", "--serial"
    )
    helpers.integ.assert_passed(out, rc, count=1)
    htmlcov = _PROJECT_ROOT / "htmlcov"
    assert htmlcov.is_dir(), f"expected htmlcov/ directory at {htmlcov}"
    assert (htmlcov / "index.html").is_file(), "expected htmlcov/index.html"
    _cleanup_coverage()


def test_cov_report_none_suppresses_terminal(tmp: TempDir) -> None:
    """--cov --cov-report none suppresses coverage table in stdout."""
    _cleanup_coverage()
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, _, rc = helpers.common.run_oxitest(
        tmp, "--cov", "--cov-report", "none", "--serial"
    )
    helpers.integ.assert_passed(out, rc, count=1)
    # With report=none the terminal table should not appear
    assert "Stmts" not in out, (
        f"expected no coverage table with --cov-report none:\n{out}"
    )
    _cleanup_coverage()


def test_cov_report_without_cov_fails(tmp: TempDir) -> None:
    """--cov-report without --cov exits with usage error."""
    (tmp / "test_ok.py").write_text("def test_ok(): pass\n")
    out, err, rc = helpers.common.run_oxitest(tmp, "--cov-report", "html")
    assert rc != 0, f"expected non-zero exit for --cov-report without --cov, got {rc}"
    combined = out + err
    assert "requires" in combined.lower() or "cov" in combined.lower(), (
        f"expected error about --cov requirement: {combined!r}"
    )
