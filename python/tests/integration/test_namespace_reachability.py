"""Integration: a derived namespace that cannot be written is reported (#1782).

The namespace is a directory basename, and nothing constrains a directory name
to the shape `fx.<namespace>.<name>` needs. Where it does not fit, the run
warns and keeps going — shortcut access still resolves, so refusing would fail
a project that works.

Every run here passes ``--warnings``. Without it the diagnostic never reaches
stdout and each assertion below would pass against a run that emitted nothing.
"""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

_FIXTURE = """import oxitest as oxi


@oxi.fixture(lifetime="package")
def conn() -> str:
    return "conn-value"
"""

_SHORTCUT_TEST = """from oxitest import Fixtures


def test_shortcut(fx: Fixtures) -> None:
    assert fx.conn == "conn-value", (
        "shortcut access is what keeps an unreachable namespace usable, and "
        "so is what makes a warning the right severity instead of a refusal"
    )
"""


def _write(tmp: TempDir, directory: str) -> None:
    """A project whose fixture package sits in *directory*."""
    integ.write_project(
        tmp,
        tests={},
        pyproject='[tool.oxitest]\ntestpaths = ["tests"]\n',
        extra_files={
            f"tests/{directory}/__fixtures__.py": _FIXTURE,
            f"tests/{directory}/test_use.py": _SHORTCUT_TEST,
        },
    )


def test_a_non_identifier_directory_warns(tmp: TempDir) -> None:
    """`integration-tests` is the dangerous case: it parses as arithmetic.

    `fx.integration-tests.conn` is valid Python meaning
    `fx.integration - tests.conn`, so the access never reaches oxitest and the
    run reports a missing fixture named `integration`. The warning is the only
    thing that connects that symptom to the directory name.
    """
    _write(tmp, "integration-tests")

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_contains(
        out,
        "namespace 'integration-tests' cannot be written",
        "not a Python identifier",
        "rename the directory",
    )


def test_a_keyword_directory_warns(tmp: TempDir) -> None:
    """`fx.class.conn` is a SyntaxError, so the reason differs from above."""
    _write(tmp, "class")

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_contains(
        out, "namespace 'class' cannot be written", "a Python keyword"
    )


def test_a_builtin_directory_does_not_warn(tmp: TempDir) -> None:
    """`fx.int.conn` parses and resolves, so refusing it would be wrong.

    This is the relaxation half of #1782. The previous validator rejected
    `int`, `list`, `match` and `type` on the plugin path while accepting
    `integration-tests`, so it was wrong in both directions at once.
    """
    _write(tmp, "int")

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "cannot be written")


def test_a_soft_keyword_directory_does_not_warn(tmp: TempDir) -> None:
    """Soft keywords are usable as identifiers, so `fx.match.conn` is legal."""
    _write(tmp, "match")

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "cannot be written")


def test_one_warning_per_namespace_not_per_declaration(tmp: TempDir) -> None:
    """Five fixtures in one unreachable directory report once.

    The count is the assertion. A per-declaration emit would report five
    times for one problem with one remedy, which trains a reader to skim
    warnings.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject='[tool.oxitest]\ntestpaths = ["tests"]\n',
        extra_files={
            "tests/bad-name/__fixtures__.py": (
                "import oxitest as oxi\n\n\n"
                + "\n\n".join(
                    f'@oxi.fixture(lifetime="module")\n'
                    f"def fixture_{index}() -> int:\n"
                    f"    return {index}\n"
                    for index in range(5)
                )
            ),
            "tests/bad-name/test_use.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_one(fx: Fixtures) -> None:\n"
                "    assert fx.fixture_0 == 0, 'five declarations, one namespace'\n"
            ),
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    assert out.count("cannot be written") == 1, (
        "five declarations share one unreachable namespace and one remedy, so "
        "reporting per declaration would emit five copies of the same warning"
    )


def test_an_inline_declaration_names_the_module_not_a_directory(tmp: TempDir) -> None:
    """An inline anchor is a file, so the remedy names the module.

    Qualified access to an inline declaration really does work — `fx.<module
    stem>.<name>` resolves — so an unreachable module stem loses a real route
    and the warning is not noise. Reaching this arm needs `python_files` to
    admit a stem that is not an identifier, which the default `test_*.py`
    never does.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject=(
            '[tool.oxitest]\ntestpaths = ["tests"]\npython_files = ["*-test.py"]\n'
        ),
        extra_files={
            "tests/my-test.py": (
                "import oxitest as oxi\n"
                "from oxitest import Fixtures\n\n\n"
                '@oxi.fixture(lifetime="module")\n'
                "def conn() -> str:\n"
                '    return "inline-value"\n\n\n'
                "def test_shortcut(fx: Fixtures) -> None:\n"
                '    assert fx.conn == "inline-value", (\n'
                '        "an inline declaration in a module whose stem is not an "\n'
                '        "identifier is still reachable by shortcut"\n'
                "    )\n"
            ),
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_contains(out, "namespace 'my-test' cannot be written")
    integ.assert_contains(out, "Derived from the test module name")
    integ.assert_excludes(out, "Derived from the directory name")


# ── the plugin path: severity keys on authorship, not on path ─────────────────

_PLUGIN_ENTRY = """from oxitest.plugin import Plugin


def oxitest_plugin(config=None):
    return Plugin()
"""

_PLUGIN_FIXTURE = """import oxitest as oxi


@oxi.fixture(lifetime="module")
def dsn() -> str:
    return "from-plugin"
"""

_PLUGIN_TEST = """from oxitest import Fixtures


def test_shortcut(fx: Fixtures) -> None:
    assert fx.dsn == "from-plugin", (
        "shortcut access reaches a plugin fixture whatever its namespace spells"
    )
"""


def test_a_declared_plugin_namespace_that_cannot_be_written_is_refused(
    tmp: TempDir,
) -> None:
    """Someone typed this name, so they can retype it — that is an error.

    The remedy is one line of pyproject, which is why this arm refuses where
    the derived arms warn.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject=(
            "[tool.oxitest]\n"
            'testpaths = ["tests"]\n'
            "plugins = ['my_plugin']\n"
            "\n"
            "[tool.oxitest.plugin_settings.my_plugin]\n"
            'namespace = "my-plugin"\n'
        ),
        extra_files={
            "my_plugin/__init__.py": _PLUGIN_ENTRY,
            "my_plugin/__fixtures__.py": _PLUGIN_FIXTURE,
            "tests/test_use.py": _PLUGIN_TEST,
        },
    )

    out, err, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    # 4, not 3 (#2172). This comment used to read "3, not 4", on the ground
    # that `plugin_fixture_homes` runs during collection so a UsageError
    # raised there surfaces as a collection error whatever its type. That was
    # a measurement of the defect, not of a decision: ADR-0014 fixes exit 4 by
    # the class of the error and not by when oxitest detects it, and
    # exit-codes.md defines exit 3 as a test file that could not be imported,
    # a declaration inside one that was refused, or a strict violation. A
    # namespace nobody can write is none of those. The reserved-namespace
    # neighbour in the same function moved with it, so the two stay
    # consistent — which is what the previous comment was really pinning.
    integ.assert_usage_error(out + err, rc)
    integ.assert_contains(out + err, "my-plugin", "not a Python identifier")


def test_a_derived_dotted_plugin_namespace_warns(tmp: TempDir) -> None:
    """A dotted module path is not an identifier, and nobody chose it.

    `plugins = ["pkg.sub"]` derives the namespace `pkg.sub`, which cannot be
    written: `fx.pkg.sub.dsn` reads as two segments and looks up a namespace
    called `pkg`. Refusing would break a project that runs today through
    shortcut access, which is the same reason the directory-derived case warns.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject=("[tool.oxitest]\ntestpaths = [\"tests\"]\nplugins = ['pkg.sub']\n"),
        extra_files={
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": _PLUGIN_ENTRY,
            "pkg/sub/__fixtures__.py": _PLUGIN_FIXTURE,
            "tests/test_use.py": _PLUGIN_TEST,
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_contains(out, "pkg.sub", "not a Python identifier")
