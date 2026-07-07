"""Integration tests for wildcard expansion in positional node IDs (#736)."""

from __future__ import annotations

from oxitest import TempDir, helpers


def _write_project(tmp: TempDir) -> None:
    """Write a minimal test project with multiple test files and functions."""
    helpers.integ.write_project(
        tmp,
        pyproject="[tool.oxitest]\ntestpaths = ['.']\n",
        tests={
            "test_math.py": (
                "def test_add(): pass\ndef test_sub(): pass\ndef test_mul(): pass\n"
            ),
            "test_string.py": ("def test_upper(): pass\ndef test_lower(): pass\n"),
        },
    )


def test_glob_function_name(tmp: TempDir) -> None:
    """Glob in function name selects only matching tests."""
    _write_project(tmp)
    # test_a* matches test_add only (test_sub and test_mul don't start with test_a)
    out, _, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "run",
        "test_math.py::test_a*",
        cwd=".",
    )
    helpers.integ.assert_passed(out, rc, count=1)


def test_glob_star_selects_all_in_file(tmp: TempDir) -> None:
    """Glob `*` selects all tests in the targeted file, no others."""
    _write_project(tmp)
    out, _, rc = helpers.common.run_oxitest_subcmd(
        tmp, "run", "test_math.py::*", cwd="."
    )
    # test_math.py has 3 tests; test_string.py tests must not run
    helpers.integ.assert_passed(out, rc, count=3)


def test_glob_no_match_runs_nothing(tmp: TempDir) -> None:
    """Glob with no matches collects zero tests."""
    _write_project(tmp)
    out, *_ = helpers.common.run_oxitest_subcmd(
        tmp,
        "run",
        "test_math.py::test_zzz*",
        cwd=".",
    )
    helpers.integ.assert_contains(out, "no tests ran")


def test_non_glob_prefix_matching_preserved(tmp: TempDir) -> None:
    """Non-glob node IDs still use existing prefix matching."""
    _write_project(tmp)
    out, _, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "run",
        "test_math.py::test_add",
        cwd=".",
    )
    helpers.integ.assert_passed(out, rc, count=1)
