"""Integration tests: error handling for bad inputs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_import_error_exits_nonzero(tmp: TempDir):
    """A test file with an import error causes a non-zero exit code."""
    (tmp / "test_bad_import.py").write_text(
        "import nonexistent_module_xyz\n\ndef test_x(): assert True\n"
    )
    _, rc = run_oxitest(tmp)
    assert rc != 0, f"import error should exit non-zero, got {rc}"


def test_syntax_error_exits_nonzero(tmp: TempDir):
    """A test file with a syntax error causes a non-zero exit code."""
    (tmp / "test_syntax.py").write_text("def test_x(\n")
    _, rc = run_oxitest(tmp)
    assert rc != 0, f"syntax error should exit non-zero, got {rc}"


def test_invalid_marker_expression_exits_nonzero(tmp: TempDir):
    """An invalid -m expression causes a non-zero exit code."""
    (tmp / "test_ok.py").write_text("def test_ok(): assert True\n")
    _, rc = run_oxitest(tmp, "-m", "not and or")
    assert rc != 0, f"invalid marker expression should exit non-zero, got {rc}"


def test_no_test_files_found(tmp: TempDir):
    """An empty directory with no test files exits 0."""
    out, rc = run_oxitest(tmp)
    assert rc == 0, f"no test files should exit 0, got {rc}"
