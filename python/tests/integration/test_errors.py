"""Integration tests: error handling for bad inputs."""

from oxitest import TempDir, helpers


def test_import_error_exits_nonzero(tmp: TempDir) -> None:
    """A test file with an import error causes a non-zero exit code."""
    (tmp / "test_bad_import.py").write_text(
        "import nonexistent_module_xyz\n\ndef test_x(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)


def test_syntax_error_exits_nonzero(tmp: TempDir) -> None:
    """A test file with a syntax error causes a non-zero exit code."""
    (tmp / "test_syntax.py").write_text("def test_x(\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)


def test_invalid_marker_expression_exits_nonzero(tmp: TempDir) -> None:
    """An invalid -m expression causes a non-zero exit code."""
    (tmp / "test_ok.py").write_text("def test_ok(): assert True\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "-m", "not and or")
    helpers.integ.assert_failed(out, rc)


def test_no_test_files_found(tmp: TempDir) -> None:
    """An empty directory with no test files exits 0."""
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)


def test_invalid_python_files_glob_exits_with_usage_error(tmp: TempDir) -> None:
    """An invalid glob pattern in python_files exits with UsageError (code 4)."""
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\npython_files = ["["]\n')
    (tmp / "test_foo.py").write_text("def test_ok(): pass\n")
    _, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 4, f"invalid glob should exit with UsageError (4), got {rc}"
    helpers.integ.assert_contains(stderr, "invalid glob pattern")
