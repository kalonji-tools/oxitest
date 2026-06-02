"""Integration tests: Unicode handling in test names and output."""

from conftest import helpers
from oxitest import TempDir


def test_unicode_test_name_renders_correctly(tmp: TempDir):
    """Test with Unicode characters in its name runs and appears in output."""
    (tmp / "test_unicode.py").write_text(
        "def test_café_résultat():\n    assert 1 == 1\n"
    )

    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_café_résultat")
