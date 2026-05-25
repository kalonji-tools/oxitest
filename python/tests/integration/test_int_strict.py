"""Integration tests: strict mode (abort and enforce)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_strict_abort_exits_3(tmp: TempDir):
    """Bare assert with strict = abort exits with code 3 before running tests."""
    (tmp / "test_bare.py").write_text("def test_bare(): assert True\n")
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, rc = run_oxitest(tmp)
    assert rc == 3, f"strict abort with bare assert should exit 3, got {rc}"


def test_strict_abort_no_violations_exits_0(tmp: TempDir):
    """Test with message on assert and strict = abort exits 0."""
    (tmp / "test_clean.py").write_text('def test_clean(): assert True, "should pass"\n')
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, rc = run_oxitest(tmp)
    assert rc == 0, f"strict abort with no violations should exit 0, got {rc}"
    assert "passed" in out, "clean strict abort run should report passed"


def test_strict_enforce_reports_violations(tmp: TempDir):
    """strict = enforce runs all tests but exits 1 when violations are found."""
    (tmp / "test_mixed.py").write_text(
        'def test_bare(): assert True\ndef test_clean(): assert True, "has message"\n'
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "enforce"\n')
    out, rc = run_oxitest(tmp)
    assert rc == 1, f"strict enforce with violations should exit 1, got {rc}"


def test_no_strict_bare_assert_passes(tmp: TempDir):
    """Without strict config, bare asserts do not cause failures."""
    (tmp / "test_bare_ok.py").write_text("def test_bare_ok(): assert True\n")
    out, rc = run_oxitest(tmp)
    assert rc == 0, f"bare assert without strict config should exit 0, got {rc}"
    assert "passed" in out, "no-strict run should report passed"
