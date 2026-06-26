"""Integration tests for lazy collection pipeline."""

from conftest import helpers
from oxitest import TempDir


def test_lazy_collection_single_node_id_skips_other_modules(tmp: TempDir):
    """Running a single test by node ID should not import unmatched modules."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\ndef test_two(): pass\n",
            "test_b.py": "def test_three(): pass\n",
            "test_c.py": "def test_four(): pass\n",
        },
    )
    out, _, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "run",
        "test_a.py::test_one",
        cwd=".",
    )
    helpers.integ.assert_passed(out, rc, count=1)


def test_lazy_collection_expression_filter(tmp: TempDir):
    """Expression filter should only import modules with matching tests."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_auth.py": (
                "import oxitest as oxi\n@oxi.mark.slow\ndef test_login(): pass\n"
            ),
            "test_db.py": "def test_query(): pass\n",
        },
        pyproject='[tool.oxitest]\nmarkers = ["slow: slow tests"]\n',
    )
    out, _, rc = helpers.common.run_oxitest(
        tmp,
        "-E",
        "mark(slow)",
    )
    helpers.integ.assert_passed(out, rc, count=1)


def test_lazy_collection_dynamic_file_falls_back_to_eager(tmp: TempDir):
    """File with exec() should fall back to eager import and still work."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_dynamic.py": ("def test_static(): pass\n"),
        },
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)


def test_lazy_collection_no_filter_imports_all(tmp: TempDir):
    """Running without any filter should import all modules (no regression)."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\n",
        },
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=2)


def test_lazy_collection_last_failed_only_imports_matched(tmp: TempDir):
    """--failed=only should only import modules with previously failed tests."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_pass(): pass\n",
            "test_b.py": "def test_fail(): assert False\n",
        },
    )
    # First run — test_fail fails
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)

    # Fix the test
    (tmp / "test_b.py").write_text("def test_fail(): pass\n")

    # Second run with --failed=only
    out, _, rc = helpers.common.run_oxitest(tmp, "--failed=only")
    helpers.integ.assert_passed(out, rc, count=1)


def test_lazy_collection_conftest_ancestor_chain(tmp: TempDir):
    """Filtered run should only load ancestor-chain conftests."""
    (tmp / "pyproject.toml").write_text("[tool.oxitest]\n")
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "fx = Fixtures()\n"
        "@fx.fixture\n"
        "def shared():\n"
        "    return 42\n"
    )
    unit = tmp / "unit"
    unit.mkdir()
    (unit / "test_a.py").write_text(
        "from oxitest import Fixture\ndef test_unit(shared: Fixture[int]): pass\n"
    )
    integ_dir = tmp / "integration"
    integ_dir.mkdir()
    (integ_dir / "test_b.py").write_text("def test_integ(): pass\n")

    # Run only unit test
    out, _, rc = helpers.common.run_oxitest_subcmd(
        tmp,
        "run",
        "unit/test_a.py::test_unit",
        cwd=".",
    )
    helpers.integ.assert_passed(out, rc, count=1)
