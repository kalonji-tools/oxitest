"""The doctest audit reads the declared tree; item collection reads the run (#1798).

One `doctest_files` list used to answer both questions. `collect_doctest_files`
walks `testpaths`, which positional CLI paths overwrite, so `oxitest tests/`
silently stopped auditing every subject outside `tests/` — a green run that had
audited nothing, with no diagnostic saying so.

The first two tests here pull in opposite directions on purpose. Repointing
both lists at the declared tree fixes the first and breaks the second, by
making a narrowed run execute every doctest in the project; repointing neither
leaves the audit broken. Only splitting them by consumer satisfies both.

The third covers the branch the split originally missed: a project that
declares no `testpaths` at all, where `declared_testpaths` is empty and the
audit fell back to the argv-overwritten field — the same defect, one layer
down.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

#: The audit is gated on `strict` — `collect_coverage_diagnostics` returns
#: early when it is unset, so a project without it exercises nothing at all.
#: `abort` puts the verdict in the exit code rather than in reporter text.
_AUDITED_PROJECT = """\
    [tool.oxitest]
    testpaths = ["tests", "src"]
    python_files = ["test_*.py"]
    strict = "abort"

    [tool.oxitest.doctest]
    scope = "public"
"""

_RUNNABLE_PROJECT = """\
    [tool.oxitest]
    testpaths = ["tests", "src"]
    python_files = ["test_*.py"]

    [tool.oxitest.doctest]
    scope = "public"
"""

#: The zero-config shape: a doctest table and `strict`, but no `testpaths`.
#: `declared_testpaths` stays empty here, which is the branch that had no
#: coverage — every other project constant in this file declares testpaths.
_UNDECLARED_PROJECT = """\
    [tool.oxitest]
    python_files = ["test_*.py"]
    strict = "abort"

    [tool.oxitest.doctest]
    scope = "public"
"""

_TEST_FILE = (
    'def test_one():\n    assert True, "the narrowed run needs something to run"\n'
)


def test_a_narrowed_run_still_audits_the_whole_declared_tree(tmp: TempDir) -> None:
    """`oxitest tests/` must keep auditing `src/`, which it never mentioned."""
    # Arrange — `src` is declared so the audit reaches it, and holds no tests.
    integ.write_project(
        tmp,
        pyproject=_AUDITED_PROJECT,
        tests={},
        extra_files={
            "tests/test_one.py": _TEST_FILE,
            "src/audited.py": (
                '"""A declared subject whose public function has no examples."""\n'
                "\n\n"
                "def audited_fn(value):\n"
                '    """Return the value unchanged."""\n'
                "    return value\n"
            ),
        },
    )

    # Act
    full_out, full_err, full_rc = helpers.run_oxitest(None, cwd=str(tmp))
    narrow_out, narrow_err, narrow_rc = helpers.run_oxitest(None, "tests", cwd=str(tmp))
    full = full_out + full_err
    narrow = narrow_out + narrow_err

    # Assert
    assert full_rc == 3, (
        f"the control: an unexamined public subject under a declared testpath "
        f"must fail a full run; rc={full_rc}\n{full}"
    )
    assert narrow_rc == 3, (
        f"narrowing the run to 'tests' must not retire the audit of 'src' — "
        f"nothing about 'tests' says anything about the rest of the declared "
        f"tree; rc={narrow_rc}\n{narrow}"
    )
    for output, label in ((full, "full run"), (narrow, "narrowed run")):
        assert "audited.audited_fn" in output, (
            f"the {label} must name the subject it is failing on, or the exit "
            f"code is the only thing the user has to work from; got:\n{output}"
        )


def test_a_narrowed_run_does_not_execute_doctests_outside_it(tmp: TempDir) -> None:
    """The other half: narrowing must still narrow what runs.

    Pointing item collection at the declared tree would make
    `oxitest tests/test_one.py` execute every doctest in the project — a worse
    regression than the audit bug, and the reason the two lists are split by
    consumer rather than both repointed.
    """
    # Arrange — the subject satisfies the audit, so the run reaches execution.
    integ.write_project(
        tmp,
        pyproject=_RUNNABLE_PROJECT,
        tests={},
        extra_files={
            "tests/test_one.py": _TEST_FILE,
            "src/documented.py": (
                '"""A declared subject carrying a runnable doctest."""\n'
                "\n\n"
                "def documented_fn():\n"
                '    """Return a fixed value.\n'
                "\n"
                "    Examples:\n"
                "        >>> documented_fn()\n"
                "        'documented'\n"
                '    """\n'
                "    return 'documented'\n"
            ),
        },
    )

    # Act
    full_out, full_err, full_rc = helpers.run_oxitest(None, cwd=str(tmp))
    narrow_out, narrow_err, narrow_rc = helpers.run_oxitest(None, "tests", cwd=str(tmp))
    full = full_out + full_err
    narrow = narrow_out + narrow_err

    # Assert
    assert full_rc == 0 and narrow_rc == 0, (
        f"both runs must pass; full rc={full_rc}, narrowed rc={narrow_rc}\n"
        f"--- full ---\n{full}\n--- narrowed ---\n{narrow}"
    )
    assert "collected 2 items" in full, (
        f"the non-vacuity guard: the full run must collect the test AND the "
        f"doctest, or the narrowed count below proves nothing; got:\n{full}"
    )
    assert "collected 1 item" in narrow, (
        f"narrowing to 'tests' must leave src/'s doctest uncollected — a "
        f"positional path is a statement about what to run; got:\n{narrow}"
    )


def test_a_narrowed_run_audits_the_whole_project_when_nothing_is_declared(
    tmp: TempDir,
) -> None:
    """Declaring nothing must not mean "argv is the declaration"."""
    # Arrange — no testpaths at all, so the audit's roots come from the layout.
    integ.write_project(
        tmp,
        pyproject=_UNDECLARED_PROJECT,
        tests={},
        extra_files={
            "tests/test_one.py": _TEST_FILE,
            "src/audited.py": (
                '"""A subject whose public function has no examples."""\n'
                "\n\n"
                "def audited_fn(value):\n"
                '    """Return the value unchanged."""\n'
                "    return value\n"
            ),
        },
    )

    # Act
    full_out, full_err, full_rc = helpers.run_oxitest(None, cwd=str(tmp))
    narrow_out, narrow_err, narrow_rc = helpers.run_oxitest(None, "tests", cwd=str(tmp))
    full = full_out + full_err
    narrow = narrow_out + narrow_err

    # Assert
    assert full_rc == 3, (
        f"the control: with nothing declared the audit walks the project root, "
        f"so an unexamined subject under 'src' must fail; rc={full_rc}\n{full}"
    )
    assert narrow_rc == 3, (
        f"a project that declares no testpaths has declared nothing — argv is "
        f"not a substitute for a declaration, and treating it as one is the "
        f"#1798 defect surviving in the undeclared branch; rc={narrow_rc}\n{narrow}"
    )
    assert "audited.audited_fn" in narrow, (
        f"the narrowed run must name the subject it fails on, or the exit code "
        f"is all the user has to work from; got:\n{narrow}"
    )
