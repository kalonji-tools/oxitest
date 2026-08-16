"""A parallel run stopped by --maxfail must not report undispatched tests as crashes.

`run_phase_parallel` breaks out of its result loop when maxfail is reached, then
drains every group the scheduler still holds into crash sentinels. Before #2142
that reported `Worker subprocess exited unexpectedly` for tests no worker had
been given, while every worker was alive and healthy.

Measured before the fix, 60 Test Items in 10 modules at `-n 4`, 5 of 5 runs:
`1 failed · 36 errors`, against `1 failed · 18 passed` for the same suite serially.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

_SENTINEL = "Worker subprocess exited unexpectedly"

_FAILING_MODULE = (
    "import time\n"
    "\n"
    "\n"
    "def test_a_fails():\n"
    "    time.sleep(0.05)\n"
    "    assert False, 'the one real failure'\n"
)


def _passing_module(index: int) -> str:
    body = ["import time\n"]
    body.extend(
        f"\n\ndef test_m{index}_{item}():\n"
        "    time.sleep(0.05)\n"
        f"    assert True, 'm{index}_{item}'\n"
        for item in range(6)
    )
    return "".join(body)


def _write_suite(tmp: TempDir) -> None:
    """Enough modules that the scheduler still holds groups when maxfail fires."""
    (tmp / "test_a.py").write_text(_FAILING_MODULE, encoding="utf-8")
    for index in range(1, 10):
        (tmp / f"test_m{index}.py").write_text(_passing_module(index), encoding="utf-8")


def test_a_maxfail_run_reports_no_phantom_worker_crash(tmp: TempDir) -> None:
    """The tests no worker was given are absent, not reported as crashes."""
    # Arrange
    _write_suite(tmp)

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(tmp, "--maxfail=1", "-n", "4")
    output = stdout + stderr

    # Assert
    assert _SENTINEL not in output, (
        "every worker is alive here — maxfail stopped the coordinator, it did "
        "not kill a worker. A test nobody was given did not run, and the serial "
        f"path reports nothing for it (#2142).\noutput:\n{output}"
    )
    assert "1 failed" in output, (
        "the one real failure must still be reported; without this the test "
        f"passes on a run that reported nothing at all.\noutput:\n{output}"
    )
    assert returncode == 2, (
        "a failing suite exits 2, and both the correct and the defective runs "
        "did, which is why a pass/fail CI gate cannot see this defect. "
        f"Got {returncode}.\noutput:\n{output}"
    )


def test_the_serial_path_reports_the_same_shape(tmp: TempDir) -> None:
    """The control. It is what makes the assertion above mean something."""
    # Arrange
    _write_suite(tmp)

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(tmp, "--maxfail=1", "--serial")
    output = stdout + stderr

    # Assert
    assert _SENTINEL not in output, (
        f"the serial path has no workers to lose.\noutput:\n{output}"
    )
    assert "1 failed" in output, (
        f"the real failure must be reported.\noutput:\n{output}"
    )
    assert returncode == 2, f"a failing suite exits 2. Got {returncode}.\n{output}"


def test_a_full_parallel_run_reports_no_crash_either(tmp: TempDir) -> None:
    """Control for the workers' health: the same suite with no maxfail."""
    # Arrange: only the passing modules, so the run has no reason to stop.
    for index in range(1, 10):
        (tmp / f"test_m{index}.py").write_text(_passing_module(index), encoding="utf-8")

    # Act
    stdout, stderr, returncode = helpers.run_oxitest(tmp, "-n", "4")
    output = stdout + stderr

    # Assert
    assert _SENTINEL not in output, (
        "no worker dies on this suite, so the sentinel must never appear. This "
        "is the control that separates 'maxfail reports phantom crashes' from "
        f"'this suite kills workers'.\noutput:\n{output}"
    )
    integ.assert_passed(output, returncode, count=54)
