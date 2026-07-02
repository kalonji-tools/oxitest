"""Integration tests: module-level marks via oxi_mark."""

from oxitest import TempDir, helpers


def test_oxi_mark_applies_to_all_tests(tmp: TempDir):
    """oxi_mark marks apply to all tests in the module."""
    (tmp / "test_mod.py").write_text(
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow]\n"
        "def test_a(): assert True\n"
        "def test_b(): assert True\n"
    )
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\nmarkers = ["slow"]\n')
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=2)


def test_oxi_mark_visible_to_expression_filter(tmp: TempDir):
    """-E mark() filtering sees module-level marks."""
    (tmp / "test_mod.py").write_text(
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow]\n"
        "def test_a(): assert True\n"
        "def test_b(): assert True\n"
    )
    (tmp / "test_other.py").write_text("def test_c(): assert True\n")
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\nmarkers = ["slow"]\n')
    out, _, rc = helpers.common.run_oxitest(tmp, "-E", "mark(slow)")
    helpers.integ.assert_passed(out, rc, count=2)


def test_oxi_mark_per_test_override(tmp: TempDir):
    """Per-test mark overrides module mark — test with per-test skip is skipped."""
    (tmp / "test_mod.py").write_text(
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.timeout(120)]\n"
        "@oxitest.mark.skip(reason='override')\n"
        "def test_skipped(): assert True\n"
        "def test_runs(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)
    helpers.integ.assert_contains(out, "1 skipped")


def test_oxi_mark_strict_validates_module_marks(tmp: TempDir):
    """--strict catches missing reason on module-level skip mark."""
    (tmp / "test_mod.py").write_text(
        "import oxitest\noxi_mark = [oxitest.mark.skip]\ndef test_a(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--strict")
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "missing-mark-reason")


def test_oxi_mark_single_mark_not_list(tmp: TempDir):
    """oxi_mark = oxi.mark.slow (single, not list) works."""
    (tmp / "test_mod.py").write_text(
        "import oxitest\noxi_mark = oxitest.mark.slow\ndef test_a(): assert True\n"
    )
    (tmp / "pyproject.toml").write_text('[tool.oxitest]\nmarkers = ["slow"]\n')
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)
