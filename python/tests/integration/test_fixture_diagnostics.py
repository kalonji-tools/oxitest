"""Integration tests: fixture diagnostics features."""

from pathlib import Path

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_strict_abort_unused_fixture(tmp: TempDir) -> None:
    """Unused fixture in strict=abort mode exits non-zero with 'unused' in output."""
    # Arrange — conftest defines a fixture that no test uses
    (tmp / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture\n"
        "def unused_db() -> str:\n"
        "    return 'connection'\n"
    )
    (tmp / "test_nothing.py").write_text("def test_pass(): assert True\n")
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')

    # Act
    out, stderr, rc = helpers.run_oxitest(tmp)

    # Assert
    integ.assert_collection_error(out, rc)
    combined = out + stderr
    integ.assert_contains(combined.lower(), "unused")


def test_strict_abort_missing_return_annotation(tmp: TempDir) -> None:
    """Fixture missing return annotation in strict=abort mode exits non-zero."""
    # Arrange — conftest defines a fixture without return type annotation
    (tmp / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture\n"
        "def db():\n"
        "    return 'connection'\n"
    )
    (tmp / "test_uses_db.py").write_text(
        "from oxitest import Fixture\n"
        "def test_use(db: Fixture[str]): assert db == 'connection'\n"
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')

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
    (root / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'root_db'\n"
    )
    sub = root / "sub"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'child_db'\n"
    )
    (sub / "test_shadow.py").write_text(
        "from oxitest import Fixture\n"
        "def test_use_db(db: Fixture[str]):\n"
        "    assert db == 'child_db'\n"
    )

    # Act
    out, stderr, rc = helpers.run_oxitest(root, "--warnings")

    # Assert — test passes but shadow warning appears
    integ.assert_passed(out, rc)
    integ.assert_contains((out + stderr).lower(), "shadow")


def test_teardown_warning_includes_test_name(tmp: TempDir) -> None:
    """Teardown error diagnostic includes the test node_id for attribution."""
    # Arrange — yield fixture that raises during teardown
    (tmp / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "from oxitest import Yields\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture\n"
        "def exploding() -> Yields[str]:\n"
        "    yield 'value'\n"
        "    raise RuntimeError('boom in teardown')\n"
    )
    (tmp / "test_td.py").write_text(
        "from oxitest import Fixture\n"
        "def test_uses_exploding(exploding: Fixture[str]):\n"
        "    assert exploding == 'value'\n"
    )

    # Act
    out, stderr, rc = helpers.run_oxitest(tmp, "--warnings")

    # Assert — test passes, teardown warning includes test function name
    integ.assert_passed(out, rc)
    combined = out + stderr
    integ.assert_contains(combined, "test_uses_exploding")
    integ.assert_contains(combined.lower(), "teardown")
