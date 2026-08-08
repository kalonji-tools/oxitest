"""Integration tests: Unicode handling in test names and output."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_unicode_test_name_renders_correctly(tmp: TempDir) -> None:
    """Test with Unicode characters in its name runs and appears in output."""
    (tmp / "test_unicode.py").write_text(
        "def test_café_résultat():\n    assert 1 == 1\n", encoding="utf-8"
    )

    out, _, rc = helpers.run_oxitest_subcmd(tmp, "query", "tests")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "test_café_résultat")
