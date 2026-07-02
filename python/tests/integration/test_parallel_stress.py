"""Integration tests: parallel execution stress scenarios.

Covers scale (200+ tests), worker crash handling, maxfail under load,
and shared fixture contention across workers.
"""

import re
from pathlib import Path

import oxitest
from oxitest import TempDir, helpers


def _parse_counts(out: str) -> dict[str, int]:
    """Extract outcome counts from summary line."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"(\d+)\s+(passed|failed|skipped|error)", out):
        counts[m.group(2)] = int(m.group(1))
    return counts


@oxitest.mark.timeout(120)
def test_parallel_matches_serial_at_scale(tmp: TempDir):
    """200+ tests across 10 files produce identical counts in serial and parallel."""
    # Arrange: generate 10 test files with 20 passing tests each (200 total).
    for file_idx in range(10):
        lines = [
            f"def test_f{file_idx}_{test_idx}():\n"
            f"    assert {file_idx} + {test_idx} >= 0\n"
            for test_idx in range(20)
        ]
        (tmp / f"test_scale_{file_idx}.py").write_text("\n".join(lines))

    # Act: run both serial and parallel (--workers forces parallel mode).
    serial_out, _, serial_rc = helpers.common.run_oxitest(tmp, "--serial", timeout=90)
    parallel_out, _, parallel_rc = helpers.common.run_oxitest(
        tmp, "--workers", "4", timeout=90
    )

    # Assert
    serial_counts = _parse_counts(serial_out)
    parallel_counts = _parse_counts(parallel_out)
    assert serial_counts == parallel_counts, (
        "serial and parallel must produce the same outcome counts "
        f"(serial={serial_counts}, parallel={parallel_counts})"
    )
    assert serial_rc == 0, "serial run should exit 0 when all tests pass"
    assert parallel_rc == 0, "parallel run should exit 0 when all tests pass"
    passed = serial_counts.get("passed", 0)
    assert passed == 200, (
        "serial/parallel parity requires all generated tests to pass — "
        f"a non-200 count means broken test generation (got {passed})"
    )


@oxitest.mark.timeout(120)
def test_worker_crash_reports_errors(tmp: TempDir):
    """A worker that crashes via os._exit reports a non-zero exit from oxitest."""
    # Arrange: one test crashes the worker process, others are normal.
    (tmp / "test_crash.py").write_text(
        "import os\n"
        "\n"
        "def test_before_crash():\n"
        "    assert True\n"
        "\n"
        "def test_crashes_worker():\n"
        "    os._exit(1)\n"
    )
    (tmp / "test_healthy.py").write_text(
        "def test_ok_a():\n    assert True\n\ndef test_ok_b():\n    assert True\n"
    )

    # Act: run with multiple workers so the crash doesn't block everything.
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--workers", "2")

    # Assert: the overall run must fail because a worker crashed.
    assert rc != 0, (
        "worker crash must cause non-zero exit so CI catches it "
        f"(got rc={rc})\n{out}{stderr}"
    )


@oxitest.mark.timeout(120)
def test_maxfail_stops_early_under_load(tmp: TempDir):
    """--maxfail 3 with 50 tests (10 failing) stops before running all 50."""
    # Arrange: 50 tests total, 10 of which always fail.
    lines = []
    for i in range(50):
        if i < 10:
            lines.append(
                f"def test_item_{i}():\n    assert False, 'deliberate failure {i}'\n"
            )
        else:
            lines.append(f"def test_item_{i}():\n    assert True\n")
    (tmp / "test_maxfail_load.py").write_text("\n".join(lines))

    # Act: use --workers to force parallel, --maxfail 3 to stop early.
    out, stderr, rc = helpers.common.run_oxitest(
        tmp, "--maxfail", "3", "--workers", "2"
    )

    # Assert: run exits non-zero because failures were detected.
    assert rc != 0, (
        f"run with deliberate failures must exit non-zero (got rc={rc})\n{out}{stderr}"
    )
    # Assert: not all 50 tests ran because maxfail should have stopped early.
    counts = _parse_counts(out)
    total_ran = sum(counts.values())
    assert total_ran < 50, (
        "maxfail prevents wasting CI time on a doomed run — if all 50 tests "
        f"executed, the early-stop logic is broken (ran {total_ran}, counts={counts})"
    )


@oxitest.mark.timeout(120)
def test_shared_fixture_created_once_across_workers(tmp: TempDir):
    """A shared fixture writes a counter file; parallel run creates it exactly once."""
    # Arrange: conftest with a shared fixture that increments a file counter.
    (tmp / "conftest.py").write_text(
        "import os\n"
        "import oxitest as oxi\n"
        "from oxitest import Fixture\n"
        "\n"
        "fx = oxi.Fixtures()\n"
        "\n"
        "COUNTER_FILE = os.path.join(os.path.dirname(__file__), '.fixture_counter')\n"
        "\n"
        "@fx.fixture(shared=True)\n"
        "def shared_resource() -> str:\n"
        "    # Atomically increment a counter file each time the fixture runs.\n"
        "    count = 0\n"
        "    if os.path.exists(COUNTER_FILE):\n"
        "        with open(COUNTER_FILE) as f:\n"
        "            count = int(f.read().strip())\n"
        "    count += 1\n"
        "    with open(COUNTER_FILE, 'w') as f:\n"
        "        f.write(str(count))\n"
        "    return f'resource-{count}'\n"
    )

    # Three test files each depend on the shared fixture.
    for i in range(3):
        (tmp / f"test_shared_{i}.py").write_text(
            "from oxitest import Fixture\n"
            "\n"
            f"def test_uses_shared_{i}(shared_resource: Fixture[str]):\n"
            "    assert shared_resource.startswith('resource-'), (\n"
            "        f'shared fixture should return resource string, '\n"
            "        f'got {{shared_resource!r}}'\n"
            "    )\n"
        )

    # Act: run with workers to exercise parallel fixture sharing.
    out, stderr, rc = helpers.common.run_oxitest(tmp, "--workers", "2")

    # Assert: all tests pass.
    helpers.integ.assert_passed(out, rc, count=3)

    # Assert: the counter file shows the fixture was created exactly once.
    counter_path = Path(str(tmp)) / ".fixture_counter"
    counter_value = int(counter_path.read_text().strip())
    assert counter_value == 1, (
        "shared fixtures must be created once and frozen across workers — "
        "multiple creations would cause state divergence between workers "
        f"(counter={counter_value})"
    )
