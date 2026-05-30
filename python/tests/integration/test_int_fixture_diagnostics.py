"""Integration tests: fixture diagnostics features."""

from pathlib import Path

from conftest import helpers
from oxitest import TempDir


def test_strict_abort_unused_fixture(tmp: TempDir):
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
    out, stderr, rc = helpers.common.run_oxitest(tmp)

    # Assert
    assert rc == 3, (
        f"strict abort with unused fixture should exit 3, got {rc}\n"
        f"stdout: {out!r}\nstderr: {stderr!r}"
    )
    combined = out + stderr
    assert "unused" in combined.lower(), (
        f"output should mention 'unused': stdout={out!r}, stderr={stderr!r}"
    )


def test_strict_abort_missing_return_annotation(tmp: TempDir):
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
    out, stderr, rc = helpers.common.run_oxitest(tmp)

    # Assert
    assert rc == 3, (
        f"strict abort with missing annotation should exit 3, got {rc}\n"
        f"stdout: {out!r}\nstderr: {stderr!r}"
    )
    combined = out + stderr
    assert "return" in combined.lower() or "annotation" in combined.lower(), (
        f"output should mention 'return' or 'annotation': "
        f"stdout={out!r}, stderr={stderr!r}"
    )


def test_fixture_shadow_warning_in_output(tmp: TempDir):
    """Shadow warning appears in stderr when child conftest overrides parent fixture."""
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
    out, stderr, rc = helpers.common.run_oxitest(root)

    # Assert — test passes but shadow warning appears
    assert rc == 0, (
        f"test should pass, got rc={rc}\nstdout: {out!r}\nstderr: {stderr!r}"
    )
    combined = out + stderr
    assert "shadow" in combined.lower(), (
        f"output should mention 'shadow': stdout={out!r}, stderr={stderr!r}"
    )


def test_teardown_warning_includes_test_name(tmp: TempDir):
    """Teardown error warning includes the test node_id for attribution."""
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
    out, stderr, rc = helpers.common.run_oxitest(tmp)

    # Assert — test passes, teardown warning includes test function name
    assert rc == 0, (
        f"test should pass despite teardown error, got rc={rc}\n"
        f"stdout: {out!r}\nstderr: {stderr!r}"
    )
    combined = out + stderr
    assert "test_uses_exploding" in combined, (
        f"teardown warning should include test function name: "
        f"stdout={out!r}, stderr={stderr!r}"
    )
    assert "teardown" in combined.lower(), (
        f"output should mention 'teardown': stdout={out!r}, stderr={stderr!r}"
    )
