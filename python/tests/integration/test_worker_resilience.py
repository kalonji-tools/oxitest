"""Integration tests: worker crash and timeout resilience."""

import oxitest
from oxitest import TempDir, helpers


@oxitest.mark.timeout(120)
def test_worker_crash_reported_as_failure(tmp: TempDir) -> None:
    """A worker that crashes via os._exit is reported as a failure, not a hang."""
    (tmp / "test_crash.py").write_text(
        "import os\n"
        "\n"
        "def test_normal():\n"
        "    assert 1 == 1\n"
        "\n"
        "def test_crashes():\n"
        "    os._exit(1)\n"
    )

    out, stderr, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    combined = out + stderr
    # The run should exit non-zero — the crash is a failure.
    assert rc != 0, f"worker crash should cause non-zero exit, got {rc}\n{combined}"
    # The normal test should still pass (run by another worker or before crash).
    helpers.integ.assert_contains(out, "1 passed")


@oxitest.mark.timeout(120)
def test_timeout_reported_per_test(tmp: TempDir) -> None:
    """A test exceeding --timeout is reported as a timeout failure."""
    (tmp / "test_slow.py").write_text(
        "import time\n"
        "\n"
        "def test_fast():\n"
        "    assert 1 == 1\n"
        "\n"
        "def test_hangs():\n"
        "    time.sleep(30)\n"
    )

    out, _, rc = helpers.common.run_oxitest(tmp, "--timeout", "1")
    helpers.integ.assert_failed(out, rc)
    # Should mention timeout in output
    helpers.integ.assert_contains(out.lower(), "timeout")
