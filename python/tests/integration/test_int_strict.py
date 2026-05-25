"""Integration tests: strict mode (abort and enforce)."""

import subprocess
import sys
from pathlib import Path

from oxitest import TempDir


def _run(tmp: TempDir, *extra: str) -> tuple[str, int]:
    """Run oxitest against a temp dir, return (stdout, exit_code)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never", *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.returncode


def test_strict_abort_exits_3(tmp: TempDir):
    """Bare assert with strict = abort exits with code 3 before running tests."""
    (tmp / "test_bare.py").write_text("def test_bare(): assert True\n")
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, rc = _run(tmp)
    assert rc == 3, f"strict abort with bare assert should exit 3, got {rc}"


def test_strict_abort_no_violations_exits_0(tmp: TempDir):
    """Test with message on assert and strict = abort exits 0."""
    (tmp / "test_clean.py").write_text('def test_clean(): assert True, "should pass"\n')
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, rc = _run(tmp)
    assert rc == 0, f"strict abort with no violations should exit 0, got {rc}"
    assert "passed" in out, "clean strict abort run should report passed"


def test_strict_enforce_reports_violations(tmp: TempDir):
    """strict = enforce runs all tests but exits 1 when violations are found."""
    (tmp / "test_mixed.py").write_text(
        'def test_bare(): assert True\ndef test_clean(): assert True, "has message"\n'
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "enforce"\n')
    out, rc = _run(tmp)
    assert rc == 1, f"strict enforce with violations should exit 1, got {rc}"


def test_no_strict_bare_assert_passes(tmp: TempDir):
    """Without strict config, bare asserts do not cause failures."""
    (tmp / "test_bare_ok.py").write_text("def test_bare_ok(): assert True\n")
    out, rc = _run(tmp)
    assert rc == 0, f"bare assert without strict config should exit 0, got {rc}"
    assert "passed" in out, "no-strict run should report passed"
