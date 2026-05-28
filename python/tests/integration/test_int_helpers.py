"""Integration tests: conftest helpers namespace."""

from conftest import helpers
from oxitest import TempDir


def test_helpers_accessible_from_test_file(tmp: TempDir):
    """A test file can import helpers from conftest."""
    project = tmp / "myproject"
    project.mkdir()
    (project / "conftest.py").write_text("def greet():\n    return 'hello'\n")
    helpers.common.write_test_file(
        project,
        "from conftest import helpers\n"
        "\n"
        "def test_greet():\n"
        "    assert helpers.myproject.greet() == 'hello'\n",
    )
    stdout, rc = helpers.common.run_oxitest(project)
    assert rc == 0, f"expected exit 0, got {rc}\n{stdout}"
    assert "1 passed" in stdout, f"expected '1 passed' in output:\n{stdout}"


def test_nested_conftest_helpers_ancestor_only(tmp: TempDir):
    """Test file sees both ancestor and local helpers when run from root."""
    root = tmp / "myproject"
    root.mkdir()
    (root / "conftest.py").write_text("def root_fn():\n    return 'root'\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "conftest.py").write_text("def sub_fn():\n    return 'sub'\n")
    helpers.common.write_test_file(
        sub,
        "from conftest import helpers\n"
        "\n"
        "def test_sees_root():\n"
        "    assert helpers.myproject.root_fn() == 'root'\n"
        "\n"
        "def test_sees_sub():\n"
        "    assert helpers.sub.sub_fn() == 'sub'\n",
    )
    # Run from root so oxitest walks the full conftest chain (root -> sub)
    stdout, rc = helpers.common.run_oxitest(root)
    assert rc == 0, f"expected exit 0, got {rc}\n{stdout}"
    assert "2 passed" in stdout, f"expected '2 passed' in output:\n{stdout}"


def test_helpers_namespace_override(tmp: TempDir):
    """__helpers_namespace__ overrides the directory-derived name."""
    root = tmp / "myproject"
    root.mkdir()
    sub = root / "integration"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        '__helpers_namespace__ = "integ"\n\ndef helper():\n    return 42\n'
    )
    helpers.common.write_test_file(
        sub,
        "from conftest import helpers\n"
        "\n"
        "def test_override():\n"
        "    assert helpers.integ.helper() == 42\n",
    )
    stdout, rc = helpers.common.run_oxitest(sub)
    assert rc == 0, f"expected exit 0, got {rc}\n{stdout}"
    assert "1 passed" in stdout, f"expected '1 passed' in output:\n{stdout}"


def test_helpers_only_conftest_no_warning(tmp: TempDir):
    """A conftest with only helpers produces no warning."""
    root = tmp / "myproject"
    root.mkdir()
    (root / "conftest.py").write_text("def helper():\n    return 'ok'\n")
    helpers.common.write_test_file(
        root,
        "def test_pass():\n    assert True\n",
    )
    stdout, rc = helpers.common.run_oxitest(root)
    assert rc == 0, f"expected exit 0, got {rc}\n{stdout}"
    assert "no Fixtures instance" not in stdout, (
        f"unexpected warning in output:\n{stdout}"
    )
