"""Integration tests: error handling for bad inputs."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_import_error_exits_collect_error(tmp: TempDir) -> None:
    """A test file that cannot be imported exits 3, which is CollectError."""
    (tmp / "test_bad_import.py").write_text(
        "import nonexistent_module_xyz\n\ndef test_x(): assert True\n", encoding="utf-8"
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_collection_error(out, rc)


def test_syntax_error_exits_collect_error(tmp: TempDir) -> None:
    """A test file with a syntax error exits 3, which is CollectError."""
    (tmp / "test_syntax.py").write_text("def test_x(\n", encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_collection_error(out, rc)


def test_an_unrecognised_flag_exits_usage_error(tmp: TempDir) -> None:
    """An unrecognised flag exits 4, which is UsageError.

    ``-m`` is not a flag of this CLI, so the run stops in argument parsing
    before any expression is read. #887 removed ``-m`` from the documentation.
    A valid and an invalid expression give the same result here, which is why
    this test asserts the flag is refused rather than the expression.

    The message names the positional path rather than the flag. Dropping the
    positional changes it to ``unexpected argument '-m' found``, so the text
    asserted here belongs to this invocation and not to ``-m`` alone.
    """
    (tmp / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    _, stderr, rc = helpers.run_oxitest(tmp, "-m", "not and or")
    integ.assert_usage_error(stderr, rc)
    integ.assert_contains(stderr, "unrecognized subcommand")


def test_an_invalid_filter_expression_exits_collect_error(tmp: TempDir) -> None:
    """An -E expression that does not parse exits 3, which is CollectError.

    ``-E`` is the filter flag this CLI has. The expression is parsed during
    collection, so a parse failure is a collection error rather than a usage
    error.
    """
    (tmp / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp, "-E", "not and or")
    integ.assert_collection_error(out, rc)
    integ.assert_contains(out, "parse error")


def test_no_test_files_found(tmp: TempDir) -> None:
    """An empty directory with no test files exits 0."""
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)


def test_invalid_python_files_glob_exits_with_usage_error(tmp: TempDir) -> None:
    """An invalid glob pattern in python_files exits with UsageError (code 4)."""
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\npython_files = ["["]\n', encoding="utf-8"
    )
    (tmp / "test_foo.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    _, stderr, rc = helpers.run_oxitest(tmp)
    assert rc == 4, f"invalid glob should exit with UsageError (4), got {rc}"
    integ.assert_contains(stderr, "invalid glob pattern")
