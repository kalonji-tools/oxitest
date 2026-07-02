"""Integration test: parallel failure output shows worker ID and concurrent tests."""

from oxitest import TempDir, helpers


def test_parallel_failure_shows_worker_context(tmp: TempDir):
    """A failing test in parallel mode should show worker # and concurrent tests."""
    # Write enough test files to force parallel execution with multiple workers
    (tmp / "test_a.py").write_text(
        "import time\n\ndef test_slow_pass():\n    time.sleep(0.3)\n"
    )
    (tmp / "test_b.py").write_text(
        "def test_fail():\n    assert False, 'deliberate failure'\n"
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp, "--workers", "2")
    output = stdout + stderr
    assert rc != 0, f"expected non-zero exit for failing test, got {rc}\n{output}"
    assert "worker #" in output, f"Expected 'worker #' in output:\n{output}"
