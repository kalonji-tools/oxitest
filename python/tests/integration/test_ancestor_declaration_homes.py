"""An unparsable ancestor declaration home warns or errors by file kind (#1765).

The ancestor walk reaches directories a run never used to read, so an ordinary
broken ``__init__.py`` several levels up must not fail the run through the
fixture system. ``__fixtures__.py`` is a reserved name whose only purpose is
declarations, so failing to parse it certainly loses fixtures and stays a
collection error.

Both halves are asserted deliberately. A test covering only the ``__init__.py``
warning passes equally against an implementation that downgraded *both* kinds to
warnings, which is the mutant this pair exists to kill.

``--warnings`` expands the diagnostic block so the message is emitted verbatim;
without it a run that emits only a warning also exits 0 and the assertion on
diagnostic text would be inert.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

BROKEN_SOURCE = "def broken(:\n    pass\n"

PYPROJECT = """\
[project]
name = "ancestor_home"
version = "0.0.0"

[tool.oxitest]
testpaths = ["tests"]
"""

ANCESTOR_FIXTURES = (
    "import oxitest as oxi\n"
    "\n"
    "\n"
    '@oxi.fixture(lifetime="function")\n'
    "def shared() -> str:\n"
    '    return "from-ancestor"\n'
)

DESCENDANT_TEST = (
    "from oxitest import Fixture\n"
    "\n"
    "\n"
    "def test_api(shared: Fixture[str]) -> None:\n"
    '    assert shared == "from-ancestor", "the sibling __fixtures__.py parses fine"\n'
)


def test_unparsable_ancestor_init_warns_and_the_run_continues(tmp: TempDir) -> None:
    """An ordinary broken __init__.py above the tests must not fail the run."""
    # Arrange
    integ.write_project(
        tmp,
        tests={},
        pyproject=PYPROJECT,
        extra_files={
            "tests/__init__.py": BROKEN_SOURCE,
            "tests/__fixtures__.py": ANCESTOR_FIXTURES,
            "tests/api/test_api.py": DESCENDANT_TEST,
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", cwd=str(tmp))

    # Assert
    combined = out + err
    assert rc == 0, (
        "a broken __init__.py is an ordinary package-init file with nothing to "
        "do with fixtures; the ancestor walk reaches directories the run never "
        "used to read, so failing on it is collateral the walk must not add: "
        f"{combined!r}"
    )
    assert "__init__.py" in combined, (
        "the user must still be told the file was skipped — silence would hide "
        "an __init__.py that genuinely declared fixtures and failed to load"
    )


def test_ancestor_init_that_raises_on_import_warns_and_the_run_continues(
    tmp: TempDir,
) -> None:
    """An ancestor __init__.py that fails to import must not fail the run.

    A decorated function makes prescan unable to rule out declarations, so the
    file is imported and the runtime decides (#1859). The ancestor walk reaches
    files a run never used to read, so an ordinary package initialiser that
    happens to raise must not take the run down with it — the same reason an
    unparsable one warns.
    """
    # Arrange
    integ.write_project(
        tmp,
        tests={},
        pyproject=PYPROJECT,
        extra_files={
            "tests/__init__.py": (
                '"""An ordinary initialiser that fails at import time."""\n'
                "\n"
                "import functools\n"
                "\n"
                'raise RuntimeError("unrelated breakage in an ancestor package")\n'
                "\n"
                "\n"
                "@functools.cache\n"
                "def expensive() -> int:\n"
                "    return 42\n"
            ),
            "tests/api/test_api.py": (
                "def test_api() -> None:\n"
                '    assert True, "an ordinary test that uses no fixtures at all"\n'
            ),
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", cwd=str(tmp))

    # Assert
    combined = out + err
    assert rc == 0, (
        "this project passes on a tree without the ancestor walk; failing it "
        "because an unrelated ancestor initialiser raises is collateral the "
        f"walk introduced, not a defect it found: {combined!r}"
    )
    assert "__init__.py" in combined, (
        "the diagnostic must name the file — the bare exception text leaves the "
        f"user with no way to tell which initialiser failed: {combined!r}"
    )


def test_unparsable_ancestor_fixtures_home_is_a_collection_error(
    tmp: TempDir,
) -> None:
    """__fixtures__.py exists only to declare, so failing to read it is fatal."""
    # Arrange
    integ.write_project(
        tmp,
        tests={},
        pyproject=PYPROJECT,
        extra_files={
            "tests/__fixtures__.py": BROKEN_SOURCE,
            "tests/api/test_api.py": (
                "def test_api() -> None:\n"
                '    assert True, "the run must not reach this"\n'
            ),
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--warnings", cwd=str(tmp))

    # Assert
    combined = out + err
    assert rc != 0, (
        "a __fixtures__.py that cannot be parsed has certainly lost fixtures; "
        f"exiting 0 would report a green run that silently dropped them: {combined!r}"
    )
    assert "could not be parsed" in combined, (
        "the diagnostic must name the file, or the user hunts a "
        f"fixture-not-found with no pointer to the cause: {combined!r}"
    )
