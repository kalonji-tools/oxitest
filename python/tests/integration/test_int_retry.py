"""Integration tests: --retries flag behavior."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import run_oxitest
from oxitest import TempDir


def test_persistent_failure_exits_1(tmp: TempDir):
    """A test that always fails still exits 1 even with --retries 1."""
    (tmp / "test_always_fail.py").write_text("def test_always_bad(): assert False\n")
    out, rc = run_oxitest(tmp, "--retries", "1")
    assert rc == 1, f"persistent failure with --retries 1 should exit 1, got {rc}"
    assert "failed" in out, "output should mention failed"


def test_retries_zero_is_default(tmp: TempDir):
    """Without --retries, a failing test exits 1 and output has no 'flaky'."""
    (tmp / "test_fail_default.py").write_text("def test_bad(): assert False\n")
    out, rc = run_oxitest(tmp)
    assert rc == 1, f"failing test without retries should exit 1, got {rc}"
    assert "flaky" not in out, "output should not mention flaky when no retries used"


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
    out, rc = run_oxitest(tmp, "--retries", "1", "--serial")
    assert rc == 0, f"flaky test with --retries 1 should exit 0, got {rc}; out={out!r}"
    assert "flaky" in out, f"output should mention flaky, got: {out!r}"
