"""Integration tests: error handling for bad inputs."""

from conftest import helpers
from oxitest import TempDir


def test_import_error_exits_nonzero(tmp: TempDir):
    """A test file with an import error causes a non-zero exit code."""
    (tmp / "test_bad_import.py").write_text(
        "import nonexistent_module_xyz\n\ndef test_x(): assert True\n"
    )
    _, _, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, f"import error should exit non-zero, got {rc}"


def test_syntax_error_exits_nonzero(tmp: TempDir):
    """A test file with a syntax error causes a non-zero exit code."""
    (tmp / "test_syntax.py").write_text("def test_x(\n")
    _, _, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, f"syntax error should exit non-zero, got {rc}"


def test_invalid_marker_expression_exits_nonzero(tmp: TempDir):
    """An invalid -m expression causes a non-zero exit code."""
    (tmp / "test_ok.py").write_text("def test_ok(): assert True\n")
    _, _, rc = helpers.common.run_oxitest(tmp, "-m", "not and or")
    assert rc != 0, f"invalid marker expression should exit non-zero, got {rc}"


def test_no_test_files_found(tmp: TempDir):
    """An empty directory with no test files exits 0."""
    out, _, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"no test files should exit 0, got {rc}"


def test_invalid_python_files_glob_exits_with_usage_error(tmp: TempDir):
    """An invalid glob pattern in python_files exits with UsageError (code 4)."""
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\npython_files = ["["]\n')
    (tmp / "test_foo.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 4, f"invalid glob should exit with UsageError (4), got {rc}"
    assert "invalid glob pattern" in stderr, (
        f"expected error message in stderr, got: {stderr}"
    )
