"""Integration tests: fixture diagnostics features."""

from pathlib import Path

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_strict_abort_unused_fixture(tmp: TempDir) -> None:
    """Unused fixture in strict=abort mode exits non-zero with 'unused' in output."""
    # Arrange — conftest defines a fixture that no test uses
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def unused_db() -> str:\n"
        "    return 'connection'\n",
        encoding="utf-8",
    )
    (tmp / "test_nothing.py").write_text(
        "def test_pass(): assert True\n", encoding="utf-8"
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n', encoding="utf-8")

    # Act
    out, stderr, rc = helpers.run_oxitest(tmp)

    # Assert
    integ.assert_collection_error(out, rc)
    combined = out + stderr
    integ.assert_contains(combined.lower(), "unused")


def test_strict_abort_missing_return_annotation(tmp: TempDir) -> None:
    """Fixture missing return annotation in strict=abort mode exits non-zero."""
    # Arrange — conftest defines a fixture without return type annotation
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def db():\n"
        "    return 'connection'\n",
        encoding="utf-8",
    )
    (tmp / "test_uses_db.py").write_text(
        "from oxitest import Fixture\n"
        "def test_use(db: Fixture[str]): assert db == 'connection'\n",
        encoding="utf-8",
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n', encoding="utf-8")

    # Act
    out, stderr, rc = helpers.run_oxitest(tmp)

    # Assert
    integ.assert_collection_error(out, rc)
    combined = out + stderr
    assert "return" in combined.lower() or "annotation" in combined.lower(), (
        f"output should mention 'return' or 'annotation': "
        f"stdout={out!r}, stderr={stderr!r}"
    )


def test_fixture_shadow_warning_in_output(tmp: TempDir) -> None:
    """Shadow diagnostic appears when child conftest overrides parent fixture."""
    # Arrange — root conftest defines 'db', sub/ conftest overrides it
    root = tmp / "proj"
    root.mkdir()
    (root / "__fixtures__.py").write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def db() -> str:\n"
        "    return 'root_db'\n",
        encoding="utf-8",
    )
    sub = root / "sub"
    sub.mkdir()
    (sub / "__fixtures__.py").write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def db() -> str:\n"
        "    return 'child_db'\n",
        encoding="utf-8",
    )
    # A test at the root as well as in sub/: the rootdir package is the
    # deepest directory covering every test, so with tests only in sub/ the
    # fold lands on sub/ and the root declaration is never in scope — no
    # shadowing to report, and the assertion below would fail for a reason
    # that has nothing to do with the diagnostic (#1720).
    (root / "test_root.py").write_text(
        "from oxitest import Fixture\n"
        "def test_root_db(db: Fixture[str]):\n"
        "    assert db == 'root_db'\n",
        encoding="utf-8",
    )
    (sub / "test_shadow.py").write_text(
        "from oxitest import Fixture\n"
        "def test_use_db(db: Fixture[str]):\n"
        "    assert db == 'child_db'\n",
        encoding="utf-8",
    )

    # Act
    out, stderr, rc = helpers.run_oxitest(root, "--warnings")

    # Assert — test passes but shadow warning appears
    integ.assert_passed(out, rc)
    integ.assert_contains((out + stderr).lower(), "shadow")


def test_teardown_warning_includes_test_name(tmp: TempDir) -> None:
    """Teardown error diagnostic includes the test node_id for attribution."""
    # Arrange — yield fixture that raises during teardown
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import Yields, fixture\n"
        "@fixture(lifetime='function')\n"
        "def exploding() -> Yields[str]:\n"
        "    yield 'value'\n"
        "    raise RuntimeError('boom in teardown')\n",
        encoding="utf-8",
    )
    (tmp / "test_td.py").write_text(
        "from oxitest import Fixture\n"
        "def test_uses_exploding(exploding: Fixture[str]):\n"
        "    assert exploding == 'value'\n",
        encoding="utf-8",
    )

    # Act
    out, stderr, rc = helpers.run_oxitest(tmp, "--warnings")

    # Assert — test passes, teardown warning includes test function name
    integ.assert_passed(out, rc)
    combined = out + stderr
    integ.assert_contains(combined, "test_uses_exploding")
    integ.assert_contains(combined.lower(), "teardown")
