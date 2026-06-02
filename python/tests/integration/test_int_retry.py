"""Integration tests: --retries flag behavior."""

from conftest import helpers
from oxitest import TempDir


def test_persistent_failure_exits_1(tmp: TempDir):
    """A test that always fails still exits 1 even with --retries 1."""
    (tmp / "test_always_fail.py").write_text("def test_always_bad(): assert False\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--retries", "1")
    helpers.integ.assert_failed(out, rc)


def test_retries_zero_is_default(tmp: TempDir):
    """Without --retries, a failing test exits 1 and output has no 'flaky'."""
    (tmp / "test_fail_default.py").write_text("def test_bad(): assert False\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_excludes(out, "flaky")


def test_flaky_test_exits_0(tmp: TempDir):
    """A test that fails on first attempt but passes on retry is reported as flaky."""
    marker_path = str(tmp / "_flaky_marker")
    (tmp / "test_flaky.py").write_text(
        "from pathlib import Path\n\n"
        "def test_flaky():\n"
        f"    marker = Path({marker_path!r})\n"
        "    if not marker.exists():\n"
        "        marker.write_text('seen')\n"
        "        assert False, 'first attempt'\n"
        "    marker.unlink()\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp, "--retries", "1", "--serial")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "flaky")
